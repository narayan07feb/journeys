"""tap-all: walk the app breadth-first, tap every option on each screen once, report what broke.

Each screen found keeps its route (the taps from a fresh launch), so the walker can get back to
it after a crash or a tap that leaves it. Reports crashes, dead ends (back does not return to the
screen before it) and screens the map does not know yet.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from .actions import from_command, from_commands
from .drivers.base import Driver, DriverError, Screen, changes_allowed
from .heal import fingerprint, heading, left_app, same_screen, screen_name, selector, tap_targets
from .pack import Pack
from .results import Run, Store

DKA = "always_finish_activities"


@dataclass
class Found:
    key: str
    screen: Screen
    route: list
    known: bool


@dataclass
class TapAllReport:
    screens: list[dict] = field(default_factory=list)
    crashes: list[dict] = field(default_factory=list)
    dead_ends: list[dict] = field(default_factory=list)
    new_screens: list[str] = field(default_factory=list)
    unreachable: list[str] = field(default_factory=list)
    left_app: list[dict] = field(default_factory=list)


class TapAll:
    def __init__(self, pack: Pack, driver: Driver, store: Store, run: Run, *,
                 max_screens: int = 20, max_taps: int = 15):
        self.pack, self.driver, self.store, self.run = pack, driver, store, run
        self.max_screens, self.max_taps = max_screens, max_taps
        self.found: list[Found] = []
        self.report = TapAllReport()

    def _key(self, screen: Screen) -> tuple[str, bool]:
        name = self.pack.map.identify(screen).name
        if name:
            return name, True
        head = heading(screen)
        for f in self.found:  # a game table or a feed changes every time: the same heading is enough
            if not f.known and (same_screen(f.screen, screen) or (head and heading(f.screen) == head)):
                return f.key, False
        digest = hashlib.sha1(repr(sorted(fingerprint(screen))).encode()).hexdigest()[:4]
        return f"NEW:{screen_name(heading(screen))}:{digest}", False

    def _seen(self, key: str) -> Found | None:
        return next((f for f in self.found if f.key == key), None)

    def _reach(self, f: Found) -> bool:
        res = self.driver.do(["stopApp", "launchApp", *f.route])
        return res.ok and self.driver.app_alive() and self._key(self.driver.look())[0] == f.key

    def walk(self) -> TapAllReport:
        self.driver.do(["stopApp", "launchApp"])
        first = self.driver.look()
        key, known = self._key(first)
        self.found.append(Found(key, first, [], known))
        i = 0
        while i < len(self.found) and i < self.max_screens:
            self._visit(self.found[i])
            i += 1
        self.report.new_screens = [f.key for f in self.found if not f.known]
        self.run.data.update(status="failed" if self.report.crashes else "passed", tap_all=self.report.__dict__)
        self.run.save()
        return self.report

    def _visit(self, here: Found) -> None:
        if not self._reach(here):
            self.report.unreachable.append(here.key)
            return
        base = self.driver.look()
        options = []
        for el in tap_targets(base, self.pack.deny, self.max_taps):
            sel = selector(el, base)
            if sel is None:  # no selector finds only this element: tapping could hit another one
                options.append({"tap": None, "result": "skipped", "note": f"no unique selector for {el.line()}"})
                continue
            tap = {"tapOn": sel}
            res = self.driver.do([tap])
            if not res.ok:
                options.append({"tap": from_command(tap), "result": "failed", "note": res.message[:200]})
                continue
            if not self.driver.app_alive():
                fid = self.store.add_failure({
                    "run": self.run.id, "journey": "tap-all", "step": len(here.route),
                    "step_text": f"{here.key}: {from_command(tap)}", "message": "the app crashed",
                    "screen": here.key, "route": from_commands(here.route + [tap]), "log": self.driver.crash_log(),
                })
                self.report.crashes.append({"screen": here.key, "tap": from_command(tap), "failure": fid})
                options.append({"tap": from_command(tap), "result": "crash", "failure": fid})
                if not self._reach(here):
                    break
                continue
            other = left_app(self.driver, self.pack.owns)
            if other:  # a system screen or another app: never walked; come back
                self.report.left_app.append({"screen": here.key, "tap": from_command(tap), "package": other})
                options.append({"tap": from_command(tap), "result": f"left the app for {other}"})
                if not self._reach(here):
                    break
                continue
            after = self.driver.look()
            if same_screen(base, after):
                options.append({"tap": from_command(tap), "result": "no change"})
                continue
            key, known = self._key(after)
            options.append({"tap": from_command(tap), "result": key})
            if self._seen(key):
                # A screen already found (an in-app Back or Leave to the menu): navigation, not a
                # dead end. Back is tested once per screen, when it is first found.
                if not self._reach(here):
                    break
                continue
            self.found.append(Found(key, after, here.route + [tap], known))
            self.driver.do(["back"])
            if not (self.driver.app_alive() and same_screen(base, self.driver.look())):
                self.report.dead_ends.append({"screen": key, "from": here.key, "tap": from_command(tap)})
                if not self._reach(here):
                    break
        self.report.screens.append({"screen": here.key, "route": from_commands(here.route), "options": options})


def dka_allowed(pack: Pack, driver: Driver, serial: str | None) -> bool:
    return changes_allowed(pack.devices.get("allow_settings_changes", "emulator"), driver, serial)


def tap_all(pack: Pack, driver: Driver, store: Store, run: Run, *, serial: str | None = None,
            dka: bool = False, **limits) -> TapAllReport:
    """With dka, turn on "don't keep activities" for the walk and put the old value back after."""
    if not pack.tap_allowed:
        raise DriverError(f"pack {pack.name} sets tap_all.allowed: false (tapping everything is not safe in this app)")
    old = None
    if dka:
        if not dka_allowed(pack, driver, serial):
            raise DriverError("--dka changes a device setting; pack.yaml devices.allow_settings_changes "
                              "does not allow it on this device")
        old = driver.global_setting(DKA, "1")
    try:
        return TapAll(pack, driver, store, run, **limits).walk()
    finally:
        if dka:
            driver.global_setting(DKA, "0" if old in (None, "", "null") else old)

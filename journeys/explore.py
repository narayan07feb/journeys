"""tap-all: walk the app breadth-first, tap every option on each screen once, report what broke.

Each screen found keeps its route (the taps from a fresh launch), so the walker can get back to
it after a crash or a tap that leaves it. Reports crashes, dead ends (back does not return to the
screen before it) and screens the map does not know yet.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field

from .actions import from_command, from_commands
from .drivers.base import Driver, DriverError, Screen, changes_allowed, id_tail
from .heal import fingerprint, heading, left_app, new_denied, same_screen, screen_name, selector, tap_targets
from .pack import Pack
from .results import Run, Store, mask, write_json

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
        acts = _actions(screen)
        for f in self.found:  # a game table or a feed changes its texts: same heading and same buttons is enough
            if not f.known and (same_screen(f.screen, screen)
                                or (head and heading(f.screen) == head and _actions(f.screen) == acts)):
                return f.key, False
        digest = hashlib.sha1(repr(sorted(fingerprint(screen))).encode()).hexdigest()[:4]
        return f"NEW:{screen_name(mask(heading(screen)))}:{digest}", False  # never an upper-cased secret

    def _seen(self, key: str) -> Found | None:
        return next((f for f in self.found if f.key == key), None)

    def _reach(self, f: Found) -> bool:
        res = self.driver.do(["stopApp", "launchApp", *f.route])
        return res.ok and self.driver.app_alive() and self._key(self.driver.look())[0] == f.key

    def _live(self, status: str, message: str = "") -> None:
        """The live page and Droid Lite panel follow tap-all as they follow a run."""
        write_json(self.store.root / "live.json", {
            "run": self.run.id, "journey": "tap-all", "status": status,
            "step": f"{len(self.report.screens)} screens", "text": "", "screen": None,
            "message": message, "updated": time.time()})

    def walk(self) -> TapAllReport:
        """Any stop (a lost link, Ctrl-C, a bug) ends the run as failed with what was walked so far."""
        self._live("running")
        try:
            res = self.driver.do(["stopApp", "launchApp"])
            if not res.ok:  # an app that does not start is not a clean walk
                raise DriverError(f"tap-all: the app did not start: {res.message}")
            first = self.driver.look()
            key, known = self._key(first)
            self.found.append(Found(key, first, [], known))
            i = 0
            while i < len(self.found) and i < self.max_screens:
                self._visit(self.found[i])
                i += 1
        except BaseException as e:
            self.run.data.update(status="failed", ended=time.time(), error=str(e) or type(e).__name__,
                                 tap_all=self.report.__dict__)
            self.run.save()
            self._live("failed", str(e) or type(e).__name__)
            raise
        self.report.new_screens = [f.key for f in self.found if not f.known]
        if len(self.found) > self.max_screens:
            self.run.data["not_visited"] = [f.key for f in self.found[self.max_screens:]]  # beyond --max-screens
        tapped = any(o.get("tap") and o["result"] not in ("failed", "skipped")
                     for s in self.report.screens for o in s["options"])  # a failed tap tested nothing
        clean = tapped and not (self.report.crashes or self.report.unreachable)  # unreachable is not clean
        self.run.data.update(status="passed" if clean else "failed", ended=time.time(), tap_all=self.report.__dict__,
                             **({} if tapped else {"error": "nothing was tapped: every element is denied or unlabelled"}))
        self.run.save()
        self._live(self.run.data["status"], self.run.data.get("error", ""))
        return self.report

    def _back_to(self, here: Found, options: list) -> bool:
        """Back on `here` for its next option; if not, the rest of its options are untested: say so."""
        if self._reach(here):
            return True
        options.append({"tap": None, "result": "skipped", "note": "could not return here; the rest was not tapped"})
        if here.key not in self.report.unreachable:
            self.report.unreachable.append(here.key)
        return False

    def _visit(self, here: Found) -> None:
        if not self._reach(here):
            self.report.unreachable.append(here.key)
            return
        base = self.driver.look()
        options = []
        more = len(tap_targets(base, self.pack.deny, 10_000)) - self.max_taps
        if more > 0:
            options.append({"tap": None, "result": "skipped", "note": f"{more} more beyond --max-taps"})
        for el in tap_targets(base, self.pack.deny, self.max_taps):
            sel = selector(el, base)
            if sel is None:  # no selector finds only this element: tapping could hit another one
                options.append({"tap": None, "result": "skipped", "note": f"no unique selector for {el.line()}"})
                continue
            tap = {"tapOn": sel}
            res = self.driver.do([tap])
            alive = self.driver.app_alive()
            if not res.ok and alive:  # a failed tap that killed the app is a crash, below
                options.append({"tap": from_command(tap), "result": "failed", "note": res.message[:200]})
                continue
            if not alive:
                fid = self.store.add_failure({
                    "run": self.run.id, "journey": "tap-all", "step": len(here.route),
                    "step_text": f"{here.key}: {from_command(tap)}", "message": "the app crashed",
                    "screen": here.key, "route": from_commands(here.route + [tap]), "log": self.driver.crash_log(),
                })
                self.report.crashes.append({"screen": here.key, "tap": from_command(tap), "failure": fid})
                options.append({"tap": from_command(tap), "result": "crash", "failure": fid})
                if not self._back_to(here, options):
                    break
                continue
            other = left_app(self.driver, self.pack.owns)
            if other:  # a system screen or another app: never walked; come back
                self.report.left_app.append({"screen": here.key, "tap": from_command(tap), "package": other})
                options.append({"tap": from_command(tap), "result": f"left the app for {other}"})
                if not self._back_to(here, options):
                    break
                continue
            after = self.driver.look()
            if same_screen(base, after) and new_denied(base, after, self.pack.deny):  # a dialog over it
                options.append({"tap": from_command(tap), "result": "showed denied words; screen reloaded"})
                if not self._back_to(here, options):
                    break
                continue
            if same_screen(base, after):
                options.append({"tap": from_command(tap), "result": "no change"})
                continue
            key, known = self._key(after)
            options.append({"tap": from_command(tap), "result": key})
            if self._seen(key):
                # A screen already found (an in-app Back or Leave to the menu): navigation, not a
                # dead end. Back is tested once per screen, when it is first found.
                if not self._back_to(here, options):
                    break
                continue
            self.found.append(Found(key, after, here.route + [tap], known))
            self.driver.do(["back"])
            if not self.driver.app_alive():  # back crashed it: a crash, with its log
                fid = self.store.add_failure({
                    "run": self.run.id, "journey": "tap-all", "step": len(here.route) + 1,
                    "step_text": f"{key}: back", "message": "the app crashed on back", "screen": key,
                    "route": from_commands(here.route + [tap, "back"]), "log": self.driver.crash_log(),
                })
                self.report.crashes.append({"screen": key, "tap": "back", "failure": fid})
                if not self._back_to(here, options):
                    break
            elif not same_screen(base, self.driver.look()):
                self.report.dead_ends.append({"screen": key, "from": here.key, "tap": from_command(tap)})
                if not self._back_to(here, options):
                    break
        self.report.screens.append({"screen": here.key, "route": from_commands(here.route), "options": options})


def _actions(screen: Screen) -> list:
    """What a screen offers to tap: a brand header shared by every screen ("MyBank") is not identity."""
    return sorted((e.kind, e.text, id_tail(e.id)) for e in screen.elements if e.kind != "Text")


def dka_allowed(pack: Pack, driver: Driver, serial: str | None) -> bool:
    return changes_allowed(pack.devices.get("allow_settings_changes", "emulator"), driver, serial)


def tap_all(pack: Pack, driver: Driver, store: Store, run: Run, *, serial: str | None = None,
            dka: bool = False, **limits) -> TapAllReport:
    """With dka, turn on "don't keep activities" for the walk and put the old value back after."""
    old = None
    try:
        if not pack.tap_allowed:
            raise DriverError(f"pack {pack.name} sets tap_all.allowed: false (tapping everything is not safe here)")
        if dka:
            if not dka_allowed(pack, driver, serial):
                raise DriverError("--dka changes a device setting; pack.yaml devices.allow_settings_changes "
                                  "does not allow it on this device")
            old = driver.global_setting(DKA, "1")
    except BaseException as e:
        run.data.update(status="failed", ended=time.time(), error=str(e) or type(e).__name__)
        run.save()
        raise
    walker = TapAll(pack, driver, store, run, **limits)
    try:
        return walker.walk()
    finally:
        if dka:
            back = "0" if old in (None, "", "null") else old
            try:
                driver.global_setting(DKA, back)
            except DriverError as e:  # never hides the walk's own error; the run says what to put back
                msg = f"{e}: set {DKA} back to {back} by hand"
                run.data.update(status="failed", dka_restore_error=msg)  # a device left changed is not a pass
                run.save()
                walker._live("failed", msg)

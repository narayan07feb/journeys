"""Drives a device with adb and the phone's own tools only: `uiautomator dump` to read the screen,
`input` to tap, type, swipe and press keys, `am` to start and stop the pack's app.

It installs nothing, writes no device setting and deletes no file. It does not start when another
UI test driver (Maestro, Speedrun, an instrumentation) already runs on the device: two UiAutomation
users on one device break each other.
"""
from __future__ import annotations

import re
import shlex
import time
import xml.etree.ElementTree as ET

from .adb import AdbDriver
from .base import CommandResult, DriverError, Element, Screen, command_name, find

_BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
_KEYS = {"back": 4, "home": 3, "enter": 66, "tab": 61, "backspace": 67, "delete": 67, "search": 84}
_NO_OP = {"waitForAnimationToEnd"}
OTHER_DRIVERS = "maestro|uiautomator|speedrun|instrument"


def _kind(cls: str, clickable: bool, label: str) -> str:
    name = (cls or "").rsplit(".", 1)[-1]
    if name.endswith("EditText"):
        return "Field"
    for kind in ("Button", "Switch", "CheckBox", "Image", "Text"):
        if kind in name:
            return "Button" if clickable and kind in ("Image", "Text") and not label else kind
    return "Button" if clickable else "View"


def parse_dump(xml: str) -> tuple[Screen, list[tuple[int, int]]]:
    """Elements a user sees or acts on, in screen order, and the centre of each (for taps)."""
    start, end = xml.find("<hierarchy"), xml.rfind("</hierarchy>")
    if start < 0 or end < 0:
        raise DriverError(f"uiautomator dump gave no screen: {xml.strip()[-200:]}")
    root = ET.fromstring(xml[start:end + len("</hierarchy>")])
    elements: list[Element] = []
    centres: list[tuple[int, int]] = []
    for n in root.iter("node"):
        a = n.attrib
        label = a.get("text") or a.get("content-desc") or ""
        clickable = a.get("clickable") == "true" or a.get("checkable") == "true"
        rid = a.get("resource-id", "")
        m = _BOUNDS.fullmatch(a.get("bounds", ""))
        if not (label or rid or clickable) or not m:
            continue
        x1, y1, x2, y2 = map(int, m.groups())
        if x2 <= x1 or y2 <= y1:
            continue  # off screen or zero size: nothing a user can see
        state = tuple(s for s in ("checked", "selected", "focused") if a.get(s) == "true")
        if a.get("enabled") == "false":
            state += ("disabled",)
        elements.append(Element(f"@{len(elements) + 1}", _kind(a.get("class", ""), clickable, label),
                                label, rid, state))
        centres.append(((x1 + x2) // 2, (y1 + y2) // 2))
    pkg = root.find("node")
    return Screen(elements, header=f"uiautomator {pkg.get('package', '') if pkg is not None else ''}, "
                                   f"{len(elements)} elements"), centres


class UiAutomatorDriver(AdbDriver):
    def __init__(self, wait_s: float = 5.0, poll_s: float = 0.4, sleep=time.sleep, clock=time.monotonic):
        self.wait_s, self.poll_s, self.sleep, self.clock = wait_s, poll_s, sleep, clock
        self.serial: str | None = None
        self.app = ""
        self._centres: list[tuple[int, int]] = []
        self._size: tuple[int, int] | None = None

    def _sh(self, cmd: str) -> str:
        p = self._adb("shell", cmd)
        if p.returncode != 0:
            raise DriverError(f"adb shell {cmd.split()[0]}: {(p.stderr or p.stdout).decode(errors='replace').strip()[-300:]}")
        return p.stdout.decode(errors="replace")

    # Driver API
    def start(self, app_id: str, serial: str | None = None) -> None:
        self.app, self.serial = app_id, serial
        busy = self._sh(f"ps -A -o NAME 2>/dev/null | grep -iE '{OTHER_DRIVERS}' || true").strip()
        if busy:
            raise DriverError(f"another UI test driver runs on the device ({' '.join(busy.split())}): "
                              "uiautomator would break it, so it does not start")

    def look(self) -> Screen:
        out = self._adb("exec-out", "uiautomator", "dump", "/dev/tty").stdout.decode(errors="replace")
        if "</hierarchy>" not in out:  # some builds cannot dump to a tty: use our own file, then delete it
            f = "/data/local/tmp/journeys-window.xml"
            out = self._adb("exec-out", f"uiautomator dump {f} >/dev/null && cat {f}; rm -f {f}").stdout.decode(
                errors="replace")
        screen, self._centres = parse_dump(out)
        return screen

    def do(self, commands: list) -> CommandResult:
        for cmd in commands:
            try:
                res = self._one(cmd)
            except ValueError as e:
                res = CommandResult(False, str(e))
            if not res.ok:
                return res
        return CommandResult(True)

    def stop(self) -> None:
        pass  # nothing was started on the device

    # commands
    def _wait_for(self, sel, present: bool = True) -> int | None:
        """Poll the screen until the selector is there (or gone); the index, or None."""
        end = self.clock() + self.wait_s
        while True:
            i = find(self.look().elements, sel)
            if (i is not None) == present or self.clock() >= end:
                return i
            self.sleep(self.poll_s)

    def _screen_size(self) -> tuple[int, int]:
        if not self._size:
            m = re.search(r"(\d+)x(\d+)", self._sh("wm size").splitlines()[-1])
            self._size = (int(m.group(1)), int(m.group(2))) if m else (1080, 1920)
        return self._size

    def _swipe(self, direction: str) -> None:
        w, h = self._screen_size()
        x, top, bottom = w // 2, h * 3 // 10, h * 7 // 10
        y1, y2 = (bottom, top) if direction.upper() == "DOWN" else (top, bottom)
        self._sh(f"input swipe {x} {y1} {x} {y2} 300")

    def _launch(self, arg) -> CommandResult:
        opts = arg if isinstance(arg, dict) else {}
        app = str(opts.get("appId", self.app))
        if app != self.app:
            return CommandResult(False, f"launchApp {app}: this driver only starts the pack's app {self.app}")
        if opts.get("stopApp", True):
            self._sh(f"am force-stop {app}")
        if opts.get("clearState"):
            self._sh(f"pm clear {app}")
        comp = self._sh(f"cmd package resolve-activity --brief -c android.intent.category.LAUNCHER {app}")
        comp = comp.strip().splitlines()[-1] if comp.strip() else ""
        if "/" not in comp:
            return CommandResult(False, f"launchApp: no launcher activity for {app}")
        self._sh(f"am start -W -n {comp}")
        end = self.clock() + self.wait_s
        while self.foreground_app() != app:
            if self.clock() >= end:
                return CommandResult(False, f"launchApp: {app} is not on screen after {self.wait_s:.0f} s")
            self.sleep(self.poll_s)
        return CommandResult(True)

    def _one(self, cmd) -> CommandResult:
        name, arg = command_name(cmd)
        if name == "launchApp":
            return self._launch(arg)
        if name == "stopApp":
            if arg not in (None, self.app):
                return CommandResult(False, f"stopApp {arg}: this driver only stops the pack's app {self.app}")
            self._sh(f"am force-stop {self.app}")
        elif name in ("back", "pressKey"):
            key = "back" if name == "back" else str(arg).lower()
            if key not in _KEYS:
                return CommandResult(False, f"pressKey {arg}: not one of {', '.join(_KEYS)}")
            self._sh(f"input keyevent {_KEYS[key]}")
        elif name == "tapOn":
            i = self._wait_for(arg)
            if i is None:
                return CommandResult(False, f"Element not found: {arg}")
            x, y = self._centres[i]
            self._sh(f"input tap {x} {y}")
        elif name == "inputText":
            text = str(arg).replace(" ", "%s")  # `input text` reads %s as a space
            self._sh(f"input text {shlex.quote(text)}")
        elif name == "eraseText":
            n = int(arg if not isinstance(arg, dict) else arg.get("charactersToErase", 50)) if arg else 50
            self._sh("input keyevent " + " ".join(["67"] * n))
        elif name == "hideKeyboard":
            if "mInputShown=true" in self._sh("dumpsys input_method"):
                self._sh("input keyevent 4")
        elif name == "assertVisible":
            if self._wait_for(arg) is None:
                return CommandResult(False, f"Element not found: {arg}")
        elif name == "assertNotVisible":
            if self._wait_for(arg, present=False) is not None:
                return CommandResult(False, f"Element is visible: {arg}")
        elif name == "scroll":
            self._swipe("DOWN")
        elif name == "scrollUntilVisible":
            opts = arg if isinstance(arg, dict) else {"element": arg}
            for _ in range(int(opts.get("maxScrolls", 10)) + 1):
                before = self.look()
                if find(before.elements, opts["element"]) is not None:
                    return CommandResult(True)
                self._swipe(str(opts.get("direction", "DOWN")))
                if self.look().listing() == before.listing():
                    break  # the end of the list: nothing moved
            return CommandResult(False, f"Element not found after scrolling: {opts['element']}")
        elif name not in _NO_OP:
            return CommandResult(False, f"uiautomator driver does not know {name}")
        return CommandResult(True)

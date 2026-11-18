"""Drives a device with adb and the phone's own tools only: `uiautomator dump` to read the screen,
`input` to tap, type, swipe and press keys, `am` to start and stop the pack's app.

- It installs nothing and writes no device setting. The only file it writes and deletes is its own
  /data/local/tmp/journeys-window.xml, on builds that cannot dump to a tty.
- It stops and starts only the pack's app; `launch` with clear_state runs `pm clear` on that app.
- It sends input (taps, keys, text, swipes) only while the pack's app, its `app_packages`, or
  Android's permission-grant dialog (for any app) has the focus, checked right before each send.
- It types printable ASCII only.
- It does not start when another UI test driver process (Maestro, Speedrun, a test or orchestrator
  process) runs on the device: two UiAutomation users break each other. An instrumentation that
  runs inside the app's own process is not visible to this check.
"""
from __future__ import annotations

import re
import shlex
import time
import xml.etree.ElementTree as ET

from .adb import AdbDriver
from .base import CommandResult, DriverError, Element, Screen, command_name, find, focused_window, kind_of

_BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
_KEYS = {"back": 4, "home": 3, "enter": 66, "tab": 61, "backspace": 67, "delete": 67, "search": 84}
_NO_OP = {"waitForAnimationToEnd"}
PERMISSION_DIALOG = re.compile(r"(com\.android|com\.google\.android)\.permissioncontroller/\S*GrantPermissionsActivity")
OTHER_DRIVERS = r"maestro|uiautomator|speedrun|instrument|orchestrator|\.test$"
_PERMISSION_PKGS = ("com.android.permissioncontroller", "com.google.android.permissioncontroller")


_CHAR_REF = re.compile(r"&#(x[0-9a-fA-F]+|[0-9]+);")


def _bad_char(ref: str) -> bool:
    """Control characters (&#1;) that apps put in texts are not XML 1.0: drop them before parsing."""
    n = int(ref[1:], 16) if ref.startswith("x") else int(ref)
    return (n < 32 and n not in (9, 10, 13)) or 0xD800 <= n <= 0xDFFF  # surrogate halves are not XML either


def parse_dump(xml: str, keep: tuple[str, ...] | None = None) -> tuple[Screen, list[tuple[int, int]]]:
    """Elements a user sees or acts on, in screen order, and the centre of each (for taps).
    With keep, only nodes of those packages (another app's texts must not pass for ours)."""
    start, end = xml.find("<hierarchy"), xml.rfind("</hierarchy>")
    if start < 0 or end < 0:
        raise DriverError(f"uiautomator dump gave no screen: {xml.strip()[-200:]}")
    body = _CHAR_REF.sub(lambda m: "" if _bad_char(m.group(1)) else m.group(0), xml[start:end + 12])
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        raise DriverError(f"uiautomator dump is not valid XML: {e}") from None
    elements: list[Element] = []
    centres: list[tuple[int, int]] = []
    for n in root.iter("node"):
        a = n.attrib
        if keep and a.get("package") and a["package"] not in keep:
            continue
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
        elements.append(Element(f"@{len(elements) + 1}", kind_of(a.get("class", ""), clickable, label),
                                label, rid, state))
        centres.append(((x1 + x2) // 2, (y1 + y2) // 2))
    pkg = root.find("node")
    return Screen(elements, header=f"uiautomator {pkg.get('package', '') if pkg is not None else ''}, "
                                   f"{len(elements)} elements"), centres


class UiAutomatorDriver(AdbDriver):
    def __init__(self, wait_s: float = 5.0, poll_s: float = 0.4, sleep=time.sleep, clock=time.monotonic,
                 app_packages: tuple[str, ...] = ()):
        self.wait_s, self.poll_s, self.sleep, self.clock = wait_s, poll_s, sleep, clock
        self.app_packages = tuple(app_packages)  # other packages the pack's app shows screens in
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
        if not re.fullmatch(r"[A-Za-z][\w.]*", app_id):
            raise DriverError(f"{app_id!r} is not an Android package name")
        self.app, self.serial = app_id, serial
        p = self._adb("shell", "ps -A -o NAME")  # read-only; fails closed: no list, no start
        names = p.stdout.decode(errors="replace").split() if p.returncode == 0 else []
        if len(names) < 2:
            raise DriverError("could not list the device's processes, so cannot tell if another UI test "
                              "driver runs there: not starting")
        busy = [n for n in names if re.search(OTHER_DRIVERS, n, re.I) and n.split(":")[0] != app_id]
        if busy:
            raise DriverError(f"another UI test driver runs on the device ({' '.join(busy)}): "
                              "uiautomator would break it, so it does not start")

    def look(self) -> Screen:
        p = self._adb("exec-out", "uiautomator", "dump", "/dev/tty")
        out = p.stdout.decode(errors="replace")
        if p.returncode != 0 and not out.strip():
            raise DriverError(f"uiautomator dump: {p.stderr.decode(errors='replace').strip()[-300:] or 'no output'}")
        if "</hierarchy>" not in out:  # some builds cannot dump to a tty: use our own file, then delete it
            f = "/data/local/tmp/journeys-window.xml"
            out = self._adb("exec-out", f"uiautomator dump {f} >/dev/null && cat {f}; rm -f {f}").stdout.decode(
                errors="replace")
        screen, self._centres = parse_dump(out, keep=(self.app, *self.app_packages, *_PERMISSION_PKGS) if self.app else None)
        m = re.search(r'bounds="\[0,0\]\[(\d+),(\d+)\]"', out)  # the window, as rotated now
        self._size = (int(m.group(1)), int(m.group(2))) if m else self._size
        return screen

    def do(self, commands: list) -> CommandResult:
        for cmd in commands:
            try:
                res = self._one(cmd)
            except (ValueError, TypeError, KeyError) as e:
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
            try:
                i = find(self.look().elements, sel)
            except DriverError:
                if self.clock() >= end:
                    raise
                i = None if present else 0  # a bad dump: not an answer, poll again
            else:
                if (i is not None) == present or self.clock() >= end:
                    return i
            self.sleep(self.poll_s)

    def _window(self) -> str:
        """The focused window ("pkg/Activity"), read-only. Focus is briefly empty while an activity
        changes, so wait up to wait_s for a real one. No focus at all (screen off, locking) is never
        the app: input waits, then is refused."""
        end = self.clock() + self.wait_s
        while True:
            out = self._adb("shell", "dumpsys", "window").stdout.decode(errors="replace")
            first = re.search(r"mCurrentFocus=(\S+)", out)  # the same first line focused_window reads
            w = None if first and first.group(1) == "null" else focused_window(out)
            if w or self.clock() >= end:
                return w or ""
            self.sleep(self.poll_s)

    def _ours(self, window: str) -> bool:
        if not window:
            return False  # no focus (an empty app_packages entry must not match it)
        return window.split("/")[0] in (self.app, *self.app_packages) or bool(PERMISSION_DIALOG.fullmatch(window))

    def _input(self, cmd: str, what: str) -> CommandResult | None:
        """Send one `input ...` command, but only while the pack's app (or Android's permission-grant
        dialog) has the focus: checked right before each send, never to another app."""
        window = self._window()
        if not self._ours(window):
            return CommandResult(False, f"{what}: the focused window is {window or 'unknown'}, not {self.app}; "
                                        "this driver sends input only to the pack's app")
        self._sh(cmd)
        return None

    def _swipe(self, direction: str) -> CommandResult | None:
        w, h = self._size or (1080, 1920)
        x, top, bottom = w // 2, h * 3 // 10, h * 7 // 10
        y1, y2 = (bottom, top) if direction.upper() == "DOWN" else (top, bottom)
        return self._input(f"input swipe {x} {y1} {x} {y2} 300", "scroll")

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
        self._sh(f"am start -W -n {shlex.quote(comp)}")
        if not self._ours(self._window()):
            return CommandResult(False, f"launchApp: {app} is not on screen after {self.wait_s:.0f} s")
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
            return self._input(f"input keyevent {_KEYS[key]}", name) or CommandResult(True)
        elif name == "tapOn":
            i = self._wait_for(arg)
            if i is None:
                return CommandResult(False, f"Element not found: {arg}")
            w, h = self._size or (10 ** 5, 10 ** 5)
            x, y = self._centres[i]
            # a row cut by the screen edge: tap its visible part
            return self._input(f"input tap {min(max(x, 1), w - 1)} {min(max(y, 1), h - 1)}", "tapOn") \
                or CommandResult(True)
        elif name == "inputText":
            text = str(arg)
            if not (text.isascii() and text.isprintable()) or "%s" in text:
                return CommandResult(False, f"inputText ({len(text)} characters, not shown): adb `input text` types printable ASCII only "
                                            "(no '%s', no line breaks); use another driver for this text")
            # `input text` reads %s as a space
            return self._input(f"input text {shlex.quote(text.replace(' ', '%s'))}", name) or CommandResult(True)
        elif name == "eraseText":
            n = int(arg if not isinstance(arg, dict) else arg.get("charactersToErase", 50)) if arg else 50
            if not 1 <= n <= 1000:
                return CommandResult(False, f"eraseText {n}: erase 1 to 1000 characters")
            # MOVE_END first, then DEL: the field's last characters
            return self._input("input keyevent 123 " + " ".join(["67"] * n), name) or CommandResult(True)
        elif name == "hideKeyboard":
            if "mInputShown=true" in self._sh("dumpsys input_method"):
                return self._input("input keyevent 4", name) or CommandResult(True)
        elif name in ("assertVisible", "assertNotVisible"):
            if not self._ours(self._window()):  # another app on screen proves nothing about ours
                return CommandResult(False, f"{name} {arg}: {self.app} is not on screen")
            if name == "assertVisible" and self._wait_for(arg) is None:
                return CommandResult(False, f"Element not found: {arg}")
            if name == "assertNotVisible" and self._wait_for(arg, present=False) is not None:
                return CommandResult(False, f"Element is visible: {arg}")
        elif name == "scroll":
            self.look()  # the screen size comes from the dump
            return self._swipe("DOWN") or CommandResult(True)
        elif name == "scrollUntilVisible":
            opts = arg if isinstance(arg, dict) else {"element": arg}
            before = self.look()
            for _ in range(int(opts.get("maxScrolls", 10))):
                if find(before.elements, opts["element"]) is not None:
                    return CommandResult(True)
                refused = self._swipe(str(opts.get("direction", "DOWN")))
                if refused:
                    return refused
                after = self.look()
                if after.listing() == before.listing():
                    break  # the end of the list: nothing moved
                before = after
            if find(before.elements, opts["element"]) is not None:
                return CommandResult(True)
            return CommandResult(False, f"Element not found after scrolling: {opts['element']}")
        elif name not in _NO_OP:
            return CommandResult(False, f"uiautomator driver does not know {name}")
        return CommandResult(True)

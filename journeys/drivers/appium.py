"""The Appium driver, for teams that use Appium: Journeys talks W3C WebDriver to the team's own Appium
server (UiAutomator2), on the host. It reads the screen from the page source and finds elements with
the same selector rules as every other driver, then taps by the element's bounds.

The server is the team's: JOURNEYS_APPIUM_URL (default http://127.0.0.1:4723). Journeys never starts,
installs or updates Appium. Appium's UiAutomator2 driver sets up its own helper apps on the device at
session start, so this driver starts only on a device pack.yaml allows to change
(devices.allow_settings_changes).
"""
from __future__ import annotations

import base64
import http.client
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path

from .adb import AdbDriver
from .base import CommandResult, DriverError, Element, Screen, changes_allowed, command_name, find, kind_of, shown

SCROLL_S = 20  # seconds scroll_to looks for its element
START_S = 300  # seconds a session start may take (Appium may install its helpers first)
IDLE_S = 900  # seconds Appium keeps an idle session; above the runner's 300 s hook limit
KEYCODES = {"enter": 66, "back": 4, "home": 3, "tab": 61, "backspace": 67, "search": 84}
_W3C_ELEMENT = "element-6066-11e4-a52e-4f735466cecf"
_BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


def _opener(url: str):
    """A local server is reached directly, never through an http_proxy the shell happens to set."""
    host = urllib.parse.urlsplit(url).hostname or ""
    local = host in ("127.0.0.1", "localhost", "::1")
    return urllib.request.build_opener(urllib.request.ProxyHandler({})) if local else urllib.request.build_opener()


def server_url() -> str:
    return os.environ.get("JOURNEYS_APPIUM_URL", "http://127.0.0.1:4723").rstrip("/")


def reachable(url: str | None = None, timeout: float = 2.0) -> str | None:
    """None when an Appium server answers at the URL, else why not (for doctor)."""
    url = url or server_url()
    try:
        with _opener(url).open(f"{url}/status", timeout=timeout) as r:
            json.loads(r.read() or b"{}")
        return None
    except (OSError, ValueError, http.client.HTTPException) as e:
        return f"no Appium server at {url} ({e}): start the team's Appium, or set JOURNEYS_APPIUM_URL"


def parse_source(xml) -> tuple[Screen, list[tuple[int, int, int, int]]]:
    """Appium's page source -> the elements a user can see or act on, in order, and each one's bounds."""
    if not isinstance(xml, str):
        raise DriverError(f"appium source: not XML text but {type(xml).__name__}")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        raise DriverError(f"appium source: unreadable XML ({e})") from None
    out: list[Element] = []
    boxes: list[tuple[int, int, int, int]] = []
    for node in root.iter():
        a = node.attrib
        m = _BOUNDS.fullmatch(a.get("bounds", ""))
        text = a.get("text") or a.get("content-desc") or a.get("hint") or ""
        rid = (a.get("resource-id") or "").rsplit("/", 1)[-1]  # the bare name, as the other drivers report it
        clickable = a.get("clickable") == "true"
        if not m or not (text or rid or clickable):
            continue
        state = tuple(s for s in ("checked", "selected", "focused") if a.get(s) == "true")
        if a.get("enabled") == "false":
            state += ("disabled",)
        out.append(Element(f"@{len(out) + 1}", kind_of(a.get("class", node.tag), clickable, text), text, rid, state))
        boxes.append(tuple(map(int, m.groups())))
    return Screen(out, header=f"{len(out)} elements"), boxes


class AppiumError(DriverError):
    """Appium answered with a WebDriver error: `error` is the W3C code ("no such element", ...)."""

    def __init__(self, message: str, error: str = "", detail: str = ""):
        super().__init__(message)
        self.error, self.detail = error, detail  # the W3C code, and the server's own message

    @property
    def fatal(self) -> bool:
        """The session is gone: nothing later in the run can work."""
        return self.error in ("invalid session id", "no such window") or "invalid session" in str(self)


class AppiumDriver(AdbDriver):
    def __init__(self, url: str | None = None, timeout_s: int = 60, allow_changes="emulator", wait_s: float = 5.0,
                 scroll_s: float = SCROLL_S):
        self.url, self.timeout, self.allow_changes, self.wait = url or server_url(), timeout_s, allow_changes, wait_s
        self.scroll_s = scroll_s
        self.serial, self.app, self.session = None, "", None

    # the wire
    def _http(self, method: str, path: str, body: dict | None = None) -> dict:
        try:
            req = urllib.request.Request(f"{self.url}{path}", method=method,
                                         data=None if body is None else json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            with _opener(self.url).open(req, timeout=self.timeout) as r:
                body = json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:  # WebDriver errors come back as JSON with value.message
            try:
                err = json.loads(e.read() or b"{}")
            except (ValueError, OSError, http.client.HTTPException):
                err = None
            value = err.get("value") if isinstance(err, dict) else None
            msg = str(value.get("message", "")) if isinstance(value, dict) else ""
            code = str(value.get("error", "")) if isinstance(value, dict) else ""
            raise AppiumError(f"appium {method} {path}: {e.code} {code + ': ' if code else ''}{msg[:300]}",
                              code, msg) from None
        except (OSError, ValueError, http.client.HTTPException) as e:
            raise DriverError(f"appium {method} {path}: {e}") from None
        if not isinstance(body, dict):
            raise DriverError(f"appium {method} {path}: not a WebDriver reply ({str(body)[:100]})")
        return body

    def _s(self, method: str, path: str, body: dict | None = None):
        if not self.session:
            raise DriverError("no Appium session: start() first")
        return self._http(method, f"/session/{self.session}{path}", body).get("value")

    def _mobile(self, script: str, **args):
        return self._s("POST", "/execute/sync", {"script": f"mobile: {script}", "args": [args]})

    # the Driver contract
    def start(self, app_id: str, serial: str | None = None) -> None:
        self.app, self.serial = app_id, serial
        if not changes_allowed(self.allow_changes, self, serial):
            raise DriverError("an Appium session changes the device: it installs its helper apps, sets the "
                              "hidden API policy (Android 9+), on a phone allows mock location for its settings "
                              "app (left on) and unlocks it; pack.yaml devices.allow_settings_changes does not "
                              "allow that on this device")
        caps = {"platformName": "Android", "appium:automationName": "UiAutomator2", "appium:noReset": True,
                "appium:appPackage": app_id, "appium:autoLaunch": False,
                # a hook step may run for 300 s with no Appium call: the session must outlive it
                "appium:newCommandTimeout": IDLE_S}
        if serial:
            caps["appium:udid"] = serial
        quick, self.timeout = self.timeout, max(self.timeout, START_S)  # a first session sets up helper apps
        try:
            value = self._http("POST", "/session", {"capabilities": {"alwaysMatch": caps}}).get("value")
        finally:
            self.timeout = quick
        self.session = value.get("sessionId") if isinstance(value, dict) else None
        if not self.session:
            raise DriverError(f"appium did not start a session: {str(value)[:300]}")

    def stop(self) -> None:
        if self.session:
            try:
                self._s("DELETE", "")
            finally:
                self.session = None

    def _read(self) -> tuple[Screen, list[tuple[int, int]], list[bool]]:
        """The screen as the user sees it: elements entirely outside the window are left out (so a
        hidden duplicate never hides the visible one, and @N counts what is on screen), each with the
        point to tap (the centre of its part inside the window) and whether all of it is inside."""
        screen, boxes = parse_source(self._s("GET", "/source") or "")
        w, h = self._window()
        keep = []
        for e, (x1, y1, x2, y2) in zip(screen.elements, boxes):
            cx1, cy1, cx2, cy2 = max(x1, 0), max(y1, 0), min(x2, w), min(y2, h)
            if cx1 < cx2 and cy1 < cy2:
                keep.append((e, ((cx1 + cx2) // 2, (cy1 + cy2) // 2), (cx1, cy1, cx2, cy2) == (x1, y1, x2, y2)))
        els = [replace(e, ref=f"@{i}") for i, (e, _, _) in enumerate(keep, 1)]
        return Screen(els, header=f"{len(els)} elements"), [c for _, c, _ in keep], [f for _, _, f in keep]

    def look(self) -> Screen:
        return self._read()[0]

    def screenshot(self, path: Path) -> Path | None:
        try:
            data = base64.b64decode(self._s("GET", "/screenshot") or "")
        except (DriverError, ValueError, TypeError):
            return None
        if not data.startswith(b"\x89PNG"):
            return None
        path.write_bytes(data)
        return path

    def _tap(self, x: int, y: int) -> None:
        self._pointer([(x, y)])

    def _pointer(self, points: list[tuple[int, int]]) -> None:
        """A finger: down at the first point, through the rest, up. One point is a tap."""
        acts = [{"type": "pointerMove", "duration": 0, "x": points[0][0], "y": points[0][1]},
                {"type": "pointerDown", "button": 0}]
        acts += [{"type": "pointerMove", "duration": 600, "x": x, "y": y} for x, y in points[1:]]  # a drag, no fling
        acts += [{"type": "pointerUp", "button": 0}]
        self._s("POST", "/actions", {"actions": [{"type": "pointer", "id": "finger", "parameters":
                                                  {"pointerType": "touch"}, "actions": acts}]})

    def _window(self) -> tuple[int, int]:
        r = self._s("GET", "/window/rect")
        try:
            return int(r["width"]), int(r["height"])
        except (TypeError, KeyError, ValueError):
            raise DriverError(f"appium window size: not a width and height ({str(r)[:100]})") from None

    def _swipe(self, up: bool) -> None:
        w, h = self._window()
        a, b = (int(h * 0.7), int(h * 0.3)) if up else (int(h * 0.3), int(h * 0.7))
        self._pointer([(w // 2, a), (w // 2, b)])

    def _find(self, sel, wait: float | None = None, whole: bool = False) -> tuple[int, int] | None:
        """Where to tap the element the selector names (the centre of its part on the screen),
        looking again for a short while. whole: only when all of it is on the screen."""
        end = time.monotonic() + (self.wait if wait is None else wait)
        while True:
            try:
                screen, centres, full = self._read()
            except AppiumError as e:  # a dump during an animation can fail once: look again
                if e.fatal or time.monotonic() >= end:
                    raise
                time.sleep(0.5)
                continue
            i = find(screen.elements, sel)
            if i is not None or time.monotonic() >= end:
                break
            time.sleep(0.5)
        if i is None or (whole and not full[i]):
            return None
        return centres[i]

    def do(self, commands: list) -> CommandResult:
        for cmd in commands:
            res = self._one(cmd)
            if not res.ok:
                return res
        return CommandResult(True)

    def _one(self, cmd) -> CommandResult:
        name, arg = command_name(cmd)
        if name == "launchApp":  # a fresh start, as in the other drivers: activateApp alone only brings it up
            self._mobile("terminateApp", appId=self.app)
            if isinstance(arg, dict) and arg.get("clearState"):
                self._mobile("clearApp", appId=self.app)
            self._mobile("activateApp", appId=self.app)
            end = time.monotonic() + self.wait  # activateApp returns before the first screen is drawn
            while self.foreground_app() != self.app and time.monotonic() < end:
                time.sleep(0.3)
        elif name == "stopApp":
            self._mobile("terminateApp", appId=self.app)
        elif name == "back":
            self._s("POST", "/back", {})
        elif name == "pressKey":
            code = KEYCODES.get(str(arg).lower())
            if code is None:
                return CommandResult(False, f"appium: no key {arg}")
            self._mobile("pressKey", keycode=code)
        elif name == "tapOn":
            at = self._find(arg)
            if at is None:
                return CommandResult(False, f"Element not found: {shown(arg)}")
            self._tap(*at)
        elif name == "inputText":
            try:
                active = self._s("GET", "/element/active")
                el = (active.get(_W3C_ELEMENT) or active.get("ELEMENT")) if isinstance(active, dict) else None
                if el:
                    self._s("POST", f"/element/{el}/value", {"text": str(arg)})
            except AppiumError as e:
                if e.error not in ("no such element", "stale element reference"):
                    raise  # an expired session or a failed type is that, not "no focus"
                el = None  # nothing focused, or it went stale: nothing to type into
            if not el:
                return CommandResult(False, "appium: no field has the focus to type into")
        elif name == "eraseText":
            try:
                n = int(arg if arg is not None else 50)
            except (TypeError, ValueError):
                return CommandResult(False, f"appium: erase takes a number of characters, not {arg!r}")
            for _ in range(n):
                self._mobile("pressKey", keycode=KEYCODES["backspace"])
        elif name == "hideKeyboard":
            try:
                try:
                    self._s("POST", "/appium/device/hide_keyboard", {})
                except AppiumError as old:  # a newer Appium may serve only the mobile: command
                    if old.error not in ("unknown command", "unknown method"):
                        raise
                    self._mobile("hideKeyboard")
            except AppiumError as e:  # an older Appium says no keyboard is up: nothing to hide
                d = e.detail.lower()  # the server's words, not the route's
                if e.fatal or "keyboard" not in d or "cannot be hidden" in d:  # appium-adb: still shown
                    raise  # a dead session, a keyboard that stays up, or any other error still fails
        elif name == "scroll":
            self._swipe(up=True)
        elif name == "scrollUntilVisible":
            opts = arg if isinstance(arg, dict) and "element" in arg else {"element": arg}
            up = str(opts.get("direction", "DOWN")).upper() != "UP"  # finger moves up to see what is below
            end = time.monotonic() + self.scroll_s  # as long as Maestro looks, not a fixed number of swipes
            while time.monotonic() < end:
                if self._find(opts["element"], wait=0, whole=True) is not None:  # all of it, as in Maestro
                    return CommandResult(True)
                self._swipe(up=up)
                time.sleep(0.3)  # let the list settle before reading it
            if self._find(opts["element"], wait=0, whole=True) is None:
                return CommandResult(False, f"Element not found: {shown(opts['element'])}")
        elif name == "waitForAnimationToEnd":  # until two reads of the screen agree, at most a few seconds
            end, last = time.monotonic() + 3, None
            while time.monotonic() < end:
                now = self._s("GET", "/source")
                if now == last:
                    break
                last = now
                time.sleep(0.3)
        elif name == "assertVisible":
            if self._find(arg) is None:
                return CommandResult(False, f"Element not found: {shown(arg)}")
        elif name == "assertNotVisible":  # waits a short while for it to go, as for assertVisible to come
            end = time.monotonic() + self.wait
            while self._find(arg, wait=0) is not None:
                if time.monotonic() >= end:
                    return CommandResult(False, f"Element is visible: {shown(arg)}")
                time.sleep(0.5)
        else:
            return CommandResult(False, f"this driver does not know the action {name}")
        return CommandResult(True)

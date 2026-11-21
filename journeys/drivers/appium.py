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
import json
import os
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from .adb import AdbDriver
from .base import CommandResult, DriverError, Element, Screen, changes_allowed, command_name, find, kind_of, shown

KEYCODES = {"Enter": 66, "Back": 4, "Home": 3, "Tab": 61, "Backspace": 67, "Search": 84}
_W3C_ELEMENT = "element-6066-11e4-a52e-4f735466cecf"
_BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


def server_url() -> str:
    return os.environ.get("JOURNEYS_APPIUM_URL", "http://127.0.0.1:4723").rstrip("/")


def reachable(url: str | None = None, timeout: float = 2.0) -> str | None:
    """None when an Appium server answers at the URL, else why not (for doctor)."""
    url = url or server_url()
    try:
        with urllib.request.urlopen(f"{url}/status", timeout=timeout) as r:
            json.loads(r.read() or b"{}")
        return None
    except (OSError, ValueError) as e:
        return f"no Appium server at {url} ({e}): start the team's Appium, or set JOURNEYS_APPIUM_URL"


def parse_source(xml: str) -> tuple[Screen, list[tuple[int, int]]]:
    """Appium's page source -> the elements a user can see or act on, in order, and each one's centre."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        raise DriverError(f"appium source: unreadable XML ({e})") from None
    out: list[Element] = []
    centres: list[tuple[int, int]] = []
    for node in root.iter():
        a = node.attrib
        text = a.get("text") or a.get("content-desc") or ""
        rid = a.get("resource-id") or ""
        clickable = a.get("clickable") == "true"
        m = _BOUNDS.fullmatch(a.get("bounds", ""))
        if not m or not (text or rid or clickable):
            continue
        x1, y1, x2, y2 = map(int, m.groups())
        state = tuple(s for s in ("checked", "selected", "focused") if a.get(s) == "true")
        if a.get("enabled") == "false":
            state += ("disabled",)
        out.append(Element(f"@{len(out) + 1}", kind_of(a.get("class", node.tag), clickable, text), text, rid, state))
        centres.append(((x1 + x2) // 2, (y1 + y2) // 2))
    return Screen(out, header=f"{len(out)} elements"), centres


class AppiumDriver(AdbDriver):
    def __init__(self, url: str | None = None, timeout_s: int = 60, allow_changes="emulator", wait_s: float = 5.0):
        self.url, self.timeout, self.allow_changes, self.wait = url or server_url(), timeout_s, allow_changes, wait_s
        self.serial, self.app, self.session = None, "", None

    # the wire
    def _http(self, method: str, path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(f"{self.url}{path}", method=method,
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:  # WebDriver errors come back as JSON with value.message
            try:
                msg = json.loads(e.read() or b"{}").get("value", {}).get("message", "")
            except ValueError:
                msg = ""
            raise DriverError(f"appium {method} {path}: {e.code} {msg[:300]}") from None
        except (OSError, ValueError) as e:
            raise DriverError(f"appium {method} {path}: {e}") from None

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
            raise DriverError("Appium sets up its helper apps on the device at session start; pack.yaml "
                              "devices.allow_settings_changes does not allow that on this device")
        caps = {"platformName": "Android", "appium:automationName": "UiAutomator2", "appium:noReset": True,
                "appium:appPackage": app_id, "appium:autoLaunch": False}
        if serial:
            caps["appium:udid"] = serial
        value = self._http("POST", "/session", {"capabilities": {"alwaysMatch": caps}}).get("value") or {}
        self.session = value.get("sessionId")
        if not self.session:
            raise DriverError(f"appium did not start a session: {str(value)[:300]}")

    def stop(self) -> None:
        if self.session:
            try:
                self._s("DELETE", "")
            finally:
                self.session = None

    def _read(self) -> tuple[Screen, list[tuple[int, int]]]:
        return parse_source(self._s("GET", "/source") or "")

    def look(self) -> Screen:
        return self._read()[0]

    def screenshot(self, path: Path) -> Path | None:
        try:
            data = base64.b64decode(self._s("GET", "/screenshot") or "")
        except (DriverError, ValueError):
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
        acts += [{"type": "pointerMove", "duration": 300, "x": x, "y": y} for x, y in points[1:]]
        acts += [{"type": "pointerUp", "button": 0}]
        self._s("POST", "/actions", {"actions": [{"type": "pointer", "id": "finger", "parameters":
                                                  {"pointerType": "touch"}, "actions": acts}]})

    def _swipe(self, up: bool) -> None:
        r = self._s("GET", "/window/rect") or {}
        w, h = int(r.get("width", 1080)), int(r.get("height", 2400))
        a, b = (int(h * 0.7), int(h * 0.3)) if up else (int(h * 0.3), int(h * 0.7))
        self._pointer([(w // 2, a), (w // 2, b)])

    def _find(self, sel, wait: float | None = None) -> tuple[int, int] | None:
        """The centre of the element the selector names, looking again for a short while."""
        end = time.monotonic() + (self.wait if wait is None else wait)
        while True:
            screen, centres = self._read()
            i = find(screen.elements, sel)
            if i is not None or time.monotonic() >= end:
                return None if i is None else centres[i]
            time.sleep(0.5)

    def do(self, commands: list) -> CommandResult:
        for cmd in commands:
            res = self._one(cmd)
            if not res.ok:
                return res
        return CommandResult(True)

    def _one(self, cmd) -> CommandResult:
        name, arg = command_name(cmd)
        if name == "launchApp":
            if isinstance(arg, dict) and arg.get("clearState"):
                self._mobile("clearApp", appId=self.app)
            self._mobile("activateApp", appId=self.app)
        elif name == "stopApp":
            self._mobile("terminateApp", appId=self.app)
        elif name == "back":
            self._s("POST", "/back", {})
        elif name == "pressKey":
            code = KEYCODES.get(str(arg))
            if code is None:
                return CommandResult(False, f"appium: no key {arg}")
            self._mobile("pressKey", keycode=code)
        elif name == "tapOn":
            at = self._find(arg)
            if at is None:
                return CommandResult(False, f"Element not found: {shown(arg)}")
            self._tap(*at)
        elif name == "inputText":
            el = (self._s("POST", "/element/active", {}) or {}).get(_W3C_ELEMENT)
            if not el:
                return CommandResult(False, "appium: no field has the focus to type into")
            self._s("POST", f"/element/{el}/value", {"text": str(arg)})
        elif name == "eraseText":
            for _ in range(int(arg if arg is not None else 50)):
                self._mobile("pressKey", keycode=KEYCODES["Backspace"])
        elif name == "hideKeyboard":
            try:
                self._s("POST", "/appium/device/hide_keyboard", {})
            except DriverError:
                pass  # no keyboard up: nothing to hide
        elif name == "scroll":
            self._swipe(up=True)
        elif name == "scrollUntilVisible":
            opts = arg if isinstance(arg, dict) and "element" in arg else {"element": arg}
            up = str(opts.get("direction", "DOWN")).upper() != "UP"  # finger moves up to see what is below
            for _ in range(10):
                if self._find(opts["element"], wait=0) is not None:
                    return CommandResult(True)
                self._swipe(up=up)
            if self._find(opts["element"], wait=0) is None:
                return CommandResult(False, f"Element not found: {shown(opts['element'])}")
        elif name == "waitForAnimationToEnd":
            time.sleep(0.5)
        elif name == "assertVisible":
            if self._find(arg) is None:
                return CommandResult(False, f"Element not found: {shown(arg)}")
        elif name == "assertNotVisible":
            if self._find(arg, wait=0) is not None:
                return CommandResult(False, f"Element is visible: {shown(arg)}")
        else:
            return CommandResult(False, f"this driver does not know the action {name}")
        return CommandResult(True)

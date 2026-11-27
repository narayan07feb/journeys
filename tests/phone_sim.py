"""A pretend phone for driver tests: answers adb, speedrun, maestro and appium calls from one FakeDriver app,
so every driver can run the same contract tests without a device."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import yaml

from journeys.drivers.fake import FakeDriver

ROW = 100  # each element is a full-width row, ROW px high, in screen order


def _done(out: bytes | str = b"", rc: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], rc, out.encode() if isinstance(out, str) else out, b"")


class Phone:
    def __init__(self, app: dict, busy: str = "", ps_works: bool = True, page: int | None = None):
        self.app = FakeDriver(app)
        self.pkg = app["app"]
        self.busy = busy          # a process name `ps` shows, e.g. dev.mobile.maestro
        self.ps_works = ps_works
        self.shell_log: list[str] = []
        self.typed: list[str] = []
        self.page, self.offset = page, 0   # page: rows on screen at once (None: all); a swipe moves one row
        self.focus: str | None = None      # a window other than the app's, e.g. a dialog
        self.keyboard = False
        self.settings: dict[str, str] = {}

    # what the phone shows
    def _elements(self):
        els = self.app.look().elements
        return els if self.page is None else els[self.offset:self.offset + self.page]

    # adb
    def adb(self, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
        if args[:2] == ("exec-out", "screencap"):
            return _done(b"\x89PNG fake")
        if args[0] == "logcat":
            return _done(self.app.crash_log())
        if args[0] != "shell":
            return _done(b"", 1)
        cmd = " ".join(args[1:])
        self.shell_log.append(cmd)
        if cmd == "ps -A -o NAME" and not self.ps_works:
            return _done(b"", 1)
        if cmd == "ps -A -o NAME":
            return _done("NAME\ninit\nsystem_server\ncom.android.systemui\n" + self.busy)
        if cmd.startswith("pidof "):
            return _done(b"123\n" if self.app.alive else b"", 0 if self.app.alive else 1)
        if cmd == "dumpsys window":
            fg = self.app.foreground_app()
            if self.focus == "null":  # nothing focused for a moment; the app task is behind
                return _done(f"  mCurrentFocus=null\n  mFocusedApp=ActivityRecord{{12 u0 {fg}/.Main t5}}\n")
            return _done(f"  mCurrentFocus=Window{{1a u0 {self.focus or f'{fg}/{fg}.Main'}}}\n")
        if cmd.startswith("getprop"):
            return _done(b"")
        if cmd == "wm size":
            return _done("Physical size: 1080x2400\n")
        if cmd.startswith("cmd package resolve-activity"):
            return _done(f"priority=0\n{self.pkg}/.Main\n")
        if cmd.startswith("am start"):
            self.app.do(["launchApp"])
            return _done("Status: ok\n")
        if cmd.startswith("am force-stop"):
            self.app.do(["stopApp"])
            return _done()
        if cmd.startswith("input tap"):
            x, y = map(int, cmd.split()[2:4])
            self.app.do([{"tapOn": f"@{self.offset + y // ROW + 1}"}])
            self.offset = 0
            return _done()
        if cmd.startswith("input keyevent"):
            if cmd.split()[2] == "4":
                self.app.do(["back"])
            elif cmd.split()[2] == "3":
                self.app.do([{"pressKey": "Home"}])
            return _done()
        if cmd.startswith("input text"):
            self.typed.append(cmd[len("input text "):])
            return _done()
        if cmd.startswith("input swipe") and self.page is None:
            return _done()  # everything is on screen: nothing moves
        if cmd.startswith("input swipe"):
            y1, y2 = int(cmd.split()[3]), int(cmd.split()[5])
            last = max(len(self.app.look().elements) - (self.page or 0), 0)
            self.offset = min(self.offset + 1, last) if y1 > y2 else max(self.offset - 1, 0)
            return _done()
        if cmd == "dumpsys input_method":
            return _done(f"  mInputShown={str(self.keyboard).lower()}\n")
        if cmd.startswith("settings get global "):
            return _done(self.settings.get(cmd.split()[3], "null") + "\n")
        if cmd.startswith("settings put global "):
            self.settings[cmd.split()[3]] = cmd.split()[4]
            return _done()
        if cmd.startswith("pm clear"):
            return _done()
        return _done(f"unknown: {cmd}", 1)

    # speedrun's session CLI
    def speedrun(self, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
        if args[0] == "session":
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[0] == "look":
            lines = [f"android 1080x2400, {len(self._elements())} elements"] + [e.line() for e in self._elements()]
            return subprocess.CompletedProcess(args, 0, "\n".join(lines) + "\n", "")
        if args[0] == "do":
            res = self.app.do(yaml.safe_load(stdin))
            return subprocess.CompletedProcess(args, 0 if res.ok else 1, "" if res.ok else f"FAILED\n  {res.message}", "")
        raise AssertionError(args)

    # maestro's CLI
    def maestro(self, *args: str) -> subprocess.CompletedProcess:
        if args[0] == "hierarchy":
            kids = [{"attributes": {"text": e.text, "resource-id": e.id,
                                    "class": "android.widget.EditText" if e.kind == "Field" else f"android.widget.{e.kind}",
                                    "clickable": str(e.kind == "Button").lower()}} for e in self._elements()]
            return subprocess.CompletedProcess(args, 0, json.dumps({"attributes": {}, "children": kids}), "")
        if args[0] == "test":
            flow = Path(args[1]).read_text().split("---\n", 1)[1]
            res = self.app.do([])
            for c in yaml.safe_load(flow):  # one at a time: an id resolves on the screen its command sees
                res = self.app.do([_as_maestro_matches(c, [e.id for e in self.app.look().elements if e.id])])
                if not res.ok:
                    break
            return subprocess.CompletedProcess(args, 0 if res.ok else 1, "", "" if res.ok else res.message)
        raise AssertionError(args)


def _as_maestro_matches(cmd, ids=()):
    """Maestro matches text and ids with IGNORE_CASE, DOT_MATCHES_ALL and MULTILINE (Orchestra.kt, cli-2.12.1)
    unless the pattern turns them off with a leading (?-ims), as java.util.regex allows. Python's re has
    no global "off" group, so: with the prefix, drop it (plain matching); without, add (?ims).
    An id pattern matches the whole resource-id or the part after its last "/" (Filters.idMatches); the
    app matches ids by name, so the pattern becomes the one screen id it matches (or none)."""
    def one(v):
        return v[len("(?-ims)"):] if v.startswith("(?-ims)") else f"(?ims:{v})"

    def rid(v):
        rx = one(v)
        hits = {i for i in ids if re.fullmatch(rx, i) or re.fullmatch(rx, i.rsplit("/", 1)[-1])}
        assert len(hits) <= 1, f"sim: id pattern {v!r} matches several ids {hits}"
        return hits.pop() if hits else "\x00matches no id"

    def sel(a):
        if isinstance(a, str):
            return one(a)
        return {k: one(v) if k == "text" and isinstance(v, str) else rid(v) if k == "id" and isinstance(v, str)
                else v for k, v in a.items()}
    if not isinstance(cmd, dict) or len(cmd) != 1:
        return cmd
    (name, arg), = cmd.items()
    if name in ("tapOn", "assertVisible", "assertNotVisible"):
        return {name: sel(arg)}
    if name == "scrollUntilVisible" and isinstance(arg, dict):
        return {name: {**arg, "element": sel(arg["element"])}}
    return cmd


def _appium(self, method: str, path: str, body: dict | None = None) -> dict:
    """The phone as an Appium (UiAutomator2) server would answer W3C WebDriver calls."""
    from xml.sax.saxutils import quoteattr
    body = body or {}
    if method == "POST" and path == "/session":
        return {"value": {"sessionId": "s1", "capabilities": {}}}
    assert path.startswith("/session/s1"), path
    path = path[len("/session/s1"):]
    if method == "DELETE" and path == "":
        return {"value": None}
    if path == "/source":
        rows = []
        for i, e in enumerate(self._elements()):
            cls = "android.widget.EditText" if e.kind == "Field" else f"android.widget.{e.kind}"
            rows.append(f"<{cls} class={quoteattr(cls)} text={quoteattr(e.text)} resource-id={quoteattr(e.id)} "
                        f"clickable=\"{str(e.kind == 'Button').lower()}\" enabled=\"true\" "
                        f"bounds=\"[0,{i * ROW}][1080,{(i + 1) * ROW}]\"/>")
        return {"value": "<?xml version='1.0' encoding='UTF-8'?><hierarchy rotation=\"0\">" + "".join(rows) + "</hierarchy>"}
    if path == "/window/rect":
        return {"value": {"x": 0, "y": 0, "width": 1080, "height": 2400}}
    if path == "/screenshot":
        import base64
        return {"value": base64.b64encode(b"\x89PNG fake").decode()}
    if path == "/actions":
        acts = body["actions"][0]["actions"]
        pts = [(a["x"], a["y"]) for a in acts if a["type"] == "pointerMove"]
        if len(pts) == 1:
            els = self._elements()
            row = pts[0][1] // ROW
            self.field = 0 <= row < len(els) and els[row].kind == "Field"  # a tapped field takes the focus
            return {"value": self.adb("shell", "input", "tap", str(pts[0][0]), str(pts[0][1])) and None}
        (x1, y1), (x2, y2) = pts[0], pts[-1]
        self.adb("shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2))
        return {"value": None}
    if path == "/back":
        self.app.do(["back"])
        return {"value": None}
    if path == "/execute/sync":
        script, args = body["script"], (body.get("args") or [{}])[0]
        if script == "mobile: activateApp":  # like Appium: a running app only comes to the front
            if self.app.alive:
                self.app.home = False
            else:
                self.app.do(["launchApp"])
        elif script == "mobile: terminateApp":
            self.app.do(["stopApp"])
        elif script == "mobile: pressKey":
            self.adb("shell", "input", "keyevent", str(args["keycode"]))
        elif script == "mobile: hideKeyboard":
            self.keyboard = False
        elif script == "mobile: clearApp":
            self.shell_log.append(f"pm clear {args['appId']}")  # what Appium's clearApp does on the device
        else:
            raise AssertionError(script)
        return {"value": None}
    if path == "/element/active":  # like Appium: nothing focused is a 404 "no such element"
        if not getattr(self, "field", False):
            from journeys.drivers.appium import AppiumError
            raise AppiumError("appium GET /session/s1/element/active: 404 no such element: no focused element",
                              "no such element")
        return {"value": {"element-6066-11e4-a52e-4f735466cecf": "e1"}}
    if path == "/element/e1/value":
        self.typed.append(body["text"])
        return {"value": None}
    if path == "/appium/device/hide_keyboard":
        self.keyboard = False
        return {"value": None}
    raise AssertionError((method, path))


Phone.appium = _appium


def hooked(kind: str, phone: Phone, tmp: Path):
    """A real driver of the given kind, wired to the pretend phone."""
    if kind == "fake":
        return phone.app
    if kind == "speedrun":
        from journeys.drivers.speedrun import SpeedrunDriver
        d = SpeedrunDriver("/bin/true", tmp / "srhome", allow_changes="any")
        d._sr = phone.speedrun
    elif kind == "appium":
        from journeys.drivers.appium import AppiumDriver
        d = AppiumDriver(allow_changes="any", wait_s=0, scroll_s=0.5)
        d._http = phone.appium
    elif kind == "maestro":
        from journeys.drivers.maestro import MaestroDriver
        d = MaestroDriver("/bin/true")
        d._run = phone.maestro
    else:
        raise ValueError(kind)
    d._adb = phone.adb
    return d


def ids(elements) -> list[str]:
    return [re.sub(r".*[:/]", "", e.id) for e in elements]

"""A pretend phone for driver tests: answers adb, speedrun and maestro calls from one FakeDriver app,
so every driver can run the same contract tests without a device."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from xml.sax.saxutils import quoteattr

import yaml

from journeys.drivers.fake import FakeDriver

ROW = 100  # each element is a full-width row, ROW px high, in screen order


def _done(out: bytes | str = b"", rc: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], rc, out.encode() if isinstance(out, str) else out, b"")


class Phone:
    def __init__(self, app: dict, busy: str = ""):
        self.app = FakeDriver(app)
        self.pkg = app["app"]
        self.busy = busy          # a process name `ps` shows, e.g. dev.mobile.maestro
        self.shell_log: list[str] = []
        self.typed: list[str] = []

    # what the phone shows
    def _elements(self):
        return self.app.look().elements

    def dump_xml(self) -> str:
        nodes = "".join(
            f'<node index="{i}" text={quoteattr(e.text)} resource-id={quoteattr(e.id)} '
            f'class="android.widget.{ "EditText" if e.kind == "Field" else "TextView" if e.kind == "Text" else e.kind}" '
            f'package="{self.pkg}" content-desc="" checkable="false" checked="{str("checked" in e.state).lower()}" '
            f'clickable="{str(e.kind in ("Button", "Field", "Switch")).lower()}" enabled="{str("disabled" not in e.state).lower()}" '
            f'focused="false" selected="false" bounds="[0,{i * ROW}][1080,{i * ROW + ROW - 10}]" />'
            for i, e in enumerate(self._elements()))
        return (f'<?xml version=\'1.0\' encoding=\'UTF-8\' standalone=\'yes\' ?><hierarchy rotation="0">'
                f'<node index="0" text="" resource-id="" class="android.widget.FrameLayout" package="{self.pkg}" '
                f'content-desc="" clickable="false" bounds="[0,0][1080,2400]">{nodes}</node></hierarchy>'
                "UI hierchary dumped to: /dev/tty\n")

    # adb
    def adb(self, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
        if args[:2] == ("exec-out", "uiautomator"):
            return _done(self.dump_xml())
        if args[:2] == ("exec-out", "screencap"):
            return _done(b"\x89PNG fake")
        if args[0] == "logcat":
            return _done(self.app.crash_log())
        if args[0] != "shell":
            return _done(b"", 1)
        cmd = " ".join(args[1:])
        self.shell_log.append(cmd)
        if cmd.startswith("ps "):
            return _done(self.busy)
        if cmd.startswith("pidof "):
            return _done(b"123\n" if self.app.alive else b"", 0 if self.app.alive else 1)
        if cmd == "dumpsys window":
            fg = self.app.foreground_app()
            return _done(f"  mCurrentFocus=Window{{1a u0 {fg}/{fg}.Main}}\n")
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
            self.app.do([{"tapOn": f"@{y // ROW + 1}"}])
            return _done()
        if cmd.startswith("input keyevent"):
            if cmd.split()[2] == "4":
                self.app.do(["back"])
            return _done()
        if cmd.startswith("input text"):
            self.typed.append(cmd[len("input text "):])
            return _done()
        if cmd.startswith(("input swipe", "pm clear", "dumpsys input_method")):
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
            res = self.app.do(yaml.safe_load(flow))
            return subprocess.CompletedProcess(args, 0 if res.ok else 1, "", "" if res.ok else res.message)
        raise AssertionError(args)


def hooked(kind: str, phone: Phone, tmp: Path):
    """A real driver of the given kind, wired to the pretend phone."""
    if kind == "fake":
        return phone.app
    if kind == "uiautomator":
        from journeys.drivers.uiautomator import UiAutomatorDriver
        d = UiAutomatorDriver(wait_s=0, sleep=lambda s: None)
    elif kind == "speedrun":
        from journeys.drivers.speedrun import SpeedrunDriver
        d = SpeedrunDriver("/bin/true", tmp / "srhome", allow_changes="any")
        d._sr = phone.speedrun
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

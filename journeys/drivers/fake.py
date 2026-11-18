"""An app described in YAML, played in memory. Used by the tests and by `--driver fake`.

    app: com.example.app
    start: HOME
    screens:
      HOME:
        elements:
          - {kind: Text, text: Home}
          - {kind: Button, text: Next, id: next, to: SECOND}   # a tap opens SECOND
          - {kind: Button, text: Boom, to: CRASH}              # a tap kills the app
          - {kind: Button, text: Skip, to: X, replace: true}   # opens X and closes this screen
      SECOND:
        elements: [{kind: Text, text: Second}]
        back: HOME                                             # default: the screen before
"""
from __future__ import annotations

from pathlib import Path

import yaml

from .base import CommandResult, Driver, DriverError, Element, Screen, command_name, find, shown

CRASH = "CRASH"
_NO_OP = {"waitForAnimationToEnd", "hideKeyboard", "inputText", "eraseText", "scroll", "pressKey"}


class FakeDriver(Driver):
    def __init__(self, app: dict):
        self.app = app
        self.stack: list[str] = []
        self.alive = False
        self.crashed = False
        self.log: list = []
        self.settings: dict[str, str] = {}
        self.emulator = True
        self.home = False  # the app is alive but behind the launcher (press home)

    @classmethod
    def from_file(cls, path: Path) -> "FakeDriver":
        return cls(yaml.safe_load(Path(path).read_text()))

    # Driver API
    def start(self, app_id: str, serial: str | None = None) -> None:
        if app_id != self.app.get("app"):
            raise DriverError(f"fake app is {self.app.get('app')}, not {app_id}")

    def look(self) -> Screen:
        if not self.alive or not self.stack or self.home:
            return Screen([], header="launcher")
        return Screen(self._elements(), header=f"fake {self.stack[-1]}")

    def do(self, commands: list) -> CommandResult:
        for cmd in commands:
            self.log.append(cmd)
            res = self._one(cmd)
            if not res.ok:
                return res
        return CommandResult(True)

    def screenshot(self, path: Path) -> Path | None:
        path = path.with_suffix(".txt")  # no pixels here: the listing stands in for the picture
        path.write_text(self.look().listing() + "\n")
        return path

    def app_alive(self) -> bool:
        return self.alive

    def stop(self) -> None:
        pass

    def foreground_app(self) -> str | None:
        if not (self.alive and self.stack) or self.home:
            return "launcher"
        return self.app["screens"][self.stack[-1]].get("package", self.app["app"])

    def crash_log(self) -> str:
        return "FATAL EXCEPTION: main (fake)" if self.crashed else ""

    def is_emulator(self) -> bool:
        return self.emulator

    def global_setting(self, name: str, value: str | None = None) -> str:
        old = self.settings.get(name, "null")
        if value is not None:
            self.settings[name] = value
        return old

    # internals
    def _raw(self) -> list[dict]:
        return self.app["screens"][self.stack[-1]].get("elements", [])

    def _elements(self) -> list[Element]:
        return [
            Element(f"@{i}", e.get("kind", "Text"), e.get("text", ""), e.get("id", ""), tuple(e.get("state", ())))
            for i, e in enumerate(self._raw(), 1)
        ]

    def _find(self, sel) -> dict | None:
        if not self.alive or not self.stack or self.home:
            return None
        i = find(self._elements(), sel)
        return None if i is None else self._raw()[i]

    def _one(self, cmd) -> CommandResult:
        name, arg = command_name(cmd)
        if name == "launchApp":
            self.alive, self.stack, self.crashed, self.home = True, [self.app["start"]], False, False
        elif name == "stopApp":
            self.alive, self.stack = False, []
        elif name == "pressKey" and str(arg).lower() == "home":
            self.home = True
        elif name == "back" or (name == "pressKey" and str(arg).lower() == "back"):
            if self.alive and self.stack and not self.home:
                target = self.app["screens"][self.stack[-1]].get("back")
                self.stack.pop()
                if target:
                    self.stack.append(target)
        elif name == "tapOn":
            el = self._find(arg)
            if el is None:
                return CommandResult(False, f"Element not found: {shown(arg)}")
            to = el.get("to")
            if el.get("dka_crash") and self.settings.get("always_finish_activities") == "1":
                to = CRASH  # this tap only breaks when activities are not kept
            if to == CRASH:
                self.alive, self.stack, self.crashed = False, [], True
            elif to:
                if el.get("replace"):  # the tap closes this screen (an interstitial's finish())
                    self.stack.pop()
                self.stack.append(to)
        elif name in ("assertVisible", "scrollUntilVisible"):
            if self._find(arg) is None:
                return CommandResult(False, f"Element not found: {shown(arg)}")
        elif name == "assertNotVisible":
            if self._find(arg) is not None:
                return CommandResult(False, f"Element is visible: {shown(arg)}")
        elif name not in _NO_OP:
            return CommandResult(False, f"fake driver does not know {name}")
        return CommandResult(True)

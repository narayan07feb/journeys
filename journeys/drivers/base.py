"""What every driver gives the runner: read the screen, run flow commands, take a screenshot."""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Element:
    ref: str
    kind: str
    text: str = ""
    id: str = ""
    state: tuple[str, ...] = ()

    def line(self) -> str:
        parts = [self.ref, self.kind]
        if self.text:
            parts.append(f'"{self.text}"')
        if self.id:
            parts.append(f"#{self.id}")
        parts.extend(self.state)
        return " ".join(parts)


@dataclass
class Screen:
    elements: list[Element] = field(default_factory=list)
    header: str = ""

    def listing(self) -> str:
        return "\n".join([self.header, *(e.line() for e in self.elements)]).strip()


@dataclass
class CommandResult:
    ok: bool
    message: str = ""


class DriverError(Exception):
    """The driver cannot work at all (no binary, no device, session lost)."""


class Driver(ABC):
    @abstractmethod
    def start(self, app_id: str, serial: str | None = None) -> None: ...

    @abstractmethod
    def look(self) -> Screen: ...

    @abstractmethod
    def do(self, commands: list) -> CommandResult:
        """Run commands in the drivers' shared form (journeys/actions.py) in order; stop at the first that fails."""

    @abstractmethod
    def screenshot(self, path: Path) -> Path | None: ...

    @abstractmethod
    def app_alive(self) -> bool: ...

    @abstractmethod
    def stop(self) -> None: ...

    def foreground_app(self) -> str | None:
        """The package on screen, or None when the driver cannot tell."""
        return None

    def crash_log(self) -> str:
        """Recent crash lines, read-only (never clears any buffer). Optional."""
        return ""

    def is_emulator(self) -> bool:
        return False

    def global_setting(self, name: str, value: str | None = None) -> str:
        """Read an Android global setting; with value, set it and return the old one."""
        raise DriverError("this driver cannot change device settings")


def changes_allowed(rule, driver: Driver, serial: str | None) -> bool:
    """pack.yaml devices.allow_settings_changes: "any", "emulator" (default) or a list of serials."""
    if rule == "any":
        return True
    if rule == "emulator":
        return driver.is_emulator()
    return isinstance(rule, list) and serial in rule


def kind_of(cls: str, clickable: bool, label: str) -> str:
    """An element's kind from its Android class, the same for every driver. A clickable element
    with no label (a bare View, Image or Text) is a Button."""
    name = (cls or "").rsplit(".", 1)[-1]
    kind = "Field" if name.endswith("EditText") else next(
        (k for k in ("Button", "Switch", "CheckBox", "Image", "Text") if k in name), "View")
    return "Button" if clickable and kind in ("View", "Image", "Text") and not label.strip() else kind


def command_name(cmd) -> tuple[str, object]:
    """A flow command is a bare name ("back") or a one-key map ({tapOn: "Next"})."""
    if isinstance(cmd, str):
        return cmd, None
    if isinstance(cmd, dict) and len(cmd) == 1:
        return next(iter(cmd.items()))
    raise ValueError(f"not a flow command: {cmd!r}")


def text_matches(pattern: str, text: str) -> bool:
    """Flow selectors treat text as a regex that must match the whole text."""
    if pattern == text:
        return True
    try:
        return re.fullmatch(pattern, text) is not None
    except re.error:
        return False


def id_matches(want: str, have: str) -> bool:
    """An id matches in full (pkg:id/name) or by its name alone."""
    return bool(have) and (have == want or have.endswith(f":id/{want}") or have.rsplit("/", 1)[-1] == want)


def find(elements: list[Element], sel) -> int | None:
    """Index of the element a selector names: "@3", "text or /regex/", or {text, id, index}."""
    if isinstance(sel, str) and sel.startswith("@") and sel[1:].isdigit():
        i = int(sel[1:]) - 1
        return i if 0 <= i < len(elements) else None
    if isinstance(sel, dict) and "element" in sel:
        sel = sel["element"]
    if isinstance(sel, str):
        sel = {"text": sel}
    if not isinstance(sel, dict):
        return None
    hits = [i for i, e in enumerate(elements)
            if ("id" not in sel or id_matches(str(sel["id"]), e.id))
            and ("text" not in sel or text_matches(str(sel["text"]), e.text))]
    try:
        idx = int(sel.get("index", 0))
    except (TypeError, ValueError):
        return None
    return hits[idx] if 0 <= idx < len(hits) else None


def focused_window(dumpsys_window: str) -> str | None:
    """The focused window from `dumpsys window` (read-only): "pkg/Activity" for an app's activity,
    else the window's own name ("NotificationShade", "Application Not Responding: com.x").
    The app behind (mFocusedApp) counts only when nothing has the focus (mCurrentFocus=null)."""
    m = re.search(r"mCurrentFocus=(null|Window\{\S+ \S+ ([^}]*)\})", dumpsys_window)
    if not m:
        return None
    if m.group(1) != "null":
        name = m.group(2).strip()
        return name if re.fullmatch(r"[\w.]+/[\w.$]+", name) else f"window:{name}"
    app = re.search(r"mFocusedApp=.*? ([\w.]+/[\w.$]+)", dumpsys_window)
    return app.group(1) if app else None


def focused_package(dumpsys_window: str) -> str | None:
    """The package of the focused window; a system window gives its "window:<name>"."""
    w = focused_window(dumpsys_window)
    return w if w is None or w.startswith("window:") else w.split("/")[0]

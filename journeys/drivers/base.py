"""What every driver gives the runner: read the screen, run flow commands, take a screenshot."""
from __future__ import annotations

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
        """Run Maestro/Speedrun flow commands in order; stop at the first that fails."""

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

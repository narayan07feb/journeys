"""Fallback driver: Maestro's CLI. Slower (a JVM start per call), used when Speedrun cannot run.

`do` writes the commands as a one-off flow and runs `maestro test`; `look` reads `maestro hierarchy`.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml

from .base import CommandResult, Driver, DriverError, Element, Screen


def _short(cls: str) -> str:
    name = (cls or "").rsplit(".", 1)[-1]
    if name.endswith("EditText"):
        return "Field"
    for kind in ("Button", "Switch", "CheckBox", "Image", "Text"):
        if kind in name:
            return kind
    return "View"


def parse_hierarchy(data: dict) -> Screen:
    """Flatten Maestro's hierarchy tree into the elements a user can see or act on, in order."""
    out: list[Element] = []

    def walk(node: dict) -> None:
        a = node.get("attributes") or {}
        text = a.get("text") or a.get("accessibilityText") or a.get("hintText") or ""
        rid = (a.get("resource-id") or "").rsplit("/", 1)[-1]
        clickable = a.get("clickable") == "true"
        if text or rid or clickable:
            state = tuple(s for s in ("checked", "selected", "focused") if a.get(s) == "true")
            if a.get("enabled") == "false":
                state += ("disabled",)
            kind = _short(a.get("class", ""))
            if clickable and kind in ("View", "Text") and not text.strip():
                kind = "Button"
            out.append(Element(f"@{len(out) + 1}", kind, text, rid, state))
        for ch in node.get("children") or []:
            walk(ch)

    walk(data)
    return Screen(out, header=f"maestro, {len(out)} elements")


class MaestroDriver(Driver):
    def __init__(self, binary: str | None = None, timeout_s: int = 180):
        self.bin = binary or shutil.which("maestro")
        if not self.bin:
            raise DriverError("maestro not found on PATH")
        self.timeout, self.serial, self.app = timeout_s, None, ""

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        dev = ["--device", self.serial] if self.serial else []
        try:
            return subprocess.run([self.bin, *dev, *args], capture_output=True, text=True, timeout=self.timeout)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise DriverError(f"maestro {args[0]}: {e}") from None

    def _adb(self, *args: str) -> subprocess.CompletedProcess:
        serial = ["-s", self.serial] if self.serial else []
        return subprocess.run(["adb", *serial, *args], capture_output=True, timeout=60)

    def start(self, app_id: str, serial: str | None = None) -> None:
        self.app, self.serial = app_id, serial

    def look(self) -> Screen:
        p = self._run("hierarchy")
        m = re.search(r"\{.*\}", p.stdout, re.S)  # skip any banner lines before the JSON
        if p.returncode != 0 or not m:
            raise DriverError(f"maestro hierarchy failed: {(p.stderr or p.stdout).strip()[-300:]}")
        return parse_hierarchy(json.loads(m.group(0)))

    def do(self, commands: list) -> CommandResult:
        flow = f"appId: {self.app}\n---\n" + yaml.safe_dump(commands, sort_keys=False, allow_unicode=True)
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write(flow)
        try:
            p = self._run("test", f.name)
        finally:
            Path(f.name).unlink(missing_ok=True)
        out = (p.stdout + p.stderr).strip()
        return CommandResult(p.returncode == 0, out[-500:] if p.returncode else "")

    def screenshot(self, path: Path) -> Path | None:
        p = self._adb("exec-out", "screencap", "-p")
        if p.returncode != 0 or not p.stdout.startswith(b"\x89PNG"):
            return None
        path.write_bytes(p.stdout)
        return path

    def app_alive(self) -> bool:
        return self._adb("shell", "pidof", self.app).returncode == 0

    def stop(self) -> None:
        pass

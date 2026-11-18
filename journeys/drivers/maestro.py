"""The Maestro driver, for teams that use Maestro: each step runs as a one-off flow through its CLI
(a JVM start per call).

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

from .adb import AdbDriver
from .base import CommandResult, DriverError, Element, Screen, kind_of


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
            kind = kind_of(a.get("class", ""), clickable, text)
            out.append(Element(f"@{len(out) + 1}", kind, text, rid, state))
        for ch in node.get("children") or []:
            walk(ch)

    walk(data)
    return Screen(out, header=f"{len(out)} elements")


def _maestro_sel(sel):
    """The drivers' selector -> Maestro's (text is a whole-text regex in both)."""
    return {"text": sel} if isinstance(sel, str) else dict(sel)


def to_maestro(cmd):
    """One command in the drivers' shared form -> Maestro flow syntax."""
    if isinstance(cmd, str):
        return cmd
    (name, arg), = cmd.items()
    if name in ("tapOn", "assertVisible", "assertNotVisible"):
        return {name: arg if isinstance(arg, str) else _maestro_sel(arg)}
    if name == "scrollUntilVisible":
        opts = arg if isinstance(arg, dict) and "element" in arg else {"element": arg}
        return {name: {"element": _maestro_sel(opts["element"]), "direction": opts.get("direction", "DOWN")}}
    return cmd


class MaestroDriver(AdbDriver):
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

    def start(self, app_id: str, serial: str | None = None) -> None:
        self.app, self.serial = app_id, serial

    def look(self) -> Screen:
        p = self._run("hierarchy")
        m = re.search(r"\{.*\}", p.stdout, re.S)  # skip any banner lines before the JSON
        if p.returncode != 0 or not m:
            raise DriverError(f"maestro hierarchy failed: {(p.stderr or p.stdout).strip()[-300:]}")
        return parse_hierarchy(json.loads(m.group(0)))

    def do(self, commands: list) -> CommandResult:
        flow = f"appId: {self.app}\n---\n" + yaml.safe_dump([to_maestro(c) for c in commands], sort_keys=False,
                                                         allow_unicode=True)
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write(flow)
        try:
            p = self._run("test", f.name)
        finally:
            Path(f.name).unlink(missing_ok=True)
        out = (p.stdout + p.stderr).strip()
        return CommandResult(p.returncode == 0, out[-500:] if p.returncode else "")

    def stop(self) -> None:
        pass

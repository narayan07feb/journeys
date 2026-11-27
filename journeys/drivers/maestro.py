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

from ..actions import ActionError
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


# Maestro compiles text and id selectors with IGNORE_CASE, DOT_MATCHES_ALL and MULTILINE (Orchestra.kt
# REGEX_OPTIONS, maestro cli-2.12.1). This inline flag turns all three off again, so Maestro matches a
# selector exactly as the other drivers do: case-sensitive, `.` not across a line break, ^ and $ at the ends.
SAME = "(?-ims)"


# Python reads these, Java (Maestro) does not: (?P<n>...) groups, (?#...) comments, (?(1)a|b) choices
# and a "{" that is not a count.
_NOT_JAVA = re.compile(r"\(\?[P#(]|\{(?!\d+(,\d*)?\})")
_LITERAL = re.compile(r"\\.|\[\^?\]?(\\.|[^\]])*\]")  # escapes and [...] classes: not checked (most read the same; docs/packs.md lists the ones that do not)


def _java(rx: str) -> str:
    if _NOT_JAVA.search(_LITERAL.sub("_", rx)):
        raise ActionError(f"/{rx}/: Maestro (Java regex) cannot read this pattern; escape a {{ that is not a "
                          "count as \\{, and use (?:...) in place of a named group, comment or (?(1)...) choice")
    return SAME + rx


def _maestro_sel(sel):
    """The drivers' selector -> Maestro's (text is a whole-text regex in both; SAME keeps the case)."""
    out = {"text": sel} if isinstance(sel, str) else dict(sel)
    return {k: _java(v) if k in ("text", "id") and isinstance(v, str) else v for k, v in out.items()}


def to_maestro(cmd):
    """One command in the drivers' shared form -> Maestro flow syntax."""
    if isinstance(cmd, str):
        return cmd
    (name, arg), = cmd.items()
    if name == "pressKey" and arg == "Search":
        raise ActionError("press search: Maestro has no Search key (its KeyCode list); use another driver, "
                          "or type and press enter")
    if name in ("tapOn", "assertVisible", "assertNotVisible"):
        sel = _maestro_sel(arg)
        return {name: sel["text"] if isinstance(arg, str) else sel}
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
        try:
            return parse_hierarchy(json.loads(m.group(0)))
        except (ValueError, AttributeError, TypeError, KeyError) as e:
            raise DriverError(f"maestro hierarchy: unreadable output ({e})") from None

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

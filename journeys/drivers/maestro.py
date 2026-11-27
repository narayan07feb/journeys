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


from ..actions import ActionError, dump, escape
from ..regex_parity import JAVA, check
from .adb import AdbDriver
from .base import CommandResult, DriverError, Element, Screen, id_tail, kind_of


def parse_hierarchy(data: dict) -> Screen:
    """Flatten Maestro's hierarchy tree into the elements a user can see or act on, in order."""
    out: list[Element] = []

    def walk(node: dict) -> None:
        a = node.get("attributes") or {}
        text = a.get("text") or a.get("accessibilityText") or a.get("hintText") or ""
        rid = a.get("resource-id") or ""  # the whole id, so a package in a selector or map rule counts
        clickable = a.get("clickable") == "true"
        if text or id_tail(rid) or clickable:  # an id with an empty name ("group/") is no id
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


def _java(rx: str) -> str:
    check(rx, JAVA)  # refuses a pattern Java reads differently
    return SAME + rx


def _maestro_id(want: str) -> str:
    """An id as plain text, so Maestro matches it as id_matches does. Maestro reads an id as a pattern and
    matches it against the whole resource-id and against the part after its last "/" (Filters.idMatches,
    maestro cli-2.12.1). So a bare name goes alone (any package's view with that name), and pkg:id/name goes
    whole (only that resource-id: the part after the last "/" never holds ":id/")."""
    pkg, sep, name = want.rpartition(":id/")
    if not name or "/" in name or "/" in pkg or (sep and not pkg):
        raise ActionError(f"id {want!r}: Maestro can match only name or pkg:id/name, each part not empty and "
                          "with no /")
    if "${" in want:
        raise ActionError(f"id {want!r}: Maestro would read the variable's value as a pattern; write the id "
                          "in the journey, or set it in the flow by hand")
    return SAME + escape(want)


def _maestro_sel(sel):
    """The drivers' selector -> Maestro's (text is a whole-text regex in both; SAME keeps the case)."""
    out = {"text": sel} if isinstance(sel, str) else dict(sel)
    return {k: _java(v) if k == "text" and isinstance(v, str) else _maestro_id(v) if k == "id" and isinstance(v, str)
            else v for k, v in out.items()}


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
        flow = f"appId: {self.app}\n---\n" + dump([to_maestro(c) for c in commands])
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

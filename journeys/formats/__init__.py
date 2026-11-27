"""Other tools' test formats <-> neutral journeys (import and export)."""
from __future__ import annotations

import re

from ..actions import ActionError, escape, to_commands
from ..journey import Journey
from ..pack import Pack
from ..regex_parity import ANY


def _top_level_or(rx: str) -> bool:
    """Whether the pattern has a | outside every group and class."""
    depth, cls, i = 0, False, 0
    while i < len(rx):
        c = rx[i]
        if c == "\\":
            i += 2
            continue
        if cls:
            cls = c != "]"
        elif c == "[":
            cls = True
        elif c in "()":
            depth += 1 if c == "(" else -1
        elif c == "|" and depth == 0:
            return True
        i += 1
    return False


class MapText(str):
    """A selector text made from a map rule: the map never reads ${NAME}, and it must match as a pattern."""


def _part(rx: str, screen: str) -> str:
    """A map rule's pattern, which may match part of a text, as one that matches all of it: [\\s\\S]* on both
    sides (every character, line breaks too, in Python, Java and JavaScript). A leading (?i) stays at the
    start, where Python reads flags. The result must compile: a broken one is an error, never plain text."""
    flags = "(?i)" if rx.startswith("(?i)") else ""
    inner = rx[len(flags):]
    left = right = f"{ANY}*"
    if not _top_level_or(inner):  # a rule's own ^ or $ is the start or end of the text: no padding there
        if inner.startswith("^"):
            inner, left = inner[1:], ""
        body = inner[:-1]
        if inner.endswith("$") and (len(body) - len(body.rstrip("\\"))) % 2 == 0:  # not an escaped \$
            inner, right = body, "\\n?"  # Python's $ also matches before a final line break
    out = f"{flags}{left}(?:{inner}){right}"
    try:
        re.compile(out)
    except re.error as e:
        raise ActionError(f"screen {screen}: rule /{rx}/ cannot be exported as a whole-text pattern ({e})") from None
    return out


def screen_checks(pack: Pack, screen: str, tool: str, notes: list[str] | None = None) -> list:
    """Asserts (shared command form) that stand for `expect: SCREEN`: its `all` rules with text or id.
    A map rule's /regex/ may match part of a text; a flow's must match all of it (see _part)."""
    out = []
    for r in pack.map.screens[screen].all:
        sel = {}
        if r.text is not None:
            regex = len(r.text) > 1 and r.text.startswith("/") and r.text.endswith("/")
            sel["text"] = MapText(_part(r.text[1:-1], screen) if regex else escape(r.text))
        if r.id is not None:
            sel["id"] = r.id
        if sel:
            out.append({"assertVisible": sel})
    sd = pack.map.screens[screen]
    dropped = [k for k, rules in (("any", sd.any), ("none", sd.none)) if rules]
    if any(r.kind for r in sd.all):
        dropped.append("kind")
    if notes is not None and dropped:
        notes.append(f"screen {screen}: its {', '.join(dropped)} rules have no {tool} check; "
                     f"the test checks {len(out)} 'all' rule(s)")
    return out


def journey_commands(pack: Pack, j: Journey, tool: str, notes: list[str]) -> list:
    """The whole journey as shared commands, for an export: gotos follow the map's path, expects become
    asserts. What only Journeys can run (hooks, event checks) is listed in notes and left out."""
    cmds, here = [], None
    if j.launch:
        cmds.append({"launchApp": {"clearState": True}} if j.clear_state else "launchApp")
    for s in j.steps:
        at = f"{j.file.name}:{s.line}"
        if s.kind == "expect":
            cmds += screen_checks(pack, s.arg, tool, notes)
            here = s.arg
        elif s.kind == "goto":
            path = pack.map.path(here, s.arg) if here else None
            if path is None:
                notes.append(f"{at}: goto {s.arg}: start screen unknown (add an expect before it); left out")
                continue
            for e in path:
                cmds += to_commands(e.do)
            cmds += screen_checks(pack, s.arg, tool, notes)
            here = s.arg
        elif s.kind in ("see", "not_see"):
            cmds += to_commands({s.kind: s.arg})
        elif s.kind == "do":
            cmds += to_commands(s.arg)
            here = None  # an action may change the screen
        elif s.kind == "input":
            cmds.append({"inputText": str(s.arg)})
            here = None
        else:
            here = None
            notes.append(f"{at}: {s.kind} steps run only in Journeys (pack hooks and event checks); left out")
    if j.after:
        notes.append(f"{j.file.name}:{j.line}: after: cleanup hooks run only in Journeys; left out "
                     "(run them yourself after the test)")
    return cmds

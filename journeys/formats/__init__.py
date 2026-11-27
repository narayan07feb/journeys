"""Other tools' test formats <-> neutral journeys (import and export)."""
from __future__ import annotations

import re

from ..actions import ActionError, escape, to_commands
from ..journey import Journey
from ..pack import Pack
from ..regex_parity import ANY


def _scan(rx: str) -> tuple[list[int], list[tuple[int, int, bool]]]:
    """The top-level | positions, and each group's (start, end, has a | directly inside)."""
    top, groups, stack, cls, i = [], [], [], False, 0
    while i < len(rx):
        c = rx[i]
        if c == "\\":
            i += 2
            continue
        if cls:
            cls = c != "]"
        elif c == "[":
            cls = True
        elif c == "(":
            stack.append([i, False])
        elif c == ")" and stack:
            start, alt = stack.pop()
            groups.append((start, i, alt))
        elif c == "|" and stack:
            stack[-1][1] = True
        elif c == "|":
            top.append(i)
        i += 1
    return top, groups


def _branch(b: str) -> str:
    """One top-level branch of a map rule, padded to match a whole text. Its own leading ^ (after any
    opening groups) and trailing $ (before any closing ones) mark the start and end of the text, so they
    replace the padding there, unless a group around them has a | (then they stay, and check() refuses)."""
    left = right = f"{ANY}*"
    _, groups = _scan(b)
    lead = re.match(r"(?:\((?:\?:)?)*", b).end()
    if b[lead:lead + 1] == "^" and not any(alt for start, _, alt in groups if start < lead):
        b, left = b[:lead] + b[lead + 1:], ""
    core = b.rstrip(")")
    tail = len(b) - len(core)
    escaped = (len(core) - 1 - len(core[:-1].rstrip("\\"))) % 2  # an odd run of \ before the $
    if core.endswith("$") and not escaped and not any(alt for _, end, alt in groups if end >= len(core)):
        b, right = core[:-1] + ")" * tail, "\\n?"  # Python's $ also matches before a final line break
    return f"{left}(?:{b}){right}"


class MapText(str):
    """A selector text made from a map rule: the map never reads ${NAME}, and it must match as a pattern."""


def _part(rx: str, screen: str) -> str:
    """A map rule's pattern, which may match part of a text, as one that matches all of it: each top-level
    branch gets [\\s\\S]* on both sides (every character, line breaks too, in Python, Java and JavaScript).
    A leading (?i) stays at the start, where Python reads flags. The result must compile: a broken one is
    an error, never plain text."""
    flags = "(?i)" if rx.startswith("(?i)") else ""
    inner = rx[len(flags):]
    cuts = _scan(inner)[0]
    branches = [inner[a + 1:b] for a, b in zip([-1, *cuts], [*cuts, len(inner)])]
    out = flags + "|".join(_branch(b) for b in branches)
    try:
        re.compile(out)
    except (re.error, OverflowError, RecursionError) as e:
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

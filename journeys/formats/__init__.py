"""Other tools' test formats <-> neutral journeys (import and export)."""
from __future__ import annotations

from ..actions import escape, to_commands
from ..journey import Journey
from ..pack import Pack


def screen_checks(pack: Pack, screen: str, tool: str, notes: list[str] | None = None) -> list:
    """Asserts (shared command form) that stand for `expect: SCREEN`: its `all` rules with text or id.
    A map rule's /regex/ may match part of a text; a flow's must match all of it, hence .*(?:...).*"""
    out = []
    for r in pack.map.screens[screen].all:
        sel = {}
        if r.text is not None:
            regex = len(r.text) > 1 and r.text.startswith("/") and r.text.endswith("/")
            sel["text"] = f".*(?:{r.text[1:-1]}).*" if regex else escape(r.text)
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

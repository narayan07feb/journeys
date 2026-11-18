"""Maestro flows <-> neutral journeys, so a team that uses Maestro keeps its own flows and runner.

Export writes a flow that the team's `maestro test` runs. Import turns a flow into a draft journey;
what has no neutral word is listed, never guessed.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from ..actions import KEYS, ActionError, escape, from_selector, to_commands, to_selector
from ..journey import Journey
from ..pack import Pack

def _maestro_sel(sel):
    """Internal selector -> Maestro's (text is a regex in both)."""
    return {"text": sel} if isinstance(sel, str) else dict(sel)


def _maestro(cmd):
    if isinstance(cmd, str):
        return cmd
    (name, arg), = cmd.items()
    if name in ("tapOn", "assertVisible", "assertNotVisible"):
        return {name: arg if isinstance(arg, str) else _maestro_sel(arg)}
    if name == "scrollUntilVisible":
        return {name: {"element": _maestro_sel(arg["element"]), "direction": arg["direction"]}}
    return cmd


def screen_checks(pack: Pack, screen: str, notes: list[str] | None = None) -> list:
    """Maestro asserts that stand for `expect: SCREEN` (its `all` rules with text or id). A map rule's
    /regex/ may match part of a text; Maestro's must match all of it, hence the .*(?:...).*"""
    out = []
    for r in pack.map.screens[screen].all:
        sel = {}
        if r.text is not None:
            sel["text"] = f".*(?:{r.text[1:-1]}).*" if to_selector(r.text) != escape(r.text) else escape(r.text)
        if r.id is not None:
            sel["id"] = r.id
        if sel:
            out.append({"assertVisible": sel})
    sd = pack.map.screens[screen]
    dropped = [k for k, rules in (("any", sd.any), ("none", sd.none)) if rules]
    if any(r.kind for r in sd.all):
        dropped.append("kind")
    if notes is not None and dropped:
        notes.append(f"screen {screen}: its {', '.join(dropped)} rules have no Maestro check; "
                     f"the flow checks {len(out)} 'all' rule(s)")
    return out


def export(pack: Pack, j: Journey) -> tuple[str, list[str]]:
    """A Maestro flow for the journey, and what could not be exported."""
    notes, cmds, here = [], [], None
    if j.launch:
        cmds.append({"launchApp": {"clearState": True}} if j.clear_state else "launchApp")
    for s in j.steps:
        at = f"{j.file.name}:{s.line}"
        if s.kind == "expect":
            cmds += screen_checks(pack, s.arg, notes)
            here = s.arg
        elif s.kind == "goto":
            path = pack.map.path(here, s.arg) if here else None
            if path is None:
                notes.append(f"{at}: goto {s.arg}: start screen unknown (add an expect before it); left out")
                continue
            for e in path:
                cmds += [_maestro(c) for c in to_commands(e.do)]
            cmds += screen_checks(pack, s.arg, notes)
            here = s.arg
        elif s.kind in ("see", "not_see"):
            cmds += [_maestro(c) for c in to_commands({s.kind: s.arg})]
        elif s.kind == "do":
            cmds += [_maestro(c) for c in to_commands(s.arg)]
            here = None  # an action may change the screen
        elif s.kind == "input":
            cmds.append({"inputText": str(s.arg)})
            here = None
        else:
            here = None
            notes.append(f"{at}: {s.kind} steps run only in Journeys (pack hooks and event checks); left out")
    body = yaml.safe_dump(cmds, sort_keys=False, allow_unicode=True)
    return f"# Exported from Journeys journey {j.name} (pack {pack.name}).\nappId: {pack.app}\n---\n{body}", notes


def _neutral_sel(arg):
    if isinstance(arg, str):
        return from_selector(arg)
    if isinstance(arg, dict) and set(arg) <= {"text", "id", "index"}:
        return from_selector(arg)
    raise ActionError(f"selector {arg!r} has keys with no neutral form")


def slug(stem: str) -> str:
    """A journey name from a file name: "Login Flow_2" -> "login-flow-2"."""
    return re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-") or "flow"


def import_flow(path: Path) -> tuple[dict, list[str]]:
    """A draft journey (as a dict to write) from one Maestro flow file, and what was left out."""
    docs = [d for d in yaml.safe_load_all(path.read_text()) if d is not None]
    if not docs or not isinstance(docs[-1], list):
        raise ActionError(f"{path.name}: not a Maestro flow (no command list)")
    commands = docs[-1]
    notes, steps, actions = [], [], []
    start = {"launch": False}

    def flush():
        if actions:
            steps.append({"do": list(actions)})
            actions.clear()

    for i, cmd in enumerate(commands):
        if isinstance(cmd, str):
            name, arg = cmd, None
        elif isinstance(cmd, dict) and len(cmd) == 1:
            (name, arg), = cmd.items()
        else:
            name, arg = None, cmd
        try:
            if name == "launchApp" and not steps and not actions:
                start = {"launch": True, "clear_state": bool(isinstance(arg, dict) and arg.get("clearState"))}
            elif name in ("assertVisible", "assertNotVisible"):
                flush()
                steps.append({"see" if name == "assertVisible" else "not_see": _neutral_sel(arg)})
            elif name == "tapOn":
                actions.append({"tap": _neutral_sel(arg)})
            elif name == "inputText":
                actions.append({"type": str(arg)})
            elif name == "eraseText":
                n = arg.get("charactersToErase", 50) if isinstance(arg, dict) else (arg or 50)
                actions.append({"erase": int(n)})
            elif name == "pressKey":
                if str(arg).lower() not in KEYS:
                    raise ActionError(f"pressKey {arg}: not a neutral key ({', '.join(sorted(KEYS))})")
                actions.append({"press": str(arg).lower()})
            elif name == "scrollUntilVisible":
                el = arg.get("element") if isinstance(arg, dict) else arg
                up = isinstance(arg, dict) and str(arg.get("direction", "DOWN")).upper() == "UP"
                sel = _neutral_sel(el)
                actions.append({"scroll_to": {"element": sel, "direction": "up"} if up else sel})
            elif name in ("back", "hideKeyboard", "scroll", "waitForAnimationToEnd", "stopApp", "launchApp"):
                actions.append({"back": "back", "hideKeyboard": "hide_keyboard", "scroll": "scroll",
                                "waitForAnimationToEnd": "wait_idle", "stopApp": "stop", "launchApp": "launch"}[name])
            else:
                raise ActionError(f"{name or cmd!r} has no neutral word")
        except (ActionError, TypeError, ValueError) as e:
            notes.append(f"{path.name} command {i + 1}: {e}; left out")
    flush()
    if not steps:
        steps.append({"do": ["wait_idle"]})
        notes.append(f"{path.name}: no command could be imported; the draft holds a placeholder step")
    return {"name": slug(path.stem), "tags": ["imported"], "start": start, "steps": steps}, notes

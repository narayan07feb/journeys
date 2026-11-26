"""Maestro flows <-> neutral journeys, so a team that uses Maestro keeps its own flows and runner.

Export writes a flow that the team's `maestro test` runs. Import turns a flow into a draft journey;
what has no neutral word is listed, never guessed.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from ..actions import KEYS, ActionError, from_selector, to_command, to_selector
from ..drivers.maestro import SAME, to_maestro
from ..journey import Journey
from ..pack import Pack
from . import journey_commands


def export(pack: Pack, j: Journey) -> tuple[str, list[str]]:
    """A Maestro flow for the journey, and what could not be exported."""
    notes: list[str] = []
    cmds = [to_maestro(c) for c in journey_commands(pack, j, "Maestro", notes)]
    if "${" in yaml.safe_dump(cmds):
        notes.append("a step uses ${NAME}: Maestro reads it as its own variable; run with -e NAME=... "
                     "or replace it in the flow")
    body = yaml.safe_dump(cmds, sort_keys=False, allow_unicode=True)
    return f"# Exported from Journeys journey {j.name} (pack {pack.name}).\nappId: {pack.app}\n---\n{body}", notes


_FLOW_VAR = re.compile(r"\$\{|\{\{")
_REGEX_CHARS = re.compile(r"[.^$*+?{}\[\]\\|()]")


def _plain(value, what: str) -> str:
    """Text from a flow, refused when it holds a Maestro variable (${X}, {{X}}) we cannot resolve."""
    if not isinstance(value, (str, int, float)) or isinstance(value, bool) or str(value) == "":
        raise ActionError(f"{what} {value!r} is not a plain text")
    if _FLOW_VAR.search(str(value)):
        raise ActionError(f"{what} {value!r} uses a Maestro variable; write it with the pack's ${{NAME}} by hand")
    return str(value)


def _unsame(v):
    """A flow Journeys wrote carries SAME in front of its selectors: drop it again."""
    return v[len(SAME):] if isinstance(v, str) and v.startswith(SAME) else v


def _neutral_sel(arg):
    arg = _unsame(arg) if isinstance(arg, str) else {k: _unsame(v) for k, v in arg.items()} if isinstance(arg, dict) else arg
    if isinstance(arg, str):
        return from_selector(_plain(arg, "selector"))
    if isinstance(arg, dict) and set(arg) <= {"text", "id", "index"}:
        if "text" in arg:
            _plain(arg["text"], "selector text")
        rid = _plain(arg["id"], "id") if "id" in arg else ""
        if rid and not re.fullmatch(r"[\w.:/-]+", rid) and _REGEX_CHARS.search(rid):  # "pkg:id/name" is fine
            raise ActionError(f"id {arg['id']!r} is a pattern; Journeys ids match exactly")
        return from_selector(arg)
    raise ActionError(f"selector {arg!r} has keys with no neutral form")


def slug(stem: str) -> str:
    """A journey name from a file name: "Login Flow_2" -> "login-flow-2"."""
    return re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-") or "flow"


def import_flow(path: Path, app: str | None = None) -> tuple[dict, list[str]]:
    """A draft journey (as a dict to write) from one Maestro flow file, and what was left out."""
    docs = [d for d in yaml.safe_load_all(path.read_text()) if d is not None]
    if not docs or not isinstance(docs[-1], list):
        raise ActionError(f"{path.name}: not a Maestro flow (no command list)")
    commands = docs[-1]
    head = docs[0] if isinstance(docs[0], dict) else {}
    early: list[str] = []
    if app and head.get("appId") not in (None, app):
        early.append(f"{path.name}: the flow is for {head['appId']}, the pack is for {app}; check the draft")
    app = app or head.get("appId")
    for key in head:
        if key not in ("appId", "name"):
            early.append(f"{path.name}: header {key} has no neutral form; left out (check the draft)")
    notes, steps, actions = early, [], []
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
        na, ns = len(actions), len(steps)
        try:
            if name in ("launchApp", "stopApp"):
                opts = {"appId": arg} if isinstance(arg, str) else arg if isinstance(arg, dict) else {}
                allowed = {"appId", "clearState"} if name == "launchApp" else {"appId"}
                if (arg is not None and not isinstance(arg, (str, dict))) or set(opts) - allowed \
                        or opts.get("appId") not in (None, app):
                    raise ActionError(f"{name} {arg!r}: only the pack's app"
                                      + (", with clearState" if name == "launchApp" else ""))
                clear = opts.get("clearState", False)
                if not isinstance(clear, bool):
                    raise ActionError(f"{name} clearState {clear!r} is not true or false")
                if name == "launchApp" and not steps and not actions:
                    start = {"launch": True, "clear_state": clear}
                else:
                    actions.append("stop" if name == "stopApp" else {"launch": {"clear_state": True}} if clear else "launch")
            elif name in ("assertVisible", "assertNotVisible"):
                flush()
                steps.append({"see" if name == "assertVisible" else "not_see": _neutral_sel(arg)})
            elif name == "tapOn":
                actions.append({"tap": _neutral_sel(arg)})
            elif name == "inputText":
                actions.append({"type": _plain(arg, "inputText")})
                notes.append(f"{path.name} command {i + 1}: typed text copied into the draft; if it is a password "
                             "or token, move it to env: and type ${NAME} (secrets never go in the pack)")
            elif name == "eraseText":
                n = arg.get("charactersToErase", 50) if isinstance(arg, dict) else (50 if arg is None else arg)
                actions.append({"erase": n})
            elif name == "pressKey":
                if str(arg).lower() not in KEYS:
                    raise ActionError(f"pressKey {arg}: not a neutral key ({', '.join(sorted(KEYS))})")
                actions.append({"press": str(arg).lower()})
            elif name == "scrollUntilVisible":
                if isinstance(arg, dict) and set(arg) - {"element", "direction"}:
                    raise ActionError(f"scrollUntilVisible keys {sorted(set(arg) - {'element', 'direction'})} "
                                      "have no neutral form")
                el = arg.get("element") if isinstance(arg, dict) else arg
                up = isinstance(arg, dict) and str(arg.get("direction", "DOWN")).upper() == "UP"
                sel = _neutral_sel(el)
                actions.append({"scroll_to": {"element": sel, "direction": "up"} if up else sel})
            elif name in ("back", "hideKeyboard", "scroll", "waitForAnimationToEnd") and arg is None:
                actions.append({"back": "back", "hideKeyboard": "hide_keyboard", "scroll": "scroll",
                                "waitForAnimationToEnd": "wait_idle"}[name])
            else:
                raise ActionError(f"{name or cmd!r} has no neutral word")
            if len(actions) > na:
                to_command(actions[-1])  # what import writes must validate; else it is listed, not kept
            if len(steps) > ns and next(iter(steps[-1])) in ("see", "not_see"):
                to_selector(next(iter(steps[-1].values())))
        except (ActionError, TypeError, ValueError) as e:
            if len(actions) > na:
                actions.pop()
            if len(steps) > ns and next(iter(steps[-1])) in ("see", "not_see"):
                steps.pop()
            notes.append(f"{path.name} command {i + 1}: {e}; left out")
    flush()
    if not steps:
        steps.append({"do": ["wait_idle"]})
        notes.append(f"{path.name}: no command could be imported; the draft holds a placeholder step")
    return {"name": slug(path.stem), "tags": ["imported"], "start": start, "steps": steps}, notes

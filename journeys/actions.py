"""The neutral words a pack uses for actions, and their translation to the drivers' commands.

A pack never names a test framework. Journeys' `do:` steps and the map's edges list actions:

    back | stop | hide_keyboard | wait_idle | scroll | launch
    {tap: SEL}  {type: "text"}  {erase: 10}  {press: enter}  {scroll_to: SEL}
    {scroll_to: {element: SEL, direction: up}}  {scroll: down}  {launch: {clear_state: true}}
    {see: SEL}  {not_see: SEL}

A selector (SEL) is "Exact text", "/a regex/" (matches the whole text), {id: name}, or a map of
text and/or id with an optional index (0 = the first match): {text: "Save", index: 1}.

Inside, drivers share one command form (flow commands: tapOn, inputText, ...); only this module
and the drivers know it.
"""
from __future__ import annotations

import re

_META = re.compile(r"([.^$*+?{}\[\]\\|()])")
KEYS = {"enter": "Enter", "back": "Back", "home": "Home", "tab": "Tab", "delete": "Backspace",
        "backspace": "Backspace", "search": "Search"}
BARE = {"back": "back", "stop": "stopApp", "hide_keyboard": "hideKeyboard", "wait_idle": "waitForAnimationToEnd",
        "scroll": "scroll", "launch": "launchApp"}
WORDS = sorted({*BARE, "tap", "type", "erase", "press", "scroll_to", "see", "not_see"})
# Framework words people bring from other tools, and the neutral word to use instead.
FOREIGN = {"tapOn": "tap", "inputText": "type", "eraseText": "erase", "pressKey": "press",
           "scrollUntilVisible": "scroll_to", "assertVisible": "see", "assertNotVisible": "not_see",
           "launchApp": "launch", "stopApp": "stop", "hideKeyboard": "hide_keyboard",
           "waitForAnimationToEnd": "wait_idle", "click": "tap", "sendKeys": "type"}


QUOTE = ("quote it: YAML reads 0123, 12:30, 1_000, Yes, No, On and Off as numbers, times or true/false, "
         'so write "0123", "12:30", "Yes"')


class ActionError(ValueError):
    pass


def escape(text: str) -> str:
    """Escape only regex characters (re.escape also escapes spaces, which some engines refuse)."""
    return _META.sub(r"\\\1", text)


def _text(t) -> str:
    if not isinstance(t, str):
        raise ActionError(f"selector text {t!r} is not a text: {QUOTE}")
    if t == "":
        raise ActionError("selector text must be a non-empty text")
    if len(t) > 1 and t.startswith("/") and t.endswith("/"):
        if len(t) == 2:
            raise ActionError("an empty regex // matches only empty text: write the text you want")
        try:
            re.compile(t[1:-1])
        except (re.error, OverflowError, RecursionError) as e:  # a count too big for re is not a crash
            raise ActionError(f"bad regex {t}: {e}") from None
        return "\\" + t[1:-1] if re.fullmatch(r"@\d+", t[1:-1]) else t[1:-1]  # never an element ref
    return "\\" + escape(t) if re.fullmatch(r"@\d+", t) else escape(t)  # "@3" is text, not a ref


def to_selector(sel):
    """Neutral selector -> the drivers' form (text is always a whole-text regex)."""
    if isinstance(sel, (str, int, float, bool)) or sel is None or type(sel).__name__ in ("date", "datetime"):
        return _text(sel)
    if isinstance(sel, dict) and sel and set(sel) <= {"text", "id", "index"} and ("text" in sel or "id" in sel):
        if "index" in sel and (type(sel["index"]) is not int or sel["index"] < 0):
            raise ActionError(f"index is a whole number from 0 (the first match), not {sel['index']!r}")
        if "id" in sel and (not isinstance(sel["id"], str) or not sel["id"]):
            raise ActionError(f"id is a non-empty text, not {sel['id']!r}")
        out = {k: v for k, v in sel.items() if k != "text"}
        if "text" in sel:
            out["text"] = _text(sel["text"])
        return out
    raise ActionError(f"a selector is \"text\", \"/regex/\", {{id: name}} or {{text, id, index}}, not {sel!r}")


def from_selector(sel):
    """The drivers' form -> neutral (plain text when the regex is only escaped text)."""
    def text(rx: str) -> str:
        literal = re.sub(r"\\(.)", r"\1", rx)
        if literal and not literal.startswith("/") and _text(literal) == rx:
            return literal
        return f"/{rx}/"
    if isinstance(sel, str):
        return text(sel)
    out = dict(sel)
    if "text" in out:
        out["text"] = text(str(out["text"]))
    return out


def to_command(action):
    if isinstance(action, str):
        if action in BARE:
            return BARE[action]
        raise ActionError(_unknown(action))
    if not (isinstance(action, dict) and len(action) == 1):
        raise ActionError(f"an action is one word or a one-key map, not {action!r}")
    word, arg = next(iter(action.items()))
    if word == "tap":
        return {"tapOn": to_selector(arg)}
    if word == "type":
        if not isinstance(arg, str) or arg == "":
            raise ActionError(f"type takes a non-empty text, not {arg!r}" + ("" if arg in ("", None) else f"; {QUOTE}"))
        return {"inputText": arg}
    if word == "erase":
        if not isinstance(arg, int) or isinstance(arg, bool) or not 1 <= arg <= 1000:
            raise ActionError(f"erase takes a number of characters from 1 to 1000, not {arg!r}")
        return {"eraseText": arg}
    if word == "press":
        if str(arg).lower() not in KEYS:
            raise ActionError(f"press takes one of {', '.join(sorted(KEYS))}, not {arg!r}")
        return {"pressKey": KEYS[str(arg).lower()]}
    if word == "scroll_to":
        opts = arg if isinstance(arg, dict) and "element" in arg else {"element": arg}
        if set(opts) - {"element", "direction"}:
            raise ActionError(f"scroll_to takes element and direction, not {sorted(set(opts) - {'element', 'direction'})}")
        direction = str(opts.get("direction", "down")).upper()
        if direction not in ("UP", "DOWN"):
            raise ActionError(f"scroll_to direction is up or down, not {opts.get('direction')!r}")
        return {"scrollUntilVisible": {"element": to_selector(opts["element"]), "direction": direction}}
    if word == "scroll":
        if str(arg).lower() != "down":
            raise ActionError(f"scroll goes down (use scroll_to with direction up to go up), not {arg!r}")
        return "scroll"
    if word in ("see", "not_see"):
        return {"assertVisible" if word == "see" else "assertNotVisible": to_selector(arg)}
    if word == "launch":
        if not isinstance(arg, dict) or set(arg) - {"clear_state"} or not isinstance(arg.get("clear_state", False), bool):
            raise ActionError(f"launch takes {{clear_state: true}} (true or false), not {arg!r}")
        return {"launchApp": {"clearState": True}} if arg.get("clear_state") else "launchApp"
    if word in BARE:
        raise ActionError(f"{word} takes no value: write it as a bare word, `{word}`")
    raise ActionError(_unknown(word))


def to_commands(actions) -> list:
    if not isinstance(actions, list):
        actions = [actions]
    if not actions:
        raise ActionError("do needs at least one action")
    return [to_command(a) for a in actions]


def _unknown(word: str) -> str:
    hint = f" (that is another tool's word: use {FOREIGN[word]})" if word in FOREIGN else ""
    return f"unknown action {word}{hint}; actions are {', '.join(WORDS)}"


def from_command(cmd):
    """The drivers' form -> neutral, for proposals the pack will keep."""
    if isinstance(cmd, str):
        return {v: k for k, v in BARE.items()}.get(cmd, cmd)
    (name, arg), = cmd.items()
    if name == "tapOn":
        return {"tap": from_selector(arg)}
    if name == "inputText":
        return {"type": arg}
    if name == "eraseText":
        return {"erase": arg}
    if name == "pressKey":
        return {"press": str(arg).lower()}
    if name in ("assertVisible", "assertNotVisible"):
        return {"see" if name == "assertVisible" else "not_see": from_selector(arg)}
    if name == "scrollUntilVisible":
        el = from_selector(arg["element"] if isinstance(arg, dict) else arg)
        down = not isinstance(arg, dict) or str(arg.get("direction", "DOWN")).upper() == "DOWN"
        return {"scroll_to": el if down else {"element": el, "direction": "up"}}
    if name == "launchApp":
        return {"launch": {"clear_state": True}} if isinstance(arg, dict) and arg.get("clearState") else "launch"
    raise ActionError(f"no neutral word for {name}")


def from_commands(cmds: list) -> list:
    return [from_command(c) for c in cmds]

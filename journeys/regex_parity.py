"""Patterns Journeys reads with Python's re, as the team's tool reads them: Java (Maestro) or JavaScript
(the Appium export, `new RegExp` with no flags).

check() refuses a pattern the engine cannot read or reads with another meaning, and returns notes for
parts that read the same on plain ASCII text but differently on other text. Each case was run on
Java 17, node and Python 3.12 (tests/test_regex_parity.py runs them again when java and node are there).
"""
from __future__ import annotations

import re

from .actions import ActionError

JAVA, JS = "Java", "JavaScript"
_COUNT = re.compile(r"\{\d+(,\d*)?\}")
_PY_ONLY_COUNT = re.compile(r"\{,\d*\}")  # Python reads {,3} as {0,3}
_NAMED = re.compile(r"\\N\{[^}]*\}")
_FLAGS = re.compile(r"\(\?([aiLmsux]*)(-[imsx]*)?[:)]")  # (?i) (?s:...) (?-i:...)


def check(rx: str, engine: str) -> list[str]:
    """Raise ActionError when `engine` cannot read `rx` as Python does; else the notes for it (maybe none)."""
    def refuse(what: str, hint: str):
        raise ActionError(f"/{rx}/: {engine} reads {what} differently from Journeys (Python); {hint}")

    notes: set[str] = set()
    i, n, quant, cls = 0, len(rx), False, None  # cls: index where the open [...] class started
    while i < n:
        c = rx[i]
        if c == "\\" and i + 1 < n:
            e = rx[i + 1]
            if e in "AZ" and (engine == JS or e == "Z"):
                refuse(f"\\{e}", "leave it out: a pattern always matches the whole text")
            if e == "0" and i + 2 < n and rx[i + 2].isdigit() and engine == JAVA:
                refuse("an octal escape \\0NN", "write the character as \\xNN")
            if e == "U" or (e == "N" and engine == JS) or (e == "a" and engine == JS):
                refuse(f"\\{e}", "write the character itself, or \\xNN / \\uNNNN")
            if e == "v" and engine == JAVA:
                refuse("\\v (any vertical space in Java, the vertical tab in Python)", "write \\x0b")
            if e in "dDwW" or (e in "bB" and cls is None and engine == JS):
                notes.add(f"\\{e} knows only ASCII {'digits' if e in 'dD' else 'letters and digits'} in {engine} "
                          "(Python: every language)")
            if e in "sS":
                notes.add(f"\\{e} knows {'only ASCII' if engine == JAVA else 'another set of'} space characters "
                          f"in {engine} (Python: every Unicode space)")
            named = _NAMED.match(rx, i)  # \N{DIGIT ONE}: one character, the { is not a count
            i, quant = named.end() if named else i + 2, False
            continue
        if cls is not None:  # inside [...]
            if c == "]" and i > cls + 1 + (rx[cls + 1:cls + 2] == "^"):
                cls = None
            elif c == "[" and engine == JAVA:
                refuse("[ inside a [...] class (a union in Java)", "escape it as \\[")
            elif c == "&" and rx.startswith("&&", i) and engine == JAVA:
                refuse("&& inside a [...] class (an intersection in Java)", "escape one of them as \\&")
            elif c == "]" and engine == JS:
                refuse("a ] first in a [...] class (an empty class in JavaScript)", "escape it as \\]")
            i += 1
            continue
        if c == "[":
            cls, quant = i, False
        elif c == "(" and rx.startswith("(?", i):
            k, flags = rx[i + 2:i + 3], _FLAGS.match(rx, i)
            if k and k in "P(#":
                refuse(f"(?{k}", "use (?:...) in place of a named group, comment or (?(1)...) choice")
            if k == ">" and engine == JS:
                refuse("(?>...) (no atomic groups in JavaScript)", "use (?:...)")
            flags = flags if flags and (flags.group(1) or flags.group(2)) else None  # (?:...) is a plain group
            if flags and engine == JS:
                refuse("(?i)-style flags (JavaScript has none inside a pattern)", "leave them out")
            if flags and "a" in flags.group(1):  # (?L) is for bytes patterns only: never a selector
                refuse("(?a)", "leave it out")
            if flags and "i" in flags.group(1):
                notes.add(f"(?i) folds only ASCII case in {engine} (Python: every language)")
            i, quant = i + 2, False
            continue
        elif c == "{":
            m = _COUNT.match(rx, i)
            if m:
                i, quant = m.end(), True
                continue
            if engine == JAVA or _PY_ONLY_COUNT.match(rx, i):
                refuse("a { that is not a count", "escape it as \\{")
        elif c in "*+?":
            if quant and c == "+" and engine == JS:
                refuse("a possessive quantifier (*+, ++, ?+)", "leave out the second +")
            quant = True  # a lazy ? or a possessive + after it ends the quantifier; Python refuses a third
            i += 1
            continue
        elif c == ".":
            notes.add(f". does not match \\r in {engine} (Python: it does)")
        quant = False
        i += 1
    return sorted(notes)


def _selector_values(cmd) -> list[tuple[str, str]]:
    """The (key, pattern) pairs of a shared command's selector: text and id."""
    if not isinstance(cmd, dict) or len(cmd) != 1:
        return []
    (name, arg), = cmd.items()
    if name == "scrollUntilVisible" and isinstance(arg, dict):
        arg = arg.get("element")
    elif name not in ("tapOn", "assertVisible", "assertNotVisible"):
        return []
    if isinstance(arg, str):
        return [("text", arg)]
    return [(k, v) for k, v in (arg or {}).items() if k in ("text", "id") and isinstance(v, str)]


def notes_for(cmds: list, engine: str, keys=("text", "id")) -> list[str]:
    """One export note per difference that shows on some text, naming the first pattern that has it.
    Raises ActionError (from check) for a pattern the engine reads differently: the export's refusal."""
    seen: dict[str, list[str]] = {}
    for c in cmds:
        for k, v in _selector_values(c):
            try:
                re.compile(v)
            except re.error:
                continue  # not a pattern: matched as exact text
            if k in keys:
                for n in check(v, engine):
                    ps = seen.setdefault(n, [])
                    if v not in ps:
                        ps.append(v)
    return [f"{n}: /{ps[0]}/" + (f" and {len(ps) - 1} more pattern(s)" if len(ps) > 1 else "")
            for n, ps in seen.items()]

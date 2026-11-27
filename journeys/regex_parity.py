"""Patterns Journeys reads with Python's re, checked for the team's tool: Java (Maestro: Kotlin Regex with
IGNORE_CASE, DOT_MATCHES_ALL and MULTILINE, which also turns on UNICODE_CASE, then "(?-ims)" in front) or
JavaScript (the Appium export, `new RegExp` with no flags).

Default-deny: check() accepts only a small shared subset, and refuses everything else. The contract: a
pattern it passes with no note matches the same texts in Python and the engine; a note names a
difference that shows only on some texts. tests/test_regex_parity.py proves the subset on Java 11, 17
and 25 and node 22 and 23: a table of every rule, and a fuzz of the subset's grammar.

The subset: any character except an emoji (above U+FFFF) next to a quantifier or in a class (JavaScript);
an escaped punctuation mark; \\t \\n \\r \\f, \\xHH and \\uHHHH; \\d \\D \\w \\W \\s \\S \\b \\B (noted); a [...] class
(no [ or && inside, no ] first); ( ), (?: ) and |; * + ? {n} {n,} {n,m} and their lazy forms; ^; $ (noted
before more of the pattern); . (noted); and one (?i) at the very start for Maestro (noted).
"""
from __future__ import annotations

import re

from .actions import ActionError

JAVA, JS = "Java", "JavaScript"
_COUNT = re.compile(r"\{([0-9]+)(,([0-9]*))?\}")  # ASCII digits: Python reads {٢} as text
_MAX_COUNT = 2**31 - 1  # Java refuses a larger count
_HEX = re.compile(r"x[0-9a-fA-F]{2}|u(?![dD][89a-fA-F])[0-9a-fA-F]{4}")  # \xHH, \uHHHH (not half a character)
_SHORTHAND = {"d": "digits", "D": "digits", "w": "letters and digits", "W": "letters and digits",
              "s": "space characters", "S": "space characters"}
ANY = r"[\s\S]"  # every character in all three engines (Journeys writes it around map rules)


def shown(rx: str) -> str:
    """The pattern on one line: control and line-break characters as escapes."""
    return re.sub(r"[\x00-\x1f\x7f\x85  ]",
                  lambda m: f"\\x{ord(m[0]):02x}" if ord(m[0]) < 256 else f"\\u{ord(m[0]):04x}", rx)


def check(rx: str, engine: str) -> list[str]:
    """Raise ActionError unless `rx` is in the shared subset; else the notes for it (maybe none)."""
    def refuse(what: str, hint: str = ""):
        raise ActionError(f"/{shown(rx)}/: {what} is not in the pattern subset Journeys can give {engine} "
                          f"unchanged (docs/packs.md, Patterns in Java and JavaScript)" + (f"; {hint}" if hint else ""))

    def astral_next(at: int) -> bool:  # an emoji followed by a quantifier
        return rx[at:at + 1] in ("*", "+", "?") or bool(_COUNT.match(rx, at))

    notes: set[str] = set()
    i, n, quant, cls = 0, len(rx), False, None  # quant: the last token was a quantifier
    if rx.startswith("(?i)"):
        if engine == JS:
            refuse("(?i)", "JavaScript reads no flags inside a pattern")
        notes.add("(?i) folds a few letters differently in Java, such as the Kelvin sign K "
                  "(Python: full Unicode case folding)")
        i = 4
    while i < n:
        c = rx[i]
        if cls is None and rx.startswith(ANY, i):  # [\s\S]: every character everywhere, no note
            i, quant = i + len(ANY), False
            continue
        if c == "\\":
            e = rx[i + 1:i + 2]
            if e in _SHORTHAND:
                notes.add(f"\\{e} knows only ASCII {_SHORTHAND[e]} in {engine} (Python: every language"
                          + (", and \\x1c-\\x1f as spaces)" if e in "sS" else ")"))
            elif e in ("b", "B") and cls is None:
                notes.add(f"\\{e} knows only ASCII letters and digits in "
                          f"{'Java 19 and later' if engine == JAVA else JS} (Python: every language)"
                          + (", and \\B matches an empty text" if e == "B" else ""))
            elif e in ("t", "n", "r", "f") or (e and not e.isalnum() and ord(e) < 128):
                pass  # a control escape, or an escaped ASCII punctuation mark or space
            elif not (e in ("x", "u") and _HEX.match(rx, i + 1)):  # its hex digits are plain characters after
                refuse(f"the escape \\{e}", "write the character itself, or \\xHH / \\uHHHH")
            i, quant = i + 2, False
            continue
        if cls is not None:  # inside [...]
            if ord(c) > 0xFFFF and engine == JS:
                refuse("an emoji inside a [...] class", "JavaScript reads it as two units; write (?:a|b)")
            if c == "]" and i == cls + 1 + (rx[cls + 1:cls + 2] == "^"):
                refuse("a ] first in a [...] class", "escape it as \\]")
            if c == "]":
                cls = None
            elif c == "[":
                refuse("a [ inside a [...] class", "escape it as \\[")
            elif c == "&" and rx.startswith("&&", i):
                refuse("&& inside a [...] class", "escape one of them as \\&")
            elif c == "^" and i == cls + 1 and engine == JS:
                notes.add("a [^...] class matches half of an emoji in JavaScript (Python: the whole one)")
            i += 1
            continue
        if ord(c) > 0xFFFF and engine == JS and astral_next(i + 1):
            refuse("a quantifier on an emoji", "JavaScript repeats only its second half; write (?:\U0001F600)+")
        if c == "[":
            cls, quant = i, False
        elif c == "(":
            if rx.startswith("(?", i) and not rx.startswith("(?:", i):
                refuse(f"the group {rx[i:i + 3]}...", "lookarounds, named groups and inline flags are not in it; "
                       "use ( ) or (?: )")
            i, quant = i + (3 if rx.startswith("(?:", i) else 1), False
            continue
        elif c == "$" and rx[i + 1:].strip(")"):
            notes.add("a $ before more of the pattern matches only at the very end in JavaScript and also before "
                      "a final \\r, \\x85, \\u2028 or \\u2029 in Java (Python: also before a final \\n)")
        elif c == ".":
            notes.add(f". does not match \\r, \\x85, \\u2028 or \\u2029 in {engine} (Python: it does)" if engine == JAVA
                      else ". does not match \\r, \\u2028, \\u2029 or a whole emoji in JavaScript (Python: it does)")
        elif c == "{":
            m = _COUNT.match(rx, i)
            if not m:
                refuse("a { that is not a count", "escape it as \\{")
            if max(int(x) for x in (m[1], m[3]) if x) > _MAX_COUNT:
                refuse("a count above 2147483647")
            i, quant = m.end(), True
            continue
        elif c in "*+?":
            if quant and c == "+":
                refuse("a possessive quantifier (*+, ++, ?+)", "leave out the second +")
            quant = True  # a lazy ? after it is fine; Python refuses a third
            i += 1
            continue
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
    Raises ActionError (from check) for a pattern outside the subset: the export's refusal."""
    seen: dict[str, list[str]] = {}
    for c in cmds:
        for k, v in _selector_values(c):
            try:
                re.compile(v)
            except re.error:
                continue  # not a pattern: matched as exact text
            if k in keys:
                for n in check(v, engine):
                    if k == "id" and n.startswith(". "):
                        continue  # a dot in an id is the package's, and an id never holds a line break
                    ps = seen.setdefault(n, [])
                    if v not in ps:
                        ps.append(v)
    return [f"{n}: /{shown(ps[0])}/" + (f" and {len(ps) - 1} more pattern(s)" if len(ps) > 1 else "")
            for n, ps in seen.items()]

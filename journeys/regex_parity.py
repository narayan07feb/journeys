"""Patterns Journeys reads with Python's re, checked for the team's tool: Java (Maestro: Kotlin Regex with
IGNORE_CASE, DOT_MATCHES_ALL and MULTILINE, which also turns on UNICODE_CASE, then "(?-ims)" in front) or
JavaScript (the Appium export, `new RegExp` with no flags).

Default-deny: check() accepts only a small shared subset, and refuses everything else. The contract: a
pattern it passes with no note matches the same texts in Python and the engine; a note names a
difference that shows only on some texts. tests/test_regex_parity.py proves the subset on Java 11, 17
and 25 and node 22 and 23: a table of every rule, and a sampled fuzz of the subset's grammar.
docs/packs.md (Patterns in Java and JavaScript) lists the subset.
"""
from __future__ import annotations

import re

from .actions import ActionError

JAVA, JS = "Java", "JavaScript"
_COUNT = re.compile(r"\{([0-9]+)(,([0-9]*))?\}")  # ASCII digits: Python reads {٢} as text
_MAX_COUNT = 2**31 - 1  # Java refuses a larger count
_HEX = re.compile(r"x[0-9a-fA-F]{2}|u(?![dD][89a-fA-F])[0-9a-fA-F]{4}")  # \xHH, \uHHHH (not half a character)
_SHORTHAND = {"d": "digits", "D": "digits", "w": "letters and digits", "W": "letters and digits"}
_CONTROL = {"t": "\t", "n": "\n", "r": "\r", "f": "\f"}
ANY = r"[\s\S]"  # every character in all three engines; Journeys writes [\s\S]* around map rules
_HALF = ("a {what} can match half of an emoji in JavaScript (Python: only the whole one)")
_GROUP_HINTS = {"=": "lookarounds cannot be sent; write the text without them", "!": "lookarounds cannot be sent",
                "<": "lookarounds and named groups cannot be sent; use ( ) for a group",
                "P": "named groups cannot be sent; use ( )"}


def shown(rx: str) -> str:
    """The pattern on one line: control and line-break characters as escapes."""
    return re.sub(r"[\x00-\x1f\x7f\x85  ]",
                  lambda m: f"\\x{ord(m[0]):02x}" if ord(m[0]) < 256 else f"\\u{ord(m[0]):04x}", rx)


def _class_ranges(rx: str, start: int) -> tuple[list[tuple[int, int]], set[str], int]:
    """The [...] class at `start`: its (low, high) code point ranges, its shorthand escapes, and its end."""
    i = start + 1 + (rx[start + 1:start + 2] == "^")
    singles, shorts, first = [], set(), True
    while i < len(rx) and (rx[i] != "]" or first):
        first = False
        if rx[i] == "\\":
            e = rx[i + 1]
            hexed = _HEX.match(rx, i + 1)
            if hexed:
                singles.append(int(hexed[0][1:], 16))
                i += 1 + len(hexed[0])
                continue
            if e.isalpha() and e not in _CONTROL:
                shorts.add(e)
                singles.append(None)
            else:
                singles.append(ord(_CONTROL.get(e, e)))
            i += 2
        else:
            singles.append(ord(rx[i]) if rx[i] != "-" else -1)
            i += 1
    ranges, k = [], 0
    while k < len(singles):
        if k + 2 < len(singles) and singles[k + 1] == -1 and None not in (singles[k], singles[k + 2]) \
                and singles[k] != -1 and singles[k + 2] != -1:
            ranges.append((singles[k], singles[k + 2]))
            k += 3
        else:
            if singles[k] is not None and singles[k] != -1:
                ranges.append((singles[k], singles[k]))
            k += 1
    return ranges, shorts, i


def check(rx: str, engine: str) -> list[str]:
    """Raise ActionError unless `rx` is in the shared subset; else the notes for it (maybe none)."""
    def refuse(what: str, hint: str = ""):
        raise ActionError(f"/{shown(rx)}/: {what} is not in the pattern subset Journeys can give {engine} "
                          f"unchanged (docs/packs.md, Patterns in Java and JavaScript)" + (f"; {hint}" if hint else ""))

    try:
        re.compile(rx)
    except (re.error, OverflowError, RecursionError) as e:
        refuse(f"a pattern Python cannot read ({e})", "escape its special characters to match the text itself")
    if any(0xD800 <= ord(c) <= 0xDFFF for c in rx):
        refuse("a lone \\uD800-\\uDFFF half of a character", "write the whole character")
    notes: set[str] = set()
    i, n, quant, cls, depth = 0, len(rx), False, None, 0  # quant: the last token was a quantifier
    alts = [False]  # per open group (and the top): a | inside it; after_alt: the group just closed had one
    after_alt = False
    if rx.startswith("(?i)"):
        if engine == JS:
            refuse("(?i)", "JavaScript reads no flags inside a pattern")
        notes.add("(?i) folds a few letters differently in Java: [A-Z] matches the Kelvin sign K in Python, "
                  "not in Java")
        i = 4
    begin = i
    while i < n:
        c = rx[i]
        count = _COUNT.match(rx, i) if c == "{" else None
        if after_alt and engine == JAVA and (c in "*+" or (count and (count[2] == "," or int(count[3] or count[1])
                                                                        >= 1000))):
            notes.add("a repeated group with | can overflow Java's stack on a text of a few thousand characters "
                      "(Python: fine); for single characters use a class, such as [ab]* for (?:a|b)*")
        if not (c in "*+?{" and after_alt):
            after_alt = False
        if cls is None and rx.startswith(ANY + "*", i):  # every character, any number, in all three: no note
            i, quant = i + len(ANY) + 1, True
            continue
        if c == "\\":
            e = rx[i + 1:i + 2]
            if e in _SHORTHAND:
                notes.add(f"\\{e} knows only ASCII {_SHORTHAND[e]} in {engine} (Python: every language)")
                if cls is not None and e.isupper() and engine == JS:
                    notes.add(_HALF.format(what=f"[...\\{e}...] class"))
            elif e in ("s", "S"):
                notes.add(f"\\{e} knows only ASCII spaces in Java (Python: Unicode spaces, and \\x1c-\\x1f)"
                          if engine == JAVA else
                          f"\\{e} knows another set of spaces in JavaScript, such as \\ufeff (Python: Unicode spaces "
                          "and \\x1c-\\x1f, not \\ufeff)")
                if cls is not None and e == "S" and engine == JS:
                    notes.add(_HALF.format(what="[...\\S...] class"))
            elif e in ("b", "B") and cls is None:
                notes.add(f"\\{e} knows only ASCII letters and digits in "
                          f"{'Java 19 and later' if engine == JAVA else JS} (Python: every language)"
                          + (", and \\B matches an empty text" if e == "B" else ""))
            elif e in _CONTROL or (e and not e.isalnum() and ord(e) < 128):
                pass  # a control escape, or an escaped ASCII punctuation mark or space
            elif not (e in ("x", "u") and _HEX.match(rx, i + 1)):  # its hex digits are plain characters after
                refuse(f"the escape \\{e}", "write the character itself, or \\xHH / \\uHHHH")
            i, quant = i + 2, False
            continue
        if cls is not None:  # inside [...]
            if ord(c) > 0xFFFF and engine == JS:
                refuse("an emoji inside a [...] class", "JavaScript reads it as two units; write (?:a|b) for a choice")
            if c == "]" and i == cls + 1 + (rx[cls + 1:cls + 2] == "^"):
                refuse("a ] first in a [...] class", "escape it as \\]")
            if c == "]":
                cls = None
            elif c == "[":
                refuse("a [ inside a [...] class", "escape it as \\[")
            elif c == "&" and rx.startswith("&&", i):
                refuse("&& inside a [...] class", "escape one of them as \\&")
            elif c == "^" and i == cls + 1 and engine == JS:
                notes.add(_HALF.format(what="[^...] class"))
            i += 1
            continue
        if ord(c) > 0xFFFF and engine == JS and (rx[i + 1:i + 2] in ("*", "+", "?") or _COUNT.match(rx, i + 1)):
            q = _COUNT.match(rx, i + 1)[0] if _COUNT.match(rx, i + 1) else rx[i + 1]
            refuse("a quantifier on an emoji", f"JavaScript repeats only its second half; write (?:{c}){q}")
        if c == "[":
            if engine == JS and any(lo <= 0xDFFF and hi >= 0xD800 for lo, hi in _class_ranges(rx, i)[0]):
                refuse("a [...] range across U+D800-U+DFFF", "JavaScript then matches halves of emoji; stop the "
                       "range at \\ud7ff and start again at \\ue000")
            cls, quant = i, False
        elif c == "(":
            if rx.startswith("(?", i) and not rx.startswith("(?:", i):
                k = rx[i + 2:i + 3]
                refuse(f"the group {rx[i:i + 3]}...", _GROUP_HINTS.get(k, "inline flags cannot be sent; for (?s) "
                       "write [\\s\\S] in place of . to cross line breaks"))
            depth += 1
            alts.append(False)
            i, quant = i + (3 if rx.startswith("(?:", i) else 1), False
            continue
        elif c == ")":
            depth -= 1
            after_alt = alts.pop()
            alts[-1] |= after_alt
        elif c == "|":
            alts[-1] = True
        elif c == "^" and not (i == begin or (depth == 0 and rx[i - 1] == "|")):
            refuse("a ^ inside the pattern", "leave it out: a pattern always matches the whole text")
        elif c == "$" and not (rx[i + 1:].strip(")") == "" or (depth == 0 and rx[i + 1:i + 2] == "|")):
            refuse("a $ inside the pattern", "leave it out: a pattern always matches the whole text")
        elif c == ".":
            notes.add(f". does not match \\r, \\x85, \\u2028 or \\u2029 in {engine} (Python: it does)" if engine == JAVA
                      else ". does not match \\r, \\u2028, \\u2029 or a whole emoji in JavaScript (Python: it does)")
        elif c == "{":
            m = _COUNT.match(rx, i)
            if not m:
                refuse("a { that is not a count", "write {0,n} for up to n" if re.match(r"\{,[0-9]", rx[i:])
                       else "escape it as \\{")
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
    Raises ActionError (from check) for a pattern outside the subset: the export's refusal. A text that is
    not a pattern (Python cannot read it) is matched as exact text and passes."""
    seen: dict[str, list[str]] = {}
    for c in cmds:
        for k, v in _selector_values(c):
            if k not in keys:
                continue
            try:
                re.compile(v)
            except (re.error, OverflowError, RecursionError):
                continue
            for note in check(v, engine):
                if k == "id" and note.startswith(". "):
                    continue  # a dot in an id is the package's, and an id never holds a line break
                ps = seen.setdefault(note, [])
                if v not in ps:
                    ps.append(v)
    return [f"{note}: /{shown(ps[0])}/" + (f" and {len(ps) - 1} more pattern(s)" if len(ps) > 1 else "")
            for note, ps in seen.items()]

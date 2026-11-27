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
# Java and JavaScript recurse on groups: more groups, deeper nesting, or a large count on a group (times the
# counts of the groups inside it) can overflow their stack. Plain characters cost nothing: [0-9]{999} is fine.
_MAX_GROUPS, _MAX_DEPTH, _MAX_GROUP_COUNT = 100, 10, 100
_HEX = re.compile(r"x[0-9a-fA-F]{2}|u(?![dD][89a-fA-F])[0-9a-fA-F]{4}")  # \xHH, \uHHHH (not half a character)
_SHORTHAND = {"d": "digits", "D": "digits", "w": "letters and digits", "W": "letters and digits"}
_CONTROL = {"t": "\t", "n": "\n", "r": "\r", "f": "\f"}
ANY = r"[\s\S]"  # every character in all three engines; Journeys writes [\s\S]* around map rules
_HALF = ("a {what} can match half of an emoji in JavaScript (Python: only the whole one)")
_GROUP_HINTS = {"=": "lookarounds cannot be sent; write the text without them", "!": "lookarounds cannot be sent",
                "<": "lookarounds and named groups cannot be sent; use ( ) for a group",
                "P": "named groups cannot be sent; use ( )", "#": "comments cannot be sent; leave the comment out",
                ">": "atomic groups cannot be sent; use (?: )"}


def shown(rx: str) -> str:
    """The pattern on one line: control and line-break characters as escapes."""
    return re.sub(r"[\x00-\x1f\x7f\x85  ]",
                  lambda m: f"\\x{ord(m[0]):02x}" if ord(m[0]) < 256 else f"\\u{ord(m[0]):04x}", rx)


def _shorthand_note(e: str, engine: str, in_class: bool = False) -> str:
    """The note for \\d \\D \\w \\W \\s \\S (in a class, the class's own note covers the emoji half)."""
    half = ", and matches only half of an emoji in JavaScript" if engine == JS and e in "DWS" and not in_class else ""
    if e in _SHORTHAND:
        return f"\\{e} knows only ASCII {_SHORTHAND[e]} in {engine} (Python: every language){half}"
    if engine == JAVA:
        return f"\\{e} knows only ASCII spaces in Java (Python: Unicode spaces, and \\x1c-\\x1f)"
    return (f"\\{e} knows another set of spaces in JavaScript, such as \\ufeff (Python: Unicode spaces and "
            f"\\x1c-\\x1f, not \\ufeff){half}")


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
            singles.append(-1 if rx[i] == "-" else ord(rx[i]))  # -1: an unescaped -, a range or a literal
            i += 1
    ranges, k = [], 0
    while k < len(singles):
        lo = 0x2D if singles[k] == -1 else singles[k]  # a - where no range can start is the character -
        if k + 2 < len(singles) and singles[k + 1] == -1 and lo is not None and singles[k + 2] is not None:
            ranges.append((lo, 0x2D if singles[k + 2] == -1 else singles[k + 2]))
            k += 3
            continue
        if lo is not None:
            ranges.append((lo, lo))
        k += 1
    return ranges, shorts, i


def check(rx: str, engine: str) -> list[str]:
    """Raise ActionError unless `rx` is in the shared subset; else the notes for it (maybe none)."""
    def refuse(what: str, hint: str = ""):
        raise ActionError(f"/{shown(rx)}/: {what} is not in the pattern subset Journeys can give {engine} "
                          f"unchanged (docs/packs.md, Patterns in Java and JavaScript)" + (f"; {hint}" if hint else ""))

    try:
        re.compile(rx)
    except (re.error, OverflowError, RecursionError, ValueError) as e:
        refuse(f"a pattern Python cannot read ({e})", "escape its special characters to match the text itself")
    if any(0xD800 <= ord(c) <= 0xDFFF for c in rx):
        refuse("a lone \\uD800-\\uDFFF half of a character", "write the whole character")
    notes: set[str] = set()
    i, n, quant, cls, depth = 0, len(rx), False, None, 0  # quant: the last token was a quantifier
    choice = [False]  # per open group (and the top): a | or a variable-count quantifier inside it
    after_choice = False  # the group just closed has one
    weight = [1]  # per open group: the largest product of group counts inside it
    after_weight, groups = 0, 0  # the group just closed (0: the last token was not a group); groups so far
    if rx.startswith("(?i)"):
        if engine == JS:
            refuse("(?i)", "JavaScript reads no flags inside a pattern; write both cases, as [Ss]ave")
        if any(ord(ch) > 127 and ch.isalpha() for ch in rx):  # ß, ẞ, ı, İ and more fold another way in Java
            refuse("(?i) with a letter outside ASCII", "Java folds some of those letters another way; write the "
                   "cases you mean, as [éÉ]")
        notes.add("(?i) folds some letters outside ASCII another way in Java: [A-Z] matches the Kelvin sign and "
                  "\\u0130 in Python, not in Java")
        i = 4
    begin = i
    in_class: dict[str, str] = {}  # the open class's shorthand letters, and their notes (merged at its end)
    class_half: set[str] = set()  # the open class's half-emoji notes (merged at its end)
    while i < n:
        c = rx[i]
        count = _COUNT.match(rx, i) if c == "{" else None
        if after_choice and engine == JAVA and (c in "*+" or (count and (count[2] == "," or int(count[3] or count[1])
                                                                           >= 1000))):
            notes.add("a repeated group with a | or an optional part inside can overflow Java's stack on a text of "
                      "a few thousand characters (Python: fine); for single characters use a class, such as [ab]* "
                      "for (?:a|b)*")
        if not (c in "*+?{" and after_choice):
            after_choice = False
        if not (c in "*+?{" and after_weight):
            after_weight = 0
        if cls is None and rx.startswith(ANY + "*", i):  # every character, any number, in all three: no note
            i, quant = i + len(ANY) + 1, True
            continue
        if c == "\\":
            e = rx[i + 1:i + 2]
            if e in "dDwWsS" and cls is None:
                notes.add(_shorthand_note(e, engine))
            elif e in "dDwWsS":
                in_class[e] = _shorthand_note(e, engine, in_class=True)
                if e in "DWS" and engine == JS:
                    class_half.add(_HALF.format(what=f"[...\\{e}...] class"))
            elif e in ("b", "B") and cls is None:
                notes.add(f"\\{e} knows only ASCII letters and digits in "
                          f"{engine} (Python: every language" + ("; more text from Java 19)" if engine == JAVA else ")")
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
                pair = any(x.swapcase() in in_class for x in in_class)  # [\s\S]: every character
                notes |= {t for x, t in in_class.items() if x.swapcase() not in in_class}
                if not (pair and rx[cls + 1:cls + 2] == "^"):  # [^\s\S] matches nothing: no half either
                    notes |= class_half
                cls, in_class, class_half = None, {}, set()
            elif c == "[":
                refuse("a [ inside a [...] class", "escape it as \\[")
            elif c == "&" and rx.startswith("&&", i):
                refuse("&& inside a [...] class", "escape one of them as \\&")
            elif c == "^" and i == cls + 1 and engine == JS:
                class_half.add(_HALF.format(what="[^...] class"))
            i += 1
            continue
        if ord(c) > 0xFFFF and engine == JS and (rx[i + 1:i + 2] in ("*", "+", "?") or _COUNT.match(rx, i + 1)):
            q = _COUNT.match(rx, i + 1)[0] if _COUNT.match(rx, i + 1) else rx[i + 1]
            refuse("a quantifier on an emoji", f"JavaScript repeats only its second half; write (?:{c}){q}")
        if c == "[":
            if engine == JS and any(lo <= 0xDFFF and hi >= 0xD800 for lo, hi in _class_ranges(rx, i)[0]):
                refuse("a [...] range across U+D800-U+DFFF", "JavaScript then matches halves of emoji; " + (
                       "list in a [^...] class only the characters it must not match" if rx[i + 1:i + 2] == "^" else
                       "stop the range at \\ud7ff and start again at \\ue000"))
            cls, quant = i, False
        elif c == "(":
            if rx.startswith("(?", i) and not rx.startswith("(?:", i):
                k = rx[i + 2:i + 3]
                refuse(f"the group {rx[i:i + 3]}...", _GROUP_HINTS.get(k, "inline flags cannot be sent: for (?s) "
                       "write [\\s\\S] in place of . to cross line breaks; for (?i) write both cases, as [Ss]ave"))
            depth, groups = depth + 1, groups + 1
            if depth > _MAX_DEPTH or groups > _MAX_GROUPS:
                refuse(f"more than {_MAX_GROUPS} groups, or groups nested more than {_MAX_DEPTH} deep",
                       "Java and JavaScript can overflow their stack on it; split the check into simpler patterns")
            choice.append(False)
            weight.append(1)
            i, quant = i + (3 if rx.startswith("(?:", i) else 1), False
            continue
        elif c == ")":
            depth -= 1
            after_choice = choice.pop()
            choice[-1] |= after_choice
            after_weight = weight.pop()
            weight[-1] = max(weight[-1], after_weight)
        elif c == "|":
            choice[-1] = True
        elif c == "^" and not (i == begin or (depth == 0 and rx[i - 1] == "|")):
            refuse("a ^ inside the pattern", "^ can stand only at the start of the pattern or of a top-level branch")
        elif c == "$" and not (rx[i + 1:].strip(")") == "" or (depth == 0 and rx[i + 1:i + 2] == "|")):
            refuse("a $ inside the pattern", "$ can stand only at the end of the pattern or of a top-level branch")
        elif c == ".":
            notes.add(f". does not match \\r, \\x85, \\u2028 or \\u2029 in {engine} (Python: it does)" if engine == JAVA
                      else ". does not match \\r, \\u2028, \\u2029 or a whole emoji in JavaScript (Python: it does)")
        elif c == "{":
            m = _COUNT.match(rx, i)
            if not m:
                refuse("a { that is not a count", "write {0,n} for up to n, or {0,} for any number" if re.match(r"\{,[0-9]*\}", rx[i:])
                       else "escape it as \\{")
            if max(int(x) for x in (m[1], m[3]) if x) > _MAX_COUNT:
                refuse("a count above 2147483647")
            choice[-1] |= m[2] is not None and m[1] != m[3]  # {n,} or {n,m}: a choice; {n} and {n,n}: none
            if after_weight and int(m[3] or m[1]) * after_weight > _MAX_GROUP_COUNT:
                refuse(f"a count on a group above {_MAX_GROUP_COUNT} (with the counts of the groups inside it)",
                       "Java can overflow its stack on it; repeat a single character or class instead, or a "
                       "smaller count")
            if after_weight:
                weight[-1] = max(weight[-1], int(m[3] or m[1]) * after_weight)
            i, quant = m.end(), True
            continue
        elif c in "*+?":
            if quant and c == "+":
                refuse("a possessive quantifier (*+, ++, ?+)", "leave out the second +")
            quant = True  # a lazy ? after it is fine; Python refuses a third
            choice[-1] = True
            i += 1
            continue
        quant = False
        i += 1
    return sorted(notes)


def _selector_texts(cmd) -> list[str]:
    """The text patterns of a shared command's selector (an id goes as plain text in both exports)."""
    if not isinstance(cmd, dict) or len(cmd) != 1:
        return []
    (name, arg), = cmd.items()
    if name == "scrollUntilVisible" and isinstance(arg, dict):
        arg = arg.get("element")
    elif name not in ("tapOn", "assertVisible", "assertNotVisible"):
        return []
    if isinstance(arg, str):
        return [arg]
    return [arg["text"]] if isinstance(arg, dict) and isinstance(arg.get("text"), str) else []


def notes_for(cmds: list, engine: str) -> list[str]:
    """One export note per difference that shows on some text, naming the first pattern that has it.
    Raises ActionError (from check) for a pattern outside the subset: the export's refusal. A text that is
    not a pattern (Python cannot read it) is matched as exact text and passes."""
    seen: dict[str, list[str]] = {}
    for c in cmds:
        for v in _selector_texts(c):
            try:
                re.compile(v)
            except (re.error, OverflowError, RecursionError):
                continue
            for note in check(v, engine):
                ps = seen.setdefault(note, [])
                if v not in ps:
                    ps.append(v)
    return [f"{note}: /{shown(ps[0])}/" + (f" and {len(ps) - 1} more pattern(s)" if len(ps) > 1 else "")
            for note, ps in seen.items()]

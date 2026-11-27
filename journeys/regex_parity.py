"""Patterns Journeys reads with Python's re, as the team's tool reads them: Java (Maestro: Kotlin Regex with
IGNORE_CASE, DOT_MATCHES_ALL and MULTILINE, which also turns on UNICODE_CASE, then "(?-ims)" in front) or
JavaScript (the Appium export, `new RegExp` with no flags).

check() refuses a pattern the engine cannot read or reads with another meaning, and returns notes for
parts that read the same on most text but not on all. Each case was run on Java 11, 17 and 25, node 22
and 23 and Python 3.12 (tests/test_regex_parity.py runs them again on the java and node it finds).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .actions import ActionError

JAVA, JS = "Java", "JavaScript"
_COUNT = re.compile(r"\{[0-9]+(,[0-9]*)?\}")  # [0-9]: Python reads {٢} as text, not a count
_PY_ONLY_COUNT = re.compile(r"\{,[0-9]*\}")  # Python reads {,3} as {0,3}
_ZERO_OK = re.compile(r"[?*]|\{0*(,|\})")  # a quantifier that allows none
_NAMED = re.compile(r"\\N\{[^}]*\}")
# Python reads \0101 as \010 then "1" (Java: \0101 is "A"), and \101 as "A" (Java: group 1, then "01")
_OCTAL3 = re.compile(r"\\(0[0-3][0-7]{2}|[1-3][0-7]{2})")
_FLAGS = re.compile(r"\(\?([aiLmsux]*)(-[imsx]*)?[:)]")  # (?i) (?s:...) (?-i:...)
_JAVA_MAX = 2**31 - 1  # Java refuses a larger count


def shown(rx: str) -> str:
    """The pattern on one line: control and line-break characters as escapes."""
    return re.sub(r"[\x00-\x1f\x7f\x85\u2028\u2029]",
                  lambda m: f"\\x{ord(m[0]):02x}" if ord(m[0]) < 256 else f"\\u{ord(m[0]):04x}", rx)


@dataclass
class _Group:
    start: int
    depth: int  # groups open around it (-1: the whole pattern)
    parent: "_Group | None"
    capture: bool = False
    behind: bool = False  # a lookbehind
    negative: bool = False  # (?! or (?<!
    dotall: bool = False  # (?s:...)
    end: int = -1
    alts: list[int] = field(default_factory=list)  # each | directly inside it


def check(rx: str, engine: str) -> list[str]:
    """Raise ActionError when `engine` cannot read `rx` as Python does; else the notes for it (maybe none)."""
    def refuse(what: str, hint: str):
        raise ActionError(f"/{shown(rx)}/: {engine} reads {what} differently from Journeys (Python); {hint}")

    def astral(in_class: bool, after: int):
        if engine == JS and in_class:
            refuse("a character above U+FFFF (an emoji) in a [...] class (two units in JavaScript)",
                   "leave the class out, or write (?:a|b) for a choice of emoji")
        if engine == JS and (rx[after:after + 1] in tuple("*+?") or _COUNT.match(rx, after)):
            notes.add("a quantifier on a character above U+FFFF (an emoji) repeats only its second half in "
                      "JavaScript (Python: the whole character)")

    notes: set[str] = set()
    top = _Group(0, -1, None)  # the whole pattern, for a | at the top
    stack: list[_Group] = []
    groups: list[_Group] = []  # the capturing groups, by number - 1
    backrefs: list[int] = []
    i, n, quant, cls, behind = 0, len(rx), False, None, False  # cls: where the open [...] class started
    dotall = verbose = class_space = neg_capture = False
    while i < n:
        c = rx[i]
        if behind and engine == JS and (c in "*+?" or _COUNT.match(rx, i)):
            refuse("a quantifier on a lookbehind (an error in JavaScript)", "leave the quantifier out")
        behind = False
        if c == "\\" and i + 1 < n:
            e, rest = rx[i + 1], rx[i + 2:]
            if ord(e) > 0xFFFF:
                astral(cls is not None, i + 2)
            if (e in "Az" and engine == JS) or (e == "Z" and (engine == JS or rest.strip(")"))):
                refuse(f"\\{e}", "nothing can follow Python's \\Z (the very end); remove what follows it"
                       if e == "Z" and rest.strip(")") else "leave it out: a pattern always matches the whole text")
            if engine == JAVA and (_OCTAL3.match(rx, i) or (e == "0" and rest[:1] not in tuple("01234567"))):
                refuse("an octal escape such as \\0 or \\101", "write the character as \\xNN")
            if e in "UN" or (e == "a" and engine == JS):
                refuse(f"\\{e}", "write the character itself, or \\xNN / \\uNNNN")
            if e == "v" and engine == JAVA:
                refuse("\\v (any vertical space in Java, the vertical tab in Python)", "write \\x0b")
            if e == "u" and re.match(r"[dD][89a-fA-F]", rest):
                refuse("a \\uD800-\\uDFFF half of a character (Java and JavaScript join two into one emoji)",
                       "write the character itself")
            if e == "b" and cls is not None and engine == JAVA:
                refuse("[\\b] (an error in Java; a backspace in Python)", "write \\x08")
            if e in "123456789" and cls is not None and engine == JAVA:
                refuse("a digit escape inside a [...] class (an error in Java)", "write the character as \\xNN")
            if e in "123456789" and cls is None and not _OCTAL3.match(rx, i):
                if engine == JAVA and any(g.behind for g in stack):
                    refuse("a back-reference inside a lookbehind (an error in Java)", "write the text again")
                two = rest[:1].isdigit() and int(e + rest[0]) <= len(groups)
                backrefs.append(int(e + rest[0]) if two else int(e))
            if e in "dDwW":
                notes.add(f"\\{e} knows only ASCII {'digits' if e in 'dD' else 'letters and digits'} in {engine} "
                          "(Python: every language)")
            if e in "bB" and cls is None:
                notes.add(f"\\{e} knows only ASCII letters and digits in "
                          f"{'Java 19 and later' if engine == JAVA else JS} (Python: every language)"
                          + (", and \\B matches an empty text" if e == "B" else ""))
            if e in "sS":
                notes.add(f"\\{e} knows {'only ASCII' if engine == JAVA else 'another set of'} space characters "
                          f"in {engine} (Python: every Unicode space, and \\x1c-\\x1f)")
            named = _NAMED.match(rx, i)  # \N{DIGIT ONE}: one character, the { is not a count
            i, quant = named.end() if named else i + 2, False
            continue
        if cls is not None:  # inside [...]
            if ord(c) > 0xFFFF:
                astral(True, i + 1)
            class_space |= c in " \t\n#"
            if c == "]" and i > cls + 1 + (rx[cls + 1:cls + 2] == "^"):
                cls = None
            elif c == "^" and i == cls + 1 and engine == JS:
                notes.add("a [^...] class matches half of an emoji in JavaScript (Python: the whole one)")
            elif c == "[" and engine == JAVA:
                refuse("[ inside a [...] class (a union in Java)", "escape it as \\[")
            elif c == "&" and rx.startswith("&&", i) and engine == JAVA:
                refuse("&& inside a [...] class (an intersection in Java)", "escape one of them as \\&")
            elif c == "]" and engine == JS:
                refuse("a ] first in a [...] class (an empty class in JavaScript)", "escape it as \\]")
            i += 1
            continue
        if ord(c) > 0xFFFF:
            astral(False, i + 1)
        if c == "[":
            cls, quant = i, False
            i += 1
            continue
        if c == "(":
            g = _Group(i, len(stack), stack[-1] if stack else top)
            if rx.startswith("(?", i):
                k, flags = rx[i + 2:i + 3], _FLAGS.match(rx, i)
                if k and k in "P(#":
                    refuse(f"(?{k}", "use (?:...) in place of a named group, comment or (?(1)...) choice")
                if k == ">" and engine == JS:
                    refuse("(?>...) (no atomic groups in JavaScript)", "use (?:...)")
                g.behind, g.negative = rx.startswith(("(?<=", "(?<!"), i), rx.startswith(("(?!", "(?<!"), i)
                on = flags.group(1) if flags and (flags.group(1) or flags.group(2)) else None
                if on is not None:
                    if engine == JS:
                        refuse("(?i)-style flags (JavaScript has none inside a pattern)", "leave them out")
                    if "a" in on:  # (?L) is for bytes patterns only: never a selector
                        refuse("(?a)", "leave it out")
                    verbose |= "x" in on
                    if "i" in on:
                        notes.add("(?i) folds a few letters differently in Java, such as the Kelvin sign \u212a "
                                  "(Python: full Unicode case folding)")
                    if "m" in on:
                        notes.add("(?m) ^ and $ also break lines at \\r, \\x85, \\u2028 and \\u2029 in Java "
                                  "(Python: only \\n)")
                    scoped = flags.group(0).endswith(":")
                    dotall |= "s" in on and not scoped  # (?s): the whole pattern (Python wants it at the start)
                    g.dotall = "s" in on and scoped
            else:
                g.capture = True
                neg_capture |= any(x.negative for x in stack)
                groups.append(g)
            stack.append(g)
            i, quant = i + (2 if rx.startswith("(?", i) else 1), False
            continue
        if c == ")" and stack:
            g = stack.pop()
            g.end, behind, quant = i, g.behind, True
            i += 1
            continue
        if c == "|":
            (stack[-1] if stack else top).alts.append(i)
        elif c == "$" and rx[i + 1:].lstrip(")")[:1] not in ("", "|") and \
                (engine == JS or not rx.startswith(("\\n", "\n"), i + 1)):
            notes.add("a $ before more of the pattern matches only at the very end in JavaScript "
                      "(Python: also before a final line break)" if engine == JS else
                      "a $ before more of the pattern also matches before a final \\r, \\x85, \\u2028 or \\u2029 "
                      "in Java (Python: only before \\n)")
        elif c == "{":
            m = _COUNT.match(rx, i)
            if m:
                if engine == JAVA and max(int(x) for x in re.findall("[0-9]+", m[0])) > _JAVA_MAX:
                    refuse("a count above 2147483647 (an error in Java)", "use a smaller count")
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
        elif c == "." and not (dotall or any(g.dotall for g in stack)):
            notes.add(f". does not match \\r, \\x85, \\u2028 or \\u2029 in {engine} (Python: it does)"
                      if engine == JAVA else
                      ". does not match \\r, \\u2028, \\u2029 or a whole emoji in JavaScript (Python: it does)")
        quant = False
        i += 1
    if engine == JAVA and verbose and class_space:
        refuse("(?x) with a space or # inside a [...] class (Java skips them there too)", "escape them as \\  or \\#")
    if engine == JS and any(_optional(groups[b - 1], rx) for b in backrefs if 0 < b <= len(groups)):
        notes.add("a back-reference to a group that did not take part matches an empty text in JavaScript "
                  "(Python: it fails)")
    if engine == JAVA and backrefs and neg_capture:
        notes.add("a group captured inside a failed (?!...) or (?<!...) stays set in Java (Python: it is cleared)")
    return sorted(notes)


def _optional(g: _Group | None, rx: str) -> bool:
    """Whether a match can leave the group out: a quantifier on it (or around it) that allows none, or a |
    beside it."""
    while g is not None and g.depth >= 0:
        if (g.end >= 0 and _ZERO_OK.match(rx, g.end + 1)) or (g.parent is not None and g.parent.alts):
            return True
        g = g.parent
    return False


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
                    if k == "id" and n.startswith(". "):
                        continue  # a dot in an id is the package's, and an id never holds a line break
                    ps = seen.setdefault(n, [])
                    if v not in ps:
                        ps.append(v)
    return [f"{n}: /{shown(ps[0])}/" + (f" and {len(ps) - 1} more pattern(s)" if len(ps) > 1 else "")
            for n, ps in seen.items()]

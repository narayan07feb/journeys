"""journeys/regex_parity.py's contract, checked against the real engines.

The checker is default-deny: it passes only a shared subset. CASES says, per engine, what it does with a
pattern, and what the engine does with the texts given:
- (SAME, texts): passed with no note; the engine matches every text as Python's fullmatch does.
- ("note", texts): passed with a note; the engine differs from Python on at least one text (none given:
  the note names a difference these texts do not show).
- ("refuse", texts): refused; the engine cannot read it, or differs from Python on at least one text.
- (DENY, []): refused because it is outside the subset; no claim about the engine.
Every witness the review rounds found is a row. test_the_subset_reads_the_same_on_the_engines fuzzes the
subset's grammar: what passes with no note must match the same. The engine tests run on the java
(Maestro: "(?-ims)" + pattern with Maestro's Kotlin Regex flags, Matcher.matches()) and node (the Appium
export: new RegExp(`^(?:${p})$`)) on PATH, when there.
"""
import base64
import json
import random
import re
import shutil
import subprocess
import warnings
from pathlib import Path

import pytest

from journeys.actions import ActionError, escape
from journeys.regex_parity import JAVA, JS, check, notes_for

SAME, DENY = "same", "deny"
E = "\U0001F600"  # an emoji: two UTF-16 units in JavaScript
CASES = {
    # the ordinary patterns a pack uses: they must pass
    "(?:Save|Next)+": {JAVA: ("note", []), JS: (SAME, ["SaveNext", "Save", ""])},  # Java: the stack note
    "Save$|Cancel": {JAVA: (SAME, ["Save", "Cancel", "Save\n"]), JS: (SAME, ["Save", "Cancel", "Save\n"])},
    "^Save|^Cancel": {JAVA: (SAME, ["Save", "Cancel"]), JS: (SAME, ["Save", "Cancel"])},
    "(?:Network & internet)[\\s\\S]*": {JAVA: (SAME, ["Network & internet\nmore", "x"]),
                                         JS: (SAME, ["Network & internet\nmore", "x"])},
    "[\\s\\S]*(?:x)\\n?": {JAVA: (SAME, ["ab x\n", "x\r"]), JS: (SAME, ["ab x\n", "x\r"])},
    "(Yes|No)?": {JAVA: (SAME, ["", "Yes", "YesNo"]), JS: (SAME, ["", "Yes", "YesNo"])},
    "Price: \\$[0-9]+(\\.[0-9][0-9])?": {JAVA: (SAME, ["Price: $10", "Price: $1.50", "Price: 1"]),
                                         JS: (SAME, ["Price: $10", "Price: $1.50", "Price: 1"])},
    "[A-Z][a-z]+": {JAVA: (SAME, ["Settings", "SETTINGS"]), JS: (SAME, ["Settings", "SETTINGS"])},
    "(a|b){2,3}?": {JAVA: (SAME, ["ab", "aaaa"]), JS: (SAME, ["ab", "aaaa"])},
    "^Total": {JAVA: (SAME, ["Total", "xTotal"]), JS: (SAME, ["Total", "xTotal"])},
    "[\\s\\S]*(?:Network)[\\s\\S]*": {JAVA: (SAME, ["Header\nNetwork", "x"]), JS: (SAME, ["Header\nNetwork", "x"])},
    "(?i)[\\s\\S]*(?:network)[\\s\\S]*": {JAVA: ("note", []), JS: (DENY, [])},
    "\\t\\n\\r\\f": {JAVA: (SAME, ["\t\n\r\f"]), JS: (SAME, ["\t\n\r\f"])},
    "\\ \\-\\_\\#\\&\\~": {JAVA: (SAME, [" -_#&~"]), JS: (SAME, [" -_#&~"])},
    "x{0}y": {JAVA: (SAME, ["y", "xy"]), JS: (SAME, ["y", "xy"])},
    "a{2}": {JAVA: (SAME, ["aa", "a{2}"]), JS: (SAME, ["aa", "a{2}"])},
    "\\{x\\}": {JAVA: (SAME, ["{x}"]), JS: (SAME, ["{x}"])},
    "[{]x": {JAVA: (SAME, ["{x"]), JS: (SAME, ["{x"])},
    "x}": {JAVA: (SAME, ["x}"]), JS: (SAME, ["x}"])},
    "a]": {JAVA: (SAME, ["a]"]), JS: (SAME, ["a]"])},
    "Café": {JAVA: (SAME, ["Café", "cafe"]), JS: (SAME, ["Café", "cafe"])},
    "[\\]]x": {JAVA: (SAME, ["]x"]), JS: (SAME, ["]x"])},
    "\\x41\\u00e9": {JAVA: (SAME, ["Aé"]), JS: (SAME, ["Aé"])},
    "[a-]": {JAVA: (SAME, ["-"]), JS: (SAME, ["-"])},
    "[.]": {JAVA: (SAME, [".", "a"]), JS: (SAME, [".", "a"])},
    "\\\\Z": {JAVA: (SAME, ["\\Z"]), JS: (SAME, ["\\Z"])},
    E + " hi": {JAVA: (SAME, [E + " hi"]), JS: (SAME, [E + " hi"])},
    # passed with a note
    "\\d+ items": {JAVA: ("note", ["\u0663 items"]), JS: ("note", ["\u0663 items"])},
    "\\w": {JAVA: ("note", ["é"]), JS: ("note", ["é"])},
    "\\B": {JAVA: ("note", [""]), JS: ("note", [""])},
    "é\\b": {JAVA: ("note", ["é"]), JS: ("note", ["é"])},  # Java: see VERSIONS
    "a.b": {JAVA: ("note", ["a\rb"]), JS: ("note", ["a\rb"])},
    "x.y": {JAVA: ("note", ["x\x85y"]), JS: ("note", [f"x{E}y"])},
    "[^a]": {JAVA: (SAME, [E, "b"]), JS: ("note", [E])},
    "a$\\s": {JAVA: ("refuse", ["a\r"]), JS: ("refuse", ["a\n"])},
    "(a$|b)\\n": {JAVA: (DENY, []), JS: ("refuse", ["a\n"])},
    "(a$|b)\\r": {JAVA: ("refuse", ["a\r"]), JS: (DENY, [])},
    # round 5 witnesses
    "(?:^|a){2}": {JAVA: ("refuse", ["a"]), JS: (DENY, [])},
    "(?:^|a){2}?": {JAVA: ("refuse", ["a"]), JS: (DENY, [])},
    "[\\s\\S]": {JAVA: (SAME, [E, "\x1c", "\ufeff"]), JS: ("note", [E])},  # \s and \S together: every character
    "[\\s\\S]{2}": {JAVA: (SAME, [E, "ab"]), JS: ("note", [E])},
    # round 6 witnesses
    "[--\\ue000]{2}": {JAVA: (SAME, [E, "--"]), JS: ("refuse", [E])},  # the range starts at a literal -
    "^Save$|^Cancel$": {JAVA: (SAME, ["Save", "Cancel", "Save\n"]), JS: (SAME, ["Save", "Cancel", "Save\n"])},
    "(?:Save)[\\s\\S]*|(?:Cancel)[\\s\\S]*": {JAVA: (SAME, ["Save draft", "Auto Save"]), JS: (SAME, ["Save draft", "Auto Save"])},
    "[\\s\\S]*(?:(Total))\\n?": {JAVA: (SAME, ["x Total\n", "Total x"]), JS: (SAME, ["x Total\n", "Total x"])},
    "[\\u0000-\\uffff]+": {JAVA: (SAME, [E, "ab"]), JS: ("refuse", [E])},
    "\ud83d\ude00": {JAVA: ("refuse", []), JS: ("refuse", [])},  # lone halves, as YAML "\uD83D\uDE00" loads
    "[": {JAVA: ("refuse", []), JS: ("refuse", [])},  # Python cannot read it either
    "a{4294967295}": {JAVA: ("refuse", []), JS: ("refuse", [])},
    "\\s": {JAVA: ("note", ["\x1c"]), JS: ("note", ["\ufeff"])},
    "(?i)[A-Z]": {JAVA: ("note", ["\u212a"]), JS: ("refuse", ["\u212a"])},  # the Kelvin sign
    "(?i)é": {JAVA: (DENY, []), JS: ("refuse", ["É"])},  # round 8: (?i) with a non-ASCII letter is refused
    "(?i)ß": {JAVA: ("refuse", ["\u1e9e"]), JS: ("refuse", ["\u1e9e"])},
    # refused, with a text the engine reads another way
    "a{b": {JAVA: ("refuse", ["a{b"]), JS: (DENY, [])},
    "(?P<n>a)": {JAVA: ("refuse", ["a"]), JS: ("refuse", ["a"])},
    "(?#c)a": {JAVA: ("refuse", ["a"]), JS: ("refuse", ["a"])},
    "(a)?(?(1)b|c)": {JAVA: ("refuse", ["c"]), JS: ("refuse", ["c"])},
    "[a[b]]": {JAVA: ("refuse", ["b"]), JS: (DENY, [])},
    "[a&&b]": {JAVA: ("refuse", ["a"]), JS: (DENY, [])},
    "a\\Z\\n": {JAVA: ("refuse", ["a\n"]), JS: ("refuse", ["aZ\n"])},
    "\\ATotal": {JAVA: (DENY, []), JS: ("refuse", ["Total"])},
    "Total\\Z": {JAVA: (DENY, []), JS: ("refuse", ["Total"])},
    "a{,3}": {JAVA: ("refuse", ["aa"]), JS: ("refuse", ["aa"])},
    "\\0101": {JAVA: ("refuse", ["\x081"]), JS: (DENY, [])},
    "\\101": {JAVA: ("refuse", ["A"]), JS: (DENY, [])},
    "\\0": {JAVA: ("refuse", ["\x00"]), JS: (DENY, [])},
    "\\0377": {JAVA: ("refuse", ["\x1f7"]), JS: (DENY, [])},
    "\\U00000041": {JAVA: ("refuse", ["A"]), JS: ("refuse", ["A"])},
    "\\N{DIGIT ONE}": {JAVA: (DENY, []), JS: ("refuse", ["1"])},
    "\\N{LF}": {JAVA: ("refuse", ["\n"]), JS: ("refuse", ["\n"])},
    "\\v": {JAVA: ("refuse", ["\n"]), JS: (DENY, [])},
    "\\a": {JAVA: (DENY, []), JS: ("refuse", ["\x07"])},
    "[]a]": {JAVA: (DENY, []), JS: ("refuse", ["]"])},
    "[^]a]": {JAVA: (DENY, []), JS: ("refuse", ["b"])},
    "[\\b]": {JAVA: ("refuse", ["\b"]), JS: (DENY, [])},
    "[\\1]": {JAVA: ("refuse", ["\x01"]), JS: (DENY, [])},
    "(?>a)": {JAVA: (DENY, []), JS: ("refuse", ["a"])},
    "a*+": {JAVA: (DENY, []), JS: ("refuse", ["aa"])},
    "a{2}+": {JAVA: (DENY, []), JS: ("refuse", ["aa"])},
    "a{\u0662}+": {JAVA: ("refuse", ["a{\u0662}}"]), JS: (DENY, [])},
    "a{\u0662}": {JAVA: ("refuse", ["a{\u0662}"]), JS: (DENY, [])},  # not a count: Python reads the text
    "a{2147483648}": {JAVA: ("refuse", ["a"]), JS: (DENY, [])},
    "\\ud83d\\ude00": {JAVA: ("refuse", [E]), JS: ("refuse", [E])},
    E + "+": {JAVA: (SAME, [E + E, E]), JS: ("refuse", [E + E])},
    "\\" + E + "+": {JAVA: (DENY, []), JS: ("refuse", [E + E])},
    f"[{E}]": {JAVA: (SAME, [E]), JS: ("refuse", [E])},
    f"[{E}-\U0001F64F]": {JAVA: (SAME, ["\U0001F603"]), JS: ("refuse", ["\U0001F603"])},
    E + "{1}": {JAVA: (SAME, [E]), JS: (DENY, [])},
    "(?x)[a b]": {JAVA: ("refuse", [" "]), JS: ("refuse", [" "])},
    "(?x)[\r]": {JAVA: ("refuse", ["\r"]), JS: (DENY, [])},
    "(?m)a$\\r?b": {JAVA: ("refuse", ["a\rb"]), JS: ("refuse", ["a\rb"])},
    "(?<=a)?b": {JAVA: (DENY, []), JS: ("refuse", ["b"])},
    "(a)?\\1": {JAVA: (DENY, []), JS: ("refuse", [""])},
    "(a)(?<=\\1)b": {JAVA: ("refuse", ["ab"]), JS: (DENY, [])},
    "(?!(a))a|\\1": {JAVA: ("refuse", ["a"]), JS: ("refuse", [""])},
    "(?!(Cancel))Save\\1": {JAVA: (DENY, []), JS: ("refuse", ["Save"])},
    "ab(?<=([ab]){2})\\1": {JAVA: (DENY, []), JS: ("refuse", ["aba"])},
    "(a?){2}\\1": {JAVA: ("refuse", ["aa"]), JS: (DENY, [])},
    "bb(?<=(?:a$|b){2})": {JAVA: ("refuse", ["bb"]), JS: (DENY, [])},
    # refused because they are outside the subset (the engine may read them the same)
    "(?-i:a)b": {JAVA: (DENY, []), JS: (DENY, [])},
    "(?iu)é": {JAVA: (DENY, []), JS: (DENY, [])},
    "(?s:.)": {JAVA: (DENY, []), JS: (DENY, [])},
    "(?x)Save": {JAVA: (DENY, []), JS: (DENY, [])},
    "(?s:Header(?-s:.))": {JAVA: (DENY, []), JS: (DENY, [])},
    "(?x:Save)[ a]": {JAVA: (DENY, []), JS: (DENY, [])},
    "(?m)OK": {JAVA: (DENY, []), JS: (DENY, [])},
    "Total\\Z|Subtotal": {JAVA: (DENY, []), JS: (DENY, [])},
    "(a)\\1": {JAVA: (DENY, []), JS: (DENY, [])},
    "(Save)\\1|Cancel": {JAVA: (DENY, []), JS: (DENY, [])},
    "(a)\\1\u00b2": {JAVA: (DENY, []), JS: (DENY, [])},  # round 4: a crash before
    "()" * 101 + "\\101": {JAVA: (DENY, []), JS: (DENY, [])},
    "(a)" * 400: {JAVA: (DENY, []), JS: (DENY, [])},  # round 7: too long; both engines overflow at 10,000
    "[^\\s\\S]": {JAVA: (SAME, ["a", E]), JS: (SAME, ["a", E])},  # matches nothing anywhere: no note
    "(?<=a)b": {JAVA: (DENY, []), JS: (DENY, [])},
    "\\0777": {JAVA: (DENY, []), JS: (DENY, [])},
}
PY_INVALID = {"[", "a{4294967295}"}  # Python itself cannot read these: refused before any other rule
# (pattern, engine): the engine version decides whether it reads the text as Python does
VERSIONS = {("é\\b", JAVA): lambda v: v >= 19}  # True: it differs (Java 19 and later read \b as ASCII)


def verdict(p, engine):
    try:
        return "note" if check(p, engine) else SAME
    except ActionError:
        return "refuse"


@pytest.mark.parametrize("engine", [JAVA, JS])
@pytest.mark.parametrize("p", list(CASES))
def test_journeys_refuses_notes_or_passes_each_pattern(p, engine):
    if p not in PY_INVALID:
        _python(p, "")  # every other case is a pattern Journeys itself accepts
    kind = CASES[p][engine][0]
    assert verdict(p, engine) == ("refuse" if kind == DENY else kind)


def _python(p, s):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)  # [a[b]]: Python warns about a future set syntax
        return str(bool(re.fullmatch(p, s))).lower()


def _java(runs):
    b = lambda x: base64.b64encode(x.encode()).decode()
    out = subprocess.run([shutil.which("java"), str(Path(__file__).parent / "regex" / "Match.java")], text=True,
                         input="".join(f"{b(p)}\t{b(s)}\n" for p, s in runs), capture_output=True, timeout=300)
    assert out.returncode == 0, out.stderr
    return out.stdout.split()


def _node(runs):
    js = ("const r = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
          "console.log(r.map(([p, s]) => { try { return String(new RegExp(`^(?:${p})$`).test(s)); }"
          " catch (e) { return 'ERROR'; } }).join(' '));")
    out = subprocess.run([shutil.which("node"), "-e", js], text=True, input=json.dumps(runs), capture_output=True,
                         timeout=300)
    assert out.returncode == 0, out.stderr
    return out.stdout.split()


def _version(tool):
    """The major version: java -version says `version "17.0.15"`, node --version says `v22.23.4`."""
    out = subprocess.run([shutil.which(tool), "-version" if tool == "java" else "--version"], text=True,
                         capture_output=True)
    return int(re.search(r'version "(\d+)|v(\d+)', out.stdout + out.stderr).group(1, 2)[tool == "node"])


ENGINES = [(JAVA, _java, "java"), (JS, _node, "node")]


@pytest.mark.parametrize("engine, run, tool", ENGINES)
def test_each_case_reads_on_the_real_engine_as_the_table_says(engine, run, tool):
    if shutil.which(tool) is None:
        pytest.skip(f"{tool} is not installed")
    version = _version(tool)
    runs = [(p, s) for p, c in CASES.items() if c[engine][0] != DENY for s in c[engine][1]]
    got = dict(zip(runs, run(runs)))
    assert len(got) == len(runs)
    wrong = []
    for p, c in CASES.items():
        kind, texts = c[engine]
        if kind == DENY or not texts:
            continue
        if kind in (SAME, "note") and any(got[(p, s)] not in ("true", "false") for s in texts):
            wrong.append((p, kind, "an accepted pattern must read on the engine", [got[(p, s)] for s in texts]))
            continue
        differs = [got[(p, s)] != _python(p, s) for s in texts]
        if (p, engine) in VERSIONS:
            ok = any(differs) == VERSIONS[(p, engine)](version)
        else:
            ok = not any(differs) if kind == SAME else any(differs)
        if not ok:
            wrong.append((p, kind, [(s, got[(p, s)], _python(p, s)) for s in texts]))
    assert not wrong, f"{engine} {version}"


# The subset's grammar, for the fuzz: each node gives a pattern and a function that builds a text it matches.
_LIT = ["a", "b", "Z", "é", "1", "-", " ", ",", "/", "\u00a0", "\u0663", "\r", "\n", "\x85", "ß", "I", "\u0130",
        "k", "\u212a", "\ufeff", E]
_ESC = ["\\.", "\\$", "\\(", "\\)", "\\[", "\\]", "\\{", "\\}", "\\|", "\\*", "\\+", "\\?", "\\\\", "\\-",
        "\\ ", "\\t", "\\n", "\\x41", "\\u00e9", "\\/", "\\,", "\\#"]
_CLASS = ["[ab]", "[a-z]", "[^a]", "[-a]", "[a-]", "[\\]a]", "[.]", "[é1]", "[A-Z0-9]", "[^\\n]", "[\\-\\]]",
          "[\\s\\S]", "[\\u0000-\\uffff]", "[\\S]", "[^ ]", "[\\w\\-]", "[--/]", "[\\s\\S\\d]"]
_SHORT = ["\\d", "\\w", "\\s", "\\D", "\\W", "\\S"]
_POOL = _LIT + ["A", "{", "}", "(", ")", "[", "]", ".", "$", "\\", "|", "*", "+", "?", "\t", "\u2028", "\x1c",
                "_", "#", ","]


def _gen(rng, depth=0, inside=False):
    """A random pattern from the subset, and a function giving a text built to match it (Python may say no).
    No quantifier inside a quantified group: nested ones backtrack for minutes on a text that fails late."""
    r = rng.random()
    if depth > 2 or r < 0.35:
        atom = rng.choice(_LIT + _ESC + _CLASS + _SHORT + [".", "^", "$", "[\\s\\S]*"])
        if atom in ("^", "$"):
            return atom, lambda rng: rng.choice(["", "\n"]) if atom == "$" else ""
        if atom == "[\\s\\S]*":
            return atom, lambda rng: "".join(rng.choice(_POOL) for _ in range(rng.randint(0, 2)))
        fits = [ch for ch in _POOL if re.fullmatch(atom, ch)]
        return atom, lambda rng: rng.choice(fits) if fits else ""
    if r < 0.55:
        parts = [_gen(rng, depth + 1, inside) for _ in range(rng.randint(1, 3))]
        return "".join(p for p, _ in parts), lambda rng: "".join(f(rng) for _, f in parts)
    if r < 0.75 or inside:
        a, b = _gen(rng, depth + 1, inside), _gen(rng, depth + 1, inside)
        return f"{rng.choice(['(', '(?:'])}{a[0]}|{b[0]})", lambda rng: rng.choice([a, b])[1](rng)
    a = _gen(rng, depth + 1, True)
    q, low, high = rng.choice([("*", 0, 3), ("+", 1, 3), ("?", 0, 1), ("{2}", 2, 2), ("{0,2}", 0, 2), ("{1,}", 1, 3),
                               ("*?", 0, 3), ("+?", 1, 3), ("??", 0, 1)])
    return f"(?:{a[0]}){q}", lambda rng: "".join(a[1](rng) for _ in range(rng.randint(low, high)))


def _texts(rng, build):
    """Texts to try: two built to match, one with a final line break, and random strings."""
    built = [build(rng), build(rng)]
    return built + [built[0] + "\n"] + ["".join(rng.choice(_POOL) for _ in range(rng.randint(0, 4))) for _ in range(4)]


def _fuzz(seed, count):
    rng = random.Random(seed)
    cases = {JAVA: [], JS: []}
    for _ in range(count):
        pat, build = _gen(rng)
        if rng.random() < 0.15:
            pat = "^" + pat
        if rng.random() < 0.15:
            pat = pat + "$"
        if rng.random() < 0.1:
            other = _gen(rng)
            pat, build = f"{pat}|{other[0]}", rng.choice([build, other[1]])
        try:
            re.compile(pat)
        except re.error:
            continue
        for engine in (JAVA, JS):
            try:
                if check(pat, engine):
                    continue  # a note: the contract does not promise the same matches
            except ActionError:
                continue
            cases[engine] += [(pat, t) for t in _texts(rng, build)]
    return cases


@pytest.mark.parametrize("engine, run, tool", ENGINES)
def test_the_subset_reads_the_same_on_the_engines(engine, run, tool):
    """What check() passes with no note matches the same texts: a sampled check, seeded (a larger run:
    JOURNEYS_FUZZ=20000)."""
    import os
    if shutil.which(tool) is None:
        pytest.skip(f"{tool} is not installed")
    runs = _fuzz(7, int(os.environ.get("JOURNEYS_FUZZ", "400")))[engine]
    assert len(runs) > 200
    got = run(runs)
    assert len(got) == len(runs)  # one answer per pair, or zip would drop the rest
    wrong = [(p, s, g) for (p, s), g in zip(runs, got) if g != _python(p, s)]
    assert not wrong[:20], f"{len(wrong)} of {len(runs)} differ"


def test_escaped_plain_texts_pass_with_no_note():
    for t in ["Total ${x}", "a.b", "[x]", "{y}", "50% (approx)", "C:\\path", "x|y", "a+b?", "^$", E, "é\n\x85",
              "\\Z", "&&", "[a[b]]"]:
        for engine in (JAVA, JS):
            assert check(escape(t), engine) == [], (t, engine)


def test_export_notes_name_each_difference_once():
    cmds = [{"tapOn": "\\d+ items"}, {"assertVisible": {"text": "\\d of \\d"}}, {"tapOn": {"text": "a.b"}},
            {"tapOn": {"id": "com.x:id/c.d"}},
            {"scrollUntilVisible": {"element": "x\\wy"}}, {"tapOn": "Plain"}, "back"]
    assert notes_for(cmds, JAVA) == [
        "\\d knows only ASCII digits in Java (Python: every language): /\\d+ items/ and 1 more pattern(s)",
        ". does not match \\r, \\x85, \\u2028 or \\u2029 in Java (Python: it does): /a.b/",
        "\\w knows only ASCII letters and digits in Java (Python: every language): /x\\wy/"]
    assert notes_for([{"tapOn": {"id": "x\\d"}}], JS) == []  # an id is plain text in both exports


def test_the_notes_name_what_differs():
    assert check("x\\B", JS) == [
        "\\B knows only ASCII letters and digits in JavaScript (Python: every language), and \\B matches an empty text"]
    assert check("\\bx", JAVA) == ["\\b knows only ASCII letters and digits in Java (Python: every language; more "
                                   "text from Java 19)"]
    assert check("\\D", JS) == ["\\D knows only ASCII digits in JavaScript (Python: every language), and matches "
                                "only half of an emoji in JavaScript"]
    with pytest.raises(ActionError, match="write both cases, as \\[Ss\\]ave"):
        check("(?i)save", JS)
    for rx, hint in (("(?#c)a", "comments cannot be sent"), ("(?>a)", "atomic groups"), ("(?i:a)", "\\[Ss\\]ave")):
        with pytest.raises(ActionError, match=hint):
            check(rx, JAVA)
    assert check("\\s", JAVA) == ["\\s knows only ASCII spaces in Java (Python: Unicode spaces, and \\x1c-\\x1f)"]
    assert check("\\s", JS) == ["\\s knows another set of spaces in JavaScript, such as \\ufeff (Python: Unicode "
                                "spaces and \\x1c-\\x1f, not \\ufeff)"]
    assert check("a$", JAVA) == check("(a$)", JS) == check("a$|b", JS) == []
    assert check("(?i)a", JAVA) == ["(?i) folds some letters outside ASCII another way in Java: [A-Z] matches the "
                                    "Kelvin sign and \\u0130 in Python, not in Java"]
    stack = ("a repeated group with a | or an optional part inside can overflow Java's stack on a text of a few "
             "thousand characters (Python: fine); for single characters use a class, such as [ab]* for (?:a|b)*")
    for rx in ("(?:a|b)*", "x(?:(?:a|b)c)+", "(?:a|b){2,}", "(?:ab?)*", "(?:a{1,2})*", "(?:[ab]c?)+", "(ab*)*"):
        assert check(rx, JAVA) == [stack], rx  # each overflows Java 11, 17 and 25 (a few thousand characters)
    for rx in ("(?:a|b){2,3}", "(?:ab)*", "(?:a{2})*", "(?:ab?){1,50}", "[ab]*", "[0-9]{999}"):
        assert check(rx, JAVA) == [], rx  # these repeat without recursion, or only a few times
    assert check("(?:a|b)*", JS) == []
    assert check("[\\S]", JS) == sorted([_halves("[...\\S...] class"), "\\S knows another set of spaces in "
                                         "JavaScript, such as \\ufeff (Python: Unicode spaces and \\x1c-\\x1f, not \\ufeff)"])


def _halves(what):
    return f"a {what} can match half of an emoji in JavaScript (Python: only the whole one)"


def test_a_refusal_names_the_subset_and_a_hint():
    with pytest.raises(ActionError, match=r"the group \(\?=\.\.\. is not in the pattern subset .* lookarounds cannot"):
        check("a(?=b)", JAVA)
    with pytest.raises(ActionError, match=r"write \{0,n\} for up to n, or \{0,\} for any number"):
        check("a{,3}", JAVA)
    with pytest.raises(ActionError, match=r"or \{0,\} for any number"):
        check("a{,}", JS)
    with pytest.raises(ActionError, match=r"write \(\?:\U0001F642\)\{2\}"):
        check("\U0001F642{2}", JS)
    with pytest.raises(ActionError, match=r"write \[\\s\\S\] in place of \."):
        check("(?s)Line one.*Line two", JAVA)
    with pytest.raises(ActionError, match="a pattern Python cannot read"):
        check("[", JAVA)
    with pytest.raises(ActionError, match=r"the escape \\1 .* write the character itself"):
        check("(a)\\1", JS)


def test_both_exports_carry_the_notes_and_refusals(pack_dir):
    from journeys.formats import appium, maestro
    from journeys.pack import Pack
    (pack_dir / "journeys" / "n.yaml").write_text("name: n\nsteps:\n  - see: '/\\d+ items/'\n")
    pack = Pack.load(pack_dir)
    for fmt, engine in ((maestro, JAVA), (appium, JS)):
        assert f"\\d knows only ASCII digits in {engine} (Python: every language): /\\d+ items/" in \
            fmt.export(pack, pack.journeys["n"])[1], engine
    (pack_dir / "journeys" / "n.yaml").write_text("name: n\nsteps:\n  - see: '/Total\\Z/'\n")
    pack = Pack.load(pack_dir)
    for fmt, engine in ((maestro, JAVA), (appium, JS)):
        with pytest.raises(ActionError, match=f"the escape .Z is not in the pattern subset Journeys can give {engine}"):
            fmt.export(pack, pack.journeys["n"])


def test_an_id_is_a_plain_name_in_both_exports(pack_dir):
    from journeys.formats import appium, maestro
    from journeys.pack import Pack
    (pack_dir / "journeys" / "i.yaml").write_text("name: i\nsteps:\n  - see: {id: 'a\\Z'}\n")
    pack = Pack.load(pack_dir)
    flow, notes = maestro.export(pack, pack.journeys["i"])
    assert "id: (?-ims)a\\\\Z" in flow and notes == []  # a\Z escaped: Java reads it as the text a\Z
    spec, notes = appium.export(pack, pack.journeys["i"])
    assert '"id": "a\\\\Z"' in spec and not [n for n in notes if "JavaScript" in n]


def test_a_screen_rule_is_checked_and_wrapped_for_both_exports(pack_dir):
    from journeys.formats import appium, maestro
    from journeys.pack import Pack
    f = pack_dir / "map" / "screens.yaml"
    f.write_text(f.read_text().replace('all: [{text: "/^Network & internet/"}]', 'all: [{text: "/(?i)network/"}]'))
    (pack_dir / "journeys" / "s.yaml").write_text("name: s\nsteps:\n  - expect: SETTINGS-HOME\n")
    pack = Pack.load(pack_dir)
    flow, notes = maestro.export(pack, pack.journeys["s"])
    assert "(?i)[\\s\\S]*(?:network)[\\s\\S]*" in flow and any(n.startswith("(?i) folds") for n in notes)
    with pytest.raises(ActionError, match=r"\(\?i\) is not in the pattern subset Journeys can give JavaScript"):
        appium.export(pack, pack.journeys["s"])  # refused, never sent as plain text
    f.write_text(f.read_text().replace('/(?i)network/', '/(?i)(?s)network/'))
    pack = Pack.load(pack_dir)
    with pytest.raises(ActionError, match="SETTINGS-HOME: rule .* cannot be exported as a whole-text pattern"):
        appium.export(pack, pack.journeys["s"])  # (?s) moves past the start: an error, never plain text


def test_a_note_names_its_pattern_on_one_line():
    assert notes_for([{"tapOn": "\\d\nNext"}], JS) == [
        "\\d knows only ASCII digits in JavaScript (Python: every language): /\\d\\x0aNext/"]


def test_ids_and_map_labels_reach_the_exports_cleanly(pack_dir):
    from journeys.formats import appium, maestro
    from journeys.pack import Pack
    j = pack_dir / "journeys" / "w.yaml"
    j.write_text("name: w\nsteps:\n  - see: {id: 'a{4294967295}'}\n")  # too big for Python's re: a plain name here
    pack = Pack.load(pack_dir)
    assert '"id": "a{4294967295}"' in appium.export(pack, pack.journeys["w"])[0]
    assert "id: (?-ims)a\\{4294967295\\}" in maestro.export(pack, pack.journeys["w"])[0]
    j.write_text("name: w\nsteps:\n  - see: {id: '['}\n")
    pack = Pack.load(pack_dir)
    assert "id: (?-ims)\\[" in maestro.export(pack, pack.journeys["w"])[0]  # never sent as (?-ims)[
    f = pack_dir / "map" / "screens.yaml"
    f.write_text(f.read_text().replace('all: [{text: "/^Network & internet/"}]', 'all: [{text: "Code ${draft}"}]'))
    j.write_text("name: w\nsteps:\n  - expect: SETTINGS-HOME\n")
    pack = Pack.load(pack_dir)
    spec = appium.export(pack, pack.journeys["w"])[0]  # a map label is never a variable
    assert '"text": "Code \\\\$\\\\{draft\\\\}", "rx": true' in spec and '"exact": false}' in spec  # a pattern only


def test_the_class_and_wrapper_rules_that_the_table_cannot_show():
    from journeys.formats import _part
    for c in ("\\W", "\\D"):
        assert _halves(f"[...{c}...] class") in check(f"[{c}]", JS)
    assert _part("x$", "S") == "[\\s\\S]*(?:x)\\n?"  # Python's $ also matches before a final \n
    assert _part("^a|b", "S") == "(?:a)[\\s\\S]*|[\\s\\S]*(?:b)[\\s\\S]*"  # each top-level branch on its own
    assert _part("^(a|b)", "S") == "(?:(a|b))[\\s\\S]*" and _part("^[|]", "S") == "(?:[|])[\\s\\S]*"
    assert _part("(Total$)", "S") == "[\\s\\S]*(?:(Total))\\n?" and _part("x\\$", "S") == "[\\s\\S]*(?:x\\$)[\\s\\S]*"
    assert _part("(^a|b)c", "S") == "[\\s\\S]*(?:(^a|b)c)[\\s\\S]*"  # a | around the ^: it stays, and check() refuses


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_spec_matches_a_map_selector_only_as_a_pattern():
    """A label that equals the wrapper text itself must not match a map selector (the map never would)."""
    from journeys.formats.appium import _HELPERS
    wrap = "[\\s\\S]*(?:^Net)[\\s\\S]*"
    js = (_HELPERS % {"wait": 1, "scroll": 1} + "\nconst sel = S(" + json.dumps({"text": wrap, "rx": True, "id": None, "index": 0, "exact": False})
          + ");\nconsole.log(hit(sel, [{text: " + json.dumps(wrap) + ", id: null}]) === null, "
          "hit(sel, [{text: 'Net x', id: null}]) !== null);")
    out = subprocess.run([shutil.which("node"), "-e", js], text=True, capture_output=True, timeout=60)
    assert out.stdout.split() == ["true", "true"], out.stderr


def test_a_count_too_big_for_python_is_a_pack_error_not_a_crash(pack_dir):
    from journeys.pack import Pack
    (pack_dir / "journeys" / "b.yaml").write_text("name: b\nsteps:\n  - see: '/a{4294967295}/'\n")
    assert any("bad regex /a{4294967295}/" in e for e in Pack.load(pack_dir).errors)
    f = pack_dir / "map" / "screens.yaml"
    f.write_text(f.read_text().replace('all: [{text: "/^Network & internet/"}]', 'all: [{text: "/a{4294967295}/"}]'))
    assert any("SETTINGS-HOME" in e and "repetition" in e for e in Pack.load(pack_dir).errors)


def test_map_rules_with_anchors_and_literal_placeholders_export(pack_dir):
    from journeys.formats import appium, maestro
    from journeys.pack import Pack
    f = pack_dir / "map" / "screens.yaml"
    base = f.read_text()
    f.write_text(base.replace('all: [{text: "/^Network & internet/"}]', 'all: [{text: "/^Network|^Display$/"}]'))
    (pack_dir / "journeys" / "s.yaml").write_text("name: s\nsteps:\n  - expect: SETTINGS-HOME\n")
    pack = Pack.load(pack_dir)
    for fmt in (maestro, appium):
        assert "(?:Network)[\\s\\S]*|(?:Display)\\n?" in fmt.export(pack, pack.journeys["s"])[0].replace(
            "\\\\", "\\")
    (pack_dir / "journeys" / "s.yaml").write_text("name: s\nsteps:\n  - see: '/Code \\$\\{draft\\}/'\n")
    pack = Pack.load(pack_dir)
    assert "Code" in appium.export(pack, pack.journeys["s"])[0]  # a pattern's \$\{draft\} is the literal text
    f.write_text(base.replace('all: [{text: "/^Network & internet/"}]', 'all: [{text: "/' + "(" * 495 + "a"
                                                                            + ")" * 495 + '/"}]'))
    assert any("SETTINGS-HOME" in e and "recursion" in e for e in Pack.load(pack_dir).errors)  # a load error


def test_a_rule_that_only_breaks_once_wrapped_is_an_export_error():
    from journeys.formats import _part
    deep = "(" * 495 + "a" + ")" * 495  # Python 3.12 compiles it, not with one more group around it
    with pytest.raises(ActionError, match="screen S: rule .* cannot be exported as a whole-text pattern"):
        _part(deep, "S")
    assert _part("(b|a$)", "S") == "[\\s\\S]*(?:(b|a$))[\\s\\S]*"  # a | around the $: it stays (check() refuses)


def test_round_7_anchor_lifting_size_pairs_and_counts(pack_dir):
    from journeys.formats import _part, appium, maestro
    from journeys.pack import Pack
    for rule in ("(^Save)?Cancel", "(^Save)+Cancel"):  # a quantified group: the ^ stays, and check() refuses it
        assert "(^Save)" in _part(rule, "S")
        with pytest.raises(ActionError, match="a \\^ inside the pattern .* only at the start"):
            check(_part(rule, "S"), JAVA)
    assert _part("(^Save)Cancel", "S") == "(?:(Save)Cancel)[\\s\\S]*"  # a group that runs once carries it out
    with pytest.raises(ActionError, match="more than 100 groups, or groups nested more than 10 deep"):
        check("(a)" * 400, JS)
    for rx in ("(?:((((((((((((((((a|b))))))))))))))))){999}", "(" * 11 + "a" + ")" * 11):  # round 8: depth
        with pytest.raises(ActionError, match="nested more than 10 deep"):
            check(rx, JAVA)
    for rx in ("(?:ab){101}", "(?:(?:a|b){100}){100}", "(?:(?:ab){10}){11}", "(?:ab?){1,500}"):  # Java overflows
        with pytest.raises(ActionError, match="a count on a group above 100"):
            check(rx, JAVA)
    assert check("(?:(?:ab){10}){10}", JAVA) == check(escape("x(" * 600), JAVA) == check("a{999}", JS) == []
    with pytest.raises(ActionError, match="list in a \\[\\^...\\] class only the characters"):
        check("[^\\ud7ff-\\ue000]{2}", JS)
    (pack_dir / "journeys" / "e.yaml").write_text('name: e\nsteps:\n  - see: "Save \\uD83D\\uDE00"\n')
    pack = Pack.load(pack_dir)  # YAML reads the escape as two halves; the selector joins them
    assert not pack.errors and maestro.export(pack, pack.journeys["e"]) and appium.export(pack, pack.journeys["e"])
    f = pack_dir / "map" / "screens.yaml"
    base = f.read_text()
    f.write_text(base.replace('all: [{text: "/^Network & internet/"}]', 'all: [{text: "Save \\uD83D\\uDE00"}]'))
    (pack_dir / "journeys" / "m.yaml").write_text("name: m\nsteps:\n  - expect: SETTINGS-HOME\n")
    pack = Pack.load(pack_dir)  # a map rule joins the halves too
    assert not pack.errors and maestro.export(pack, pack.journeys["m"]) and appium.export(pack, pack.journeys["m"])
    f.write_text(base)
    (pack_dir / "journeys" / "e.yaml").write_text("name: e\nsteps:\n  - see: '/a{" + "0" * 4500 + "1}/'\n")
    assert any("bad regex" in e for e in Pack.load(pack_dir).errors)  # Python's int length limit: an error


def test_explore_survives_a_tap_the_driver_cannot_send(tappable_pack_dir, tmp_path, monkeypatch):
    """A screen's label can fall outside the pattern subset (Maestro refuses it): exploring just stops."""
    import journeys.runner as runner_mod
    from journeys.journey import Step
    from journeys.pack import Pack
    from journeys.results import Store
    from phone_sim import Phone, hooked
    import yaml
    pack = Pack.load(tappable_pack_dir)
    d = hooked("fake", Phone(yaml.safe_load((tappable_pack_dir / "fake" / "app.yaml").read_text())), tmp_path)
    d.start(pack.app, "emulator-5554")
    store = Store(tmp_path / "state")
    r = runner_mod.Runner(pack, d, store, store.new_run(), heal=True)
    def refuse(*a, **k):
        raise ActionError("a label outside the subset")
    monkeypatch.setattr(runner_mod, "explore", refuse)
    monkeypatch.setattr(r, "_alive", lambda: True)
    r._explore_failure("F-1", Step("see", "x"))  # no exception


ID_NAMES = ["next", "a.b", "a$b", "x(y)", "q?", "[n]", "a\\b", "a|b", "{1}", "a b", "Name", "ß", "naïve", "😀"]


@pytest.mark.skipif(shutil.which("java") is None, reason="java is not installed")
def test_maestro_matches_an_id_on_java_as_journeys_does():
    """Maestro full-matches an id pattern against the whole resource-id or the part after its last /
    (Filters.idMatches, cli-2.12.1). The maestro driver's id must pick the views id_matches picks, on real
    Java, except one known gap: pkg:id/name cannot match a view whose resource-id is the bare name."""
    from journeys.drivers.base import id_matches
    from journeys.drivers.maestro import SAME, _maestro_id
    wants = [w for n in ID_NAMES for w in (n, f"com.x:id/{n}")]
    haves = [h for n in ID_NAMES for h in (n, f"com.x:id/{n}", f"android:id/{n}", n.upper(), f"x{n}", f"{n}x")] + \
        ["axb", "a.bc", "com.x:id/axb", "comxx:id/next"]
    runs, pairs = [], []
    for w in wants:
        p = _maestro_id(w)[len(SAME):]  # Match.java adds SAME itself
        for h in haves:
            pairs.append((w, h))
            runs += [(p, h), (p, h.rsplit("/", 1)[-1])]
    got = _java(runs)
    differ = {(w, h) for k, (w, h) in enumerate(pairs) if ("true" in got[2 * k:2 * k + 2]) != id_matches(w, h)}
    gap = {(w, h) for w, h in pairs if ":id/" in w and ":id/" not in h and id_matches(w, h)}
    assert gap and differ == gap

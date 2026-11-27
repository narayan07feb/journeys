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
    "[\\s\\S]": {JAVA: ("note", []), JS: ("note", [E])},
    "[\\s\\S]{2}": {JAVA: ("note", []), JS: ("note", [E])},
    "[\\u0000-\\uffff]+": {JAVA: (SAME, [E, "ab"]), JS: ("refuse", [E])},
    "\ud83d\ude00": {JAVA: ("refuse", []), JS: ("refuse", [])},  # lone halves, as YAML "\uD83D\uDE00" loads
    "[": {JAVA: ("refuse", []), JS: ("refuse", [])},  # Python cannot read it either
    "a{4294967295}": {JAVA: ("refuse", []), JS: ("refuse", [])},
    "\\s": {JAVA: ("note", ["\x1c"]), JS: ("note", ["\ufeff"])},
    "(?i)[A-Z]": {JAVA: ("note", ["\u212a"]), JS: ("refuse", ["\u212a"])},  # the Kelvin sign
    "(?i)é": {JAVA: ("note", []), JS: ("refuse", ["É"])},  # Maestro folds é and É as Python does
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
          "[\\s\\S]", "[\\u0000-\\uffff]", "[\\S]", "[^ ]", "[\\w\\-]"]
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
    assert notes_for([{"tapOn": {"id": "com.x:id/c.d"}}], JAVA) == []  # a dot in an id is the package's
    assert notes_for([{"tapOn": {"id": "x\\d"}}], JS, keys=("text",)) == []  # the Appium export leaves ids out


def test_the_notes_name_what_differs():
    assert check("x\\B", JS) == [
        "\\B knows only ASCII letters and digits in JavaScript (Python: every language), and \\B matches an empty text"]
    assert check("\\bx", JAVA) == ["\\b knows only ASCII letters and digits in Java 19 and later (Python: every language)"]
    assert check("\\s", JAVA) == ["\\s knows only ASCII spaces in Java (Python: Unicode spaces, and \\x1c-\\x1f)"]
    assert check("\\s", JS) == ["\\s knows another set of spaces in JavaScript, such as \\ufeff (Python: Unicode "
                                "spaces and \\x1c-\\x1f, not \\ufeff)"]
    assert check("a$", JAVA) == check("(a$)", JS) == check("a$|b", JS) == []
    assert check("(?i)a", JAVA) == ["(?i) folds a few letters differently in Java: [A-Z] matches the Kelvin sign "
                                    "\u212a in Python, not in Java"]
    stack = ("a repeated group with | can overflow Java's stack on a text of a few thousand characters (Python: "
             "fine); for single characters use a class, such as [ab]* for (?:a|b)*")
    assert check("(?:a|b)*", JAVA) == check("x(?:(?:a|b)c)+", JAVA) == check("(?:a|b){2,}", JAVA) == [stack]
    assert check("(?:a|b){2,3}", JAVA) == check("(?:ab)*", JAVA) == check("(?:a|b)*", JS) == []
    assert check("[\\S]", JS) == sorted([_halves("[...\\S...] class"), "\\S knows another set of spaces in "
                                         "JavaScript, such as \\ufeff (Python: Unicode spaces and \\x1c-\\x1f, not \\ufeff)"])


def _halves(what):
    return f"a {what} can match half of an emoji in JavaScript (Python: only the whole one)"


def test_a_refusal_names_the_subset_and_a_hint():
    with pytest.raises(ActionError, match=r"the group \(\?=\.\.\. is not in the pattern subset .* lookarounds cannot"):
        check("a(?=b)", JAVA)
    with pytest.raises(ActionError, match=r"write \{0,n\} for up to n"):
        check("a{,3}", JAVA)
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


def test_an_id_is_a_pattern_in_maestro_and_a_plain_name_in_the_appium_export(pack_dir):
    from journeys.formats import appium, maestro
    from journeys.pack import Pack
    (pack_dir / "journeys" / "i.yaml").write_text("name: i\nsteps:\n  - see: {id: 'a\\Z'}\n")
    pack = Pack.load(pack_dir)
    with pytest.raises(ActionError, match="the escape .Z"):
        maestro.export(pack, pack.journeys["i"])
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
    with pytest.raises(ActionError, match="a pattern Python cannot read"):
        maestro.export(pack, pack.journeys["w"])
    j.write_text("name: w\nsteps:\n  - see: {id: '['}\n")
    pack = Pack.load(pack_dir)
    with pytest.raises(ActionError, match="a pattern Python cannot read"):
        maestro.export(pack, pack.journeys["w"])  # never sent to Maestro as (?-ims)[
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
    assert _part("^a|b", "S") == "[\\s\\S]*(?:^a|b)[\\s\\S]*"  # a | at the top: the anchors stay in the rule
    assert _part("^(a|b)", "S") == "(?:(a|b))[\\s\\S]*" and _part("^[|]", "S") == "(?:[|])[\\s\\S]*"


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

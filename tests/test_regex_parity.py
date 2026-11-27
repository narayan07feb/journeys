"""Every pattern rule in journeys/regex_parity.py, checked against the real engines.

CASES says, per engine, what Journeys does with a pattern and a text that shows why:
- ("refuse", text): the engine cannot read the pattern, or matches `text` unlike Python's fullmatch.
- ("note", text): the engine reads it, but matches `text` unlike Python (the export notes it).
- ("same", [texts]): the engine matches every text as Python does.
The verdict test always runs. The engine tests run each case on java (Java 11+, as Maestro: "(?-ims)" +
pattern, Matcher.matches()) and node (as the Appium export: new RegExp(`^(?:${p})$`)) when installed.
"""
import base64
import json
import re
import shutil
import subprocess
import warnings
from pathlib import Path

import pytest

from journeys.actions import ActionError
from journeys.regex_parity import JAVA, JS, check, notes_for

SAME = "same"
CASES = {
    "a{b": {JAVA: ("refuse", "a{b"), JS: (SAME, ["a{b", "ab"])},
    "(?P<n>a)": {JAVA: ("refuse", "a"), JS: ("refuse", "a")},
    "(?#c)a": {JAVA: ("refuse", "a"), JS: ("refuse", "a")},
    "(a)?(?(1)b|c)": {JAVA: ("refuse", "c"), JS: ("refuse", "c")},
    "[a[b]]": {JAVA: ("refuse", "b"), JS: (SAME, ["b", "a]", "b]"])},
    "[a&&b]": {JAVA: ("refuse", "a"), JS: (SAME, ["a", "&", "b"])},
    "a\\Z\\n": {JAVA: ("refuse", "a\n"), JS: ("refuse", "aZ\n")},
    "\\ATotal": {JAVA: (SAME, ["Total", "ATotal"]), JS: ("refuse", "Total")},
    "a{,3}": {JAVA: ("refuse", "aa"), JS: ("refuse", "aa")},
    "\\0101": {JAVA: ("refuse", "\x081"), JS: (SAME, ["\x081", "A"])},
    "\\U00000041": {JAVA: ("refuse", "A"), JS: ("refuse", "A")},
    "\\N{DIGIT ONE}": {JAVA: ("refuse", "1"), JS: ("refuse", "1")},  # Java knows this name: STRICT
    "\\N{LF}": {JAVA: ("refuse", "\n"), JS: ("refuse", "\n")},
    "\\v": {JAVA: ("refuse", "\n"), JS: (SAME, ["\x0b", "\n", "v"])},
    "\\a": {JAVA: (SAME, ["\x07", "a"]), JS: ("refuse", "\x07")},
    "[]a]": {JAVA: (SAME, ["]", "a", "b"]), JS: ("refuse", "]")},
    "[^]a]": {JAVA: (SAME, ["b", "]"]), JS: ("refuse", "b")},
    "(?>a)": {JAVA: (SAME, ["a"]), JS: ("refuse", "a")},
    "a*+": {JAVA: (SAME, ["aa", ""]), JS: ("refuse", "aa")},
    "a{2}+": {JAVA: (SAME, ["aa"]), JS: ("refuse", "aa")},
    "(?i)é": {JAVA: ("note", "É"), JS: ("refuse", "É")},
    "(?-i:a)b": {JAVA: (SAME, ["ab", "Ab"]), JS: ("refuse", "ab")},
    "(?a)\\w": {JAVA: ("refuse", "a"), JS: ("refuse", "a")},
    "\\d": {JAVA: ("note", "٣"), JS: ("note", "٣")},
    "\\w": {JAVA: ("note", "é"), JS: ("note", "é")},
    "\\s": {JAVA: ("note", "\x1c"), JS: ("note", "\x1c")},
    "é\\b": {JAVA: ("note", "é"), JS: ("note", "é")},  # Java 11/17 read it as Python, Java 19+ do not: VARIES
    "\\B": {JAVA: ("note", ""), JS: ("note", "")},
    "\\101": {JAVA: ("refuse", "A"), JS: (SAME, ["A"])},
    "\\0": {JAVA: ("refuse", "\x00"), JS: (SAME, ["\x00"])},
    "\\08": {JAVA: ("refuse", "\x008"), JS: (SAME, ["\x008"])},
    "[\\b]": {JAVA: ("refuse", "\b"), JS: (SAME, ["\b"])},
    "(?<=a)?b": {JAVA: (SAME, ["b"]), JS: ("refuse", "b")},
    "a$\n": {JAVA: (SAME, ["a\n"]), JS: ("note", "a\n")},
    "(?iu)é": {JAVA: (SAME, ["É", "é"]), JS: ("refuse", "É")},
    "(?x)[a b]": {JAVA: ("refuse", " "), JS: ("refuse", " ")},
    "(?m)a$\r?b": {JAVA: ("note", "a\rb"), JS: ("refuse", "a\rb")},
    "[^a]": {JAVA: (SAME, ["\U0001F600", "b"]), JS: ("note", "\U0001F600")},
    "\U0001F600+": {JAVA: (SAME, ["\U0001F600\U0001F600"]), JS: ("note", "\U0001F600\U0001F600")},
    "[\U0001F600]": {JAVA: (SAME, ["\U0001F600"]), JS: ("note", "\U0001F600")},
    "\U0001F600 hi": {JAVA: (SAME, ["\U0001F600 hi"]), JS: (SAME, ["\U0001F600 hi"])},
    "\\ud83d\\ude00": {JAVA: ("refuse", "\U0001F600"), JS: ("refuse", "\U0001F600")},
    "(a)?\\1": {JAVA: (SAME, ["", "a", "aa"]), JS: ("note", "")},
    "(a)?\\101": {JAVA: ("refuse", "A"), JS: (SAME, ["A", "aA"])},  # octal in Python and JS: no back-reference note
    "a$\\s": {JAVA: ("note", "a\r"), JS: ("note", "a\n")},
    "[\\1]": {JAVA: ("refuse", "\x01"), JS: (SAME, ["\x01"])},
    "\\0377": {JAVA: ("refuse", "\x1f7"), JS: (SAME, ["\x1f7"])},
    "x.y": {JAVA: ("note", "x\x85y"), JS: ("note", "x\U0001F600y")},
    "a.b": {JAVA: ("note", "a\rb"), JS: ("note", "a\rb")},
}
# The engine test accepts both results for these. VARIES: the engine version decides (Java 19+ reads \b as
# ASCII; node 23+ reads (?-i:...)), and Journeys keeps the stricter verdict. STRICT: refused on purpose
# although this case reads the same (Java knows some \N{...} names, not all).
VARIES = {("é\\b", JAVA), ("(?-i:a)b", JS)}
STRICT = {("\\N{DIGIT ONE}", JAVA)}
for _p, _texts in {"\\0777": ["?7"], "\\012": ["\n"], "\\00": ["\x00"], "(?<=a)b?": ["", "b"], "a$": ["a", "a\n"],
                   "(a$)": ["a", "a\n"], "a{2}": ["aa", "a{2}"], "\\{x\\}": ["{x}"], "[{]x": ["{x"], "x}": ["x}"], "(?:a)b": ["ab"],
                   "(?=a)a": ["a"], "Café": ["Café", "cafe"], "[\\]]x": ["]x"], "a+?": ["aa"], "(a)\\1": ["aa"],
                   "\\x41": ["A"], "[a-]": ["-"], "[.]": [".", "a"], "\\\\Z": ["\\Z"]}.items():
    CASES[_p] = {JAVA: (SAME, _texts), JS: (SAME, _texts)}


def verdict(p, engine):
    try:
        return "note" if check(p, engine) else SAME
    except ActionError:
        return "refuse"


@pytest.mark.parametrize("engine", [JAVA, JS])
@pytest.mark.parametrize("p", list(CASES))
def test_journeys_refuses_notes_or_passes_each_pattern(p, engine):
    _python(p, "")  # every case is a pattern Journeys itself accepts
    assert verdict(p, engine) == CASES[p][engine][0]


def _python(p, s):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)  # [a[b]]: Python warns about a future set syntax
        return bool(re.fullmatch(p, s))


def _runs(engine):
    return [(p, s) for p, c in CASES.items() for s in ([c[engine][1]] if c[engine][0] != SAME else c[engine][1])]


def _java(runs):
    b = lambda x: base64.b64encode(x.encode()).decode()
    out = subprocess.run([shutil.which("java"), str(Path(__file__).parent / "regex" / "Match.java")], text=True,
                         input="".join(f"{b(p)}\t{b(s)}\n" for p, s in runs), capture_output=True, timeout=120)
    assert out.returncode == 0, out.stderr
    return out.stdout.split()


def _node(runs):
    js = ("const r = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
          "console.log(r.map(([p, s]) => { try { return String(new RegExp(`^(?:${p})$`).test(s)); }"
          " catch (e) { return 'ERROR'; } }).join(' '));")
    out = subprocess.run([shutil.which("node"), "-e", js], text=True, input=json.dumps(runs), capture_output=True,
                         timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout.split()


@pytest.mark.parametrize("engine, run, tool", [(JAVA, _java, "java"), (JS, _node, "node")])
def test_each_case_reads_on_the_real_engine_as_the_table_says(engine, run, tool):
    if shutil.which(tool) is None:
        pytest.skip(f"{tool} is not installed")
    runs = _runs(engine)
    got = run(runs)
    assert len(got) == len(runs)
    wrong = []
    for (p, s), g in zip(runs, got):
        kind, py = CASES[p][engine][0], str(_python(p, s)).lower()
        ok = {SAME: g == py, "note": g not in (py, "ERROR"), "refuse": g != py}[kind] or \
            ((p, engine) in VARIES | STRICT and g == py)
        if not ok:
            wrong.append((p, s, kind, f"{engine} {g}, Python {py}"))
    assert not wrong


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
        with pytest.raises(ActionError, match=f"{engine} reads .Z"):
            fmt.export(pack, pack.journeys["n"])


def test_an_id_is_a_pattern_in_maestro_and_a_plain_name_in_the_appium_export(pack_dir):
    from journeys.formats import appium, maestro
    from journeys.pack import Pack
    (pack_dir / "journeys" / "i.yaml").write_text("name: i\nsteps:\n  - see: {id: 'a\\Z'}\n")
    pack = Pack.load(pack_dir)
    with pytest.raises(ActionError, match="Java reads .Z"):
        maestro.export(pack, pack.journeys["i"])
    spec, notes = appium.export(pack, pack.journeys["i"])
    assert '"id": "a\\\\Z"' in spec and not [n for n in notes if "JavaScript" in n]


def test_python_3_14_end_of_text_is_refused_for_javascript_and_read_by_java():
    """Python 3.14 reads \\z as the end (3.12 refuses it, so it is not in the table); JavaScript reads the letter z."""
    with pytest.raises(ActionError, match="JavaScript reads .z"):
        check("a\\z", JS)
    assert check("a\\z", JAVA) == []  # Java's \\z is the end of the text too


def test_the_b_notes_name_what_differs():
    assert check("x\\B", JS) == [
        "\\B knows only ASCII letters and digits in JavaScript (Python: every language), and \\B matches an empty text"]
    assert check("\\bx", JAVA) == ["\\b knows only ASCII letters and digits in Java 19 and later (Python: every language)"]


def test_a_dollar_before_more_of_the_pattern_is_noted_for_java_unless_a_line_feed_follows():
    assert check("a$\\r", JAVA) == ["a $ before more of the pattern also matches before a final \\r, \\x85, \\u2028 "
                                    "or \\u2029 in Java (Python: only before \\n)"]
    assert check("a$\\n", JAVA) == check("a$", JAVA) == []

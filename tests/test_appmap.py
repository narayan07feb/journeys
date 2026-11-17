from pathlib import Path

import pytest

from journeys.appmap import AppMap, Edge, Rule, ScreenDef
from journeys.drivers.base import Element, Screen


def screen(*texts, ids=()):
    els = [Element(f"@{i}", "Text", t) for i, t in enumerate(texts, 1)]
    els += [Element(f"@{len(els) + i}", "Button", "", id_) for i, id_ in enumerate(ids, 1)]
    return Screen(els)


def test_rule_text_exact_regex_id_kind():
    el = Element("@1", "Button", "Sign in now", "signin")
    assert Rule(text="Sign in now").matches(el)
    assert not Rule(text="Sign in").matches(el)
    assert Rule(text="/^Sign in/").matches(el)
    assert Rule(id="signin", kind="Button").matches(el)
    assert not Rule(id="signin", kind="Text").matches(el)


def test_rule_parse_rejects_unknown_keys_and_bad_regex():
    with pytest.raises(ValueError):
        Rule.parse({"txt": "x"})
    with pytest.raises(Exception):
        Rule.parse({"text": "/(/"})
    assert Rule.parse("Hi") == Rule(text="Hi")


def test_score_all_any_none():
    d = ScreenDef("A", all=[Rule(text="Home")], any=[Rule(text="x"), Rule(text="y")], none=[Rule(text="Bad")])
    assert d.score(screen("Home", "x")) == 2
    assert d.score(screen("Home", "x", "y")) == 3
    assert d.score(screen("Home")) is None          # any not met
    assert d.score(screen("Home", "x", "Bad")) is None
    assert d.score(screen("x")) is None


def test_identify_best_and_tie():
    m = AppMap(screens={
        "A": ScreenDef("A", all=[Rule(text="Home")]),
        "B": ScreenDef("B", all=[Rule(text="Home"), Rule(text="Extra")]),
        "C": ScreenDef("C", all=[Rule(text="Home")]),
    })
    assert m.identify(screen("Home", "Extra")).name == "B"
    tie = m.identify(screen("Home"))
    assert tie.name is None and tie.ambiguous == ["A", "C"]
    assert m.identify(screen("Nothing")).name is None


def test_path_shortest_and_missing():
    m = AppMap(edges=[Edge("A", "B", []), Edge("B", "C", []), Edge("A", "C", ["x"]), Edge("C", "D", [])])
    assert [(e.src, e.dst) for e in m.path("A", "D")] == [("A", "C"), ("C", "D")]
    assert m.path("A", "A") == []
    assert m.path("D", "A") is None


def test_load_reports_errors(tmp_path: Path):
    (tmp_path / "screens.yaml").write_text("screens:\n  A:\n    match: {all: [{bad: 1}]}\n  B: {}\n")
    (tmp_path / "edges.yaml").write_text("edges:\n  - {from: A, to: Z, do: [back]}\n  - {from: A}\n")
    m = AppMap.load(tmp_path)
    text = "\n".join(m.errors)
    assert "screen A" in text and "screen B has no all/any rule" in text
    assert "unknown screen Z" in text and "needs from, to and do" in text
    assert ":2:" in text or ":4:" in text  # errors carry a line


def test_rule_id_matches_full_android_id():
    el = Element("@1", "Button", "", "com.android.settings:id/search_action_bar")
    assert Rule(id="search_action_bar").matches(el)
    assert Rule(id="com.android.settings:id/search_action_bar").matches(el)
    assert not Rule(id="action_bar").matches(el)


def test_selector_for_real_labels():
    from journeys.heal import selector
    sel = lambda t, i="": selector(Element("@1", "Button", t, i))
    assert sel("Play now") == "Play now"
    assert sel("Wild +4 (x)") == r"Wild \+4 \(x\)"
    assert sel("Daily table, Must play") == "Daily table.*"
    assert sel("Skip: next (with two p…") == r"Skip: next \(with two p.*"
    assert sel("x", "com.a:id/go") == {"id": "com.a:id/go"}

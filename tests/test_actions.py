import pytest

from journeys.actions import ActionError, from_commands, to_commands, to_selector


def test_neutral_words_become_driver_commands():
    assert to_commands(["back", {"tap": "Save (draft)"}, {"tap": "/Display.*/"}, {"tap": {"id": "next", "index": 1}},
                        {"type": "hi"}, {"erase": 3}, {"press": "Enter"}, {"scroll_to": "Dark"},
                        {"scroll_to": {"element": {"id": "x"}, "direction": "up"}}, {"see": "A"}, {"not_see": "/B.*/"},
                        "launch", {"launch": {"clear_state": True}}, "stop", "hide_keyboard", "wait_idle"]) == [
        "back", {"tapOn": r"Save \(draft\)"}, {"tapOn": "Display.*"}, {"tapOn": {"id": "next", "index": 1}},
        {"inputText": "hi"}, {"eraseText": 3}, {"pressKey": "Enter"},
        {"scrollUntilVisible": {"element": "Dark", "direction": "DOWN"}},
        {"scrollUntilVisible": {"element": {"id": "x"}, "direction": "UP"}},
        {"assertVisible": "A"}, {"assertNotVisible": "B.*"},
        "launchApp", {"launchApp": {"clearState": True}}, "stopApp", "hideKeyboard", "waitForAnimationToEnd"]


def test_round_trip_back_to_neutral():
    neutral = [{"tap": "Save (draft)"}, {"tap": "/Display.*/"}, {"tap": {"id": "next"}}, {"type": "x"},
               {"press": "enter"}, {"scroll_to": "Dark"}, {"scroll_to": {"element": "Up", "direction": "up"}},
               {"see": "A"}, "back", "launch", {"launch": {"clear_state": True}}, "stop"]
    assert from_commands(to_commands(neutral)) == neutral


@pytest.mark.parametrize("bad, says", [
    ({"tapOn": "X"}, "another tool's word: use tap"),
    ("scrollUntilVisible", "use scroll_to"),
    ({"tap": ["a"]}, "a selector is"),
    ({"erase": "all"}, "number of characters"),
    ({"press": "volume_up"}, "press takes one of"),
    ({"tap": "a", "type": "b"}, "one-key map"),
])
def test_bad_actions_say_what_to_write(bad, says):
    with pytest.raises(ActionError, match=says):
        to_commands([bad])


def test_selector_text_is_exact_unless_slashed():
    assert to_selector("1+1=2?") == r"1\+1=2\?"
    assert to_selector({"text": "/a|b/"}) == {"text": "a|b"}


def test_a_pack_with_framework_words_does_not_validate(pack_dir):
    from journeys.pack import Pack
    (pack_dir / "journeys" / "extra.yaml").write_text(
        "name: extra\nsteps:\n  - do: [{tapOn: Display}]\n  - see: [x]\n")
    errs = Pack.load(pack_dir).errors
    assert any("extra.yaml:3" in e and "use tap" in e for e in errs)
    assert any("extra.yaml:4" in e and "a selector is" in e for e in errs)


def test_an_id_joins_the_two_halves_of_a_character():
    """YAML reads "\\uD83D\\uDE00" as two halves; an id holds the one character, as a text does."""
    from journeys.actions import to_selector
    halves = chr(0xD83D) + chr(0xDE00)
    assert to_selector({"id": halves}) == {"id": chr(0x1F600)} and to_selector({"id": "a"}) == {"id": "a"}

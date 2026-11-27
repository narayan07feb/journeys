"""The Driver contract: every driver runs the same flow commands with the same results.

Each real driver is wired to one pretend phone (tests/phone_sim.py), so these run without a device.
"""
from pathlib import Path

import pytest

from journeys.drivers.base import DriverError
from phone_sim import Phone, hooked, ids

APP = {
    "app": "com.example.app",
    "start": "HOME",
    "screens": {
        "HOME": {"elements": [
            {"kind": "Text", "text": "Welcome"},
            {"kind": "Field", "text": "Email", "id": "com.example.app:id/email"},
            {"kind": "Button", "text": "Next", "id": "com.example.app:id/next", "to": "SECOND"},
            {"kind": "Button", "text": "Help, FAQ, contact", "to": "HELP"},
        ]},
        "SECOND": {"elements": [{"kind": "Text", "text": "Second page"}, {"kind": "Text", "text": "Line one\nLine two"}]},
        "HELP": {"elements": [{"kind": "Text", "text": "Help"}]},
    },
}
DRIVERS = ["fake", "speedrun", "maestro", "appium"]


@pytest.fixture(params=DRIVERS)
def drv(request, tmp_path):
    phone = Phone(APP)
    d = hooked(request.param, phone, tmp_path)
    d.start("com.example.app", "emulator-5554")
    assert d.do(["launchApp"]).ok
    return d


def texts(d):
    return [e.text for e in d.look().elements]


def test_every_driver_can_read_and_set_a_global_setting(drv):
    """tap-all --dka needs it; the caller checks devices.allow_settings_changes first."""
    assert drv.global_setting("always_finish_activities", "1") in ("", "null", "0")
    assert drv.global_setting("always_finish_activities") == "1"


def test_launch_shows_the_start_screen(drv):
    assert "Welcome" in texts(drv) and "next" in ids(drv.look().elements)
    assert drv.app_alive()


def test_tap_by_text_id_and_regex_then_back(drv):
    assert drv.do([{"tapOn": "Next"}]).ok and "Second page" in texts(drv)
    assert drv.do(["back"]).ok and "Welcome" in texts(drv)
    assert drv.do([{"tapOn": {"id": "next"}}]).ok and "Second page" in texts(drv)
    assert drv.do(["back", {"tapOn": "Help.*"}]).ok and "Help" in texts(drv)


def test_text_and_ids_match_with_case_on_every_driver(drv):
    """Maestro alone matches without case by default (cli-2.12.1 Orchestra REGEX_OPTIONS); the driver turns it off."""
    assert not drv.do([{"tapOn": "NEXT"}]).ok and "Welcome" in texts(drv)
    assert not drv.do([{"tapOn": {"id": "NEXT"}}]).ok
    assert not drv.do([{"assertVisible": "welcome"}]).ok
    assert drv.do([{"assertNotVisible": "WELCOME"}]).ok


def test_dot_and_anchors_do_not_cross_lines_on_every_driver(drv):
    """Maestro alone reads . across lines and ^/$ at each line by default; the driver turns both off."""
    assert drv.do([{"tapOn": "Next"}, {"assertVisible": "Line one\nLine two"}]).ok
    assert not drv.do([{"assertVisible": "Line one.Line two"}]).ok  # no DOTALL
    assert not drv.do([{"assertVisible": "Line two"}]).ok  # whole text, not one line


def test_a_missing_element_fails_the_command(drv):
    res = drv.do([{"tapOn": "Nope"}])
    assert not res.ok and "Nope" in res.message
    assert "Welcome" in texts(drv)


def test_asserts(drv):
    assert drv.do([{"assertVisible": "Welcome"}, {"assertNotVisible": "Second page"}]).ok
    assert not drv.do([{"assertVisible": "Second page"}]).ok
    assert not drv.do([{"assertNotVisible": "Welcome"}]).ok
    assert drv.do([{"scrollUntilVisible": {"element": "Next"}}]).ok


def test_commands_stop_at_the_first_failure(drv):
    assert not drv.do([{"tapOn": "Nope"}, {"tapOn": "Next"}]).ok
    assert "Welcome" in texts(drv)


def test_typing_does_not_fail(drv):
    assert drv.do([{"tapOn": {"id": "email"}}, {"inputText": "a b"}, "hideKeyboard"]).ok


def test_every_neutral_action_runs_on_every_driver(drv):
    from journeys.actions import to_commands
    acts = [{"tap": {"id": "email"}}, {"type": "hi"}, {"erase": 2}, {"press": "enter"}, "hide_keyboard",
            "scroll", {"scroll_to": "Next"}, {"scroll_to": {"element": "Welcome", "direction": "up"}},
            "wait_idle", {"see": {"text": "Next", "index": 0}}, {"not_see": {"text": "Next", "index": 1}},
            {"launch": {"clear_state": True}}, {"tap": "Next"}]
    res = drv.do(to_commands(acts))
    assert res.ok, res.message
    assert "Second page" in texts(drv)
    assert not drv.do(to_commands([{"scroll_to": "Not here"}])).ok
    assert drv.do(["launchApp"]).ok and len(drv.look().elements) >= 3
    assert not drv.do(to_commands([{"tap": "@3"}])).ok  # neutral "@3" is text, not the 3rd element


def test_stop_and_relaunch(drv):
    assert drv.do(["stopApp"]).ok and not drv.app_alive()
    assert drv.do(["launchApp"]).ok and drv.app_alive() and "Welcome" in texts(drv)


def test_foreground_and_screenshot(drv, tmp_path):
    assert drv.foreground_app() == "com.example.app"
    assert drv.screenshot(tmp_path / "s.png") is not None


def test_driver_choice_has_no_silent_fallback(monkeypatch, pack_dir, tmp_path):
    from journeys import cli
    from journeys.pack import Pack
    from journeys.results import Store
    pack = Pack.load(pack_dir)
    every = {"maestro": "maestro is not on PATH", "speedrun": None, "appium": "no Appium server", "fake": None}
    monkeypatch.setattr(cli, "availability", lambda only=None: {k: v for k, v in every.items() if only in (None, k)})
    with pytest.raises(DriverError, match="maestro is not on PATH.*these can: speedrun"):
        cli.make_driver("maestro", pack, None, Store(tmp_path))
    for gone in ("uiautomator", "espresso"):  # Journeys has no driver of its own
        with pytest.raises(DriverError, match="not one of"):
            cli.make_driver(gone, pack, None, Store(tmp_path))


def test_a_dead_adb_link_is_a_driver_error_not_a_crash(tmp_path):
    import subprocess
    d = hooked("maestro", Phone(APP), tmp_path)
    d.app = "com.example.app"
    d._adb = lambda *a, **k: subprocess.CompletedProcess(a, 1, b"", b"error: device offline")
    with pytest.raises(DriverError, match="device offline"):
        d.app_alive()


def test_home_leaves_the_app_on_every_driver(drv):
    from journeys.actions import to_commands
    assert drv.do(to_commands([{"press": "home"}])).ok
    assert drv.foreground_app() != "com.example.app"


def test_focused_window_rules():
    from journeys.drivers.base import focused_package, focused_window
    assert focused_window("mCurrentFocus=Window{9f3 u0 NotificationShade}\n"
                          "mFocusedApp=ActivityRecord{12 u0 com.example.app/.Main t5}") == "window:NotificationShade"
    assert focused_package("mCurrentFocus=Window{1 u0 Application Not Responding: com.x}") == \
        "window:Application Not Responding: com.x"
    assert focused_window("mCurrentFocus=null\nmFocusedApp=ActivityRecord{12 u0 com.a/.Main t5}") == "com.a/.Main"
    assert focused_package("mCurrentFocus=Window{1 u0 com.a/com.a.Main}") == "com.a"
    assert focused_window("nothing") is None


def test_full_and_short_ids_match_on_every_driver(drv):
    assert drv.do([{"tapOn": {"id": "com.example.app:id/next"}}]).ok and "Second page" in texts(drv)
    assert drv.do(["back", {"tapOn": {"id": "next"}}]).ok


@pytest.mark.parametrize("kind", DRIVERS)
@pytest.mark.parametrize("want", ["a.b", "com.example.app:id/a.b"])
def test_an_id_with_pattern_characters_matches_only_that_name_on_every_driver(kind, want, tmp_path):
    """Maestro reads an id as a pattern (Filters.idMatches): "a.b" would also match "axb" there."""
    app = {"app": "com.example.app", "start": "HOME", "screens": {
        "HOME": {"elements": [{"kind": "Button", "text": "Decoy", "id": "com.example.app:id/axb", "to": "DECOY"},
                              {"kind": "Button", "text": "Target", "id": "com.example.app:id/a.b", "to": "TARGET"}]},
        "DECOY": {"elements": [{"kind": "Text", "text": "Wrong page"}]},
        "TARGET": {"elements": [{"kind": "Text", "text": "Right page"}]}}}
    d = hooked(kind, Phone(app), tmp_path)
    d.start("com.example.app", "emulator-5554")
    assert d.do(["launchApp", {"tapOn": {"id": want}}]).ok and texts(d) == ["Right page"]
    assert not d.do(["back", {"tapOn": {"id": "a.c"}}]).ok


TWO = {"app": "com.example.app", "start": "HOME", "screens": {
    "HOME": {"elements": [{"kind": "Button", "text": "Other", "id": "com.other:id/next", "to": "OTHER"},
                          {"kind": "Button", "text": "Ours", "id": "com.example.app:id/next", "to": "OURS"}]},
    "OTHER": {"elements": [{"kind": "Text", "text": "Other page"}]},
    "OURS": {"elements": [{"kind": "Text", "text": "Our page"}]}}}


@pytest.mark.parametrize("kind", DRIVERS)
def test_a_package_in_an_id_picks_that_package_on_every_driver(kind, tmp_path):
    d = hooked(kind, Phone(TWO), tmp_path)
    d.start("com.example.app", "emulator-5554")
    assert d.do(["launchApp", {"tapOn": {"id": "com.example.app:id/next"}}]).ok and texts(d) == ["Our page"]


@pytest.mark.parametrize("kind", DRIVERS)
def test_a_bare_id_counts_an_index_over_every_package_on_every_driver(kind, tmp_path):
    d = hooked(kind, Phone(TWO), tmp_path)
    d.start("com.example.app", "emulator-5554")
    assert d.do(["launchApp", {"tapOn": {"id": "next", "index": 1}}]).ok and texts(d) == ["Our page"]
    assert d.do(["back", {"tapOn": {"id": "next"}}]).ok and texts(d) == ["Other page"]


@pytest.mark.parametrize("kind", ["fake", "speedrun", "maestro"])
def test_a_next_line_character_in_an_id_reaches_the_driver(kind, tmp_path):
    app = {"app": "com.example.app", "start": "HOME", "screens": {
        "HOME": {"elements": [{"kind": "Button", "text": "Go", "id": "a\x85b", "to": "NEXT"}]},
        "NEXT": {"elements": [{"kind": "Text", "text": "Next page"}]}}}
    d = hooked(kind, Phone(app), tmp_path)
    d.start("com.example.app", "emulator-5554")
    assert d.do(["launchApp", {"tapOn": {"id": "a\x85b"}}]).ok and texts(d) == ["Next page"]


SHAPES = {"app": "com.example.app", "start": "HOME", "screens": {
    "HOME": {"elements": [{"kind": "Button", "text": "", "id": "com.other:id/a", "to": "P1"},
                          {"kind": "Button", "text": "", "id": "com.example.app:id/a", "to": "P2"},
                          {"kind": "Button", "text": "", "id": "b", "to": "P3"},
                          {"kind": "Button", "text": "", "id": "g/c", "to": "P4"},
                          {"kind": "Button", "text": "", "id": "com.example.app:id/d", "to": "P5"}]},
    **{f"P{i}": {"elements": [{"kind": "Text", "text": f"Page {i}"}]} for i in range(1, 6)}}}


@pytest.mark.parametrize("kind", DRIVERS)
def test_each_id_shape_round_trips_through_look_heal_and_tap_on_every_driver(kind, tmp_path):
    """look reports the whole resource-id; heal's selector names it by its name (with an index when two packages
    share it); the tap lands on that view on every driver."""
    from journeys.heal import selector
    d = hooked(kind, Phone(SHAPES), tmp_path)
    d.start("com.example.app", "emulator-5554")
    assert d.do(["launchApp"]).ok
    got = []
    for i in range(5):
        screen = d.look()
        el = screen.elements[i]
        sel = selector(el, screen)
        got.append((el.id, sel))
        assert d.do([{"tapOn": sel}]).ok and texts(d) == [f"Page {i + 1}"], (kind, el.id, sel)
        assert d.do(["back"]).ok
    assert got == [("com.other:id/a", {"id": "a", "index": 0}), ("com.example.app:id/a", {"id": "a", "index": 1}),
                   ("b", {"id": "b"}), ("g/c", {"id": "c"}), ("com.example.app:id/d", {"id": "d"})]


@pytest.mark.parametrize("kind", DRIVERS)
def test_a_map_rule_with_a_package_checks_the_package_on_every_driver(kind, tmp_path):
    from journeys.appmap import Rule
    d = hooked(kind, Phone(TWO), tmp_path)
    d.start("com.example.app", "emulator-5554")
    assert d.do(["launchApp"]).ok
    els = d.look().elements
    assert [Rule.parse({"id": w}).matches(e) for w in ("com.example.app:id/next", "next") for e in els] == \
        [False, True, True, True]


NESTED = {"app": "com.example.app", "start": "HOME", "screens": {
    "HOME": {"elements": [{"kind": "Button", "text": "", "id": "com.example.app:id/open", "to": "DECOY"},
                          {"kind": "Button", "text": "", "id": "com.example.app:id/inbox/open", "to": "INBOX"}]},
    "DECOY": {"elements": [{"kind": "Text", "text": "Plain open"}]},
    "INBOX": {"elements": [{"kind": "Text", "text": "Inbox"}]}}}


@pytest.mark.parametrize("kind", [k for k in DRIVERS if k != "maestro"])  # Maestro refuses a / in a name
def test_a_nested_name_is_picked_by_its_whole_id(kind, tmp_path):
    d = hooked(kind, Phone(NESTED), tmp_path)
    d.start("com.example.app", "emulator-5554")
    assert d.do(["launchApp", {"tapOn": {"id": "com.example.app:id/inbox/open"}}]).ok and texts(d) == ["Inbox"]


def test_id_rules():
    from journeys.appmap import Rule
    from journeys.drivers.base import Element, id_matches
    assert id_matches("com.x:id/foo", "foo") and id_matches("foo", "com.x:id/foo")
    assert id_matches("com.x:id/foo", "com.x:id/foo") and not id_matches("com.x:id/foo", "com.y:id/foo")
    assert not id_matches("foo", "foobar") and not id_matches("", "foo")
    assert Rule.parse({"id": "com.x:id/foo"}).matches(Element("@1", "Button", "", "foo"))


def test_maestro_driver_writes_maestro_selectors(tmp_path):
    flows = []
    phone = Phone(APP)
    d = hooked("maestro", phone, tmp_path)
    real = d._run

    def run(*args):
        if args[0] == "test":
            flows.append(Path(args[1]).read_text())
        return real(*args)
    d._run = run
    d.start("com.example.app", "R")
    d.do(["launchApp", {"scrollUntilVisible": {"element": "Next", "direction": "DOWN"}}])
    assert "element:\n      text: (?-ims)Next" in flows[-1]

def test_unreadable_maestro_hierarchy_is_a_driver_error(tmp_path):
    import subprocess
    d = hooked("maestro", Phone(APP), tmp_path)
    d.start("com.example.app", "R")
    d._run = lambda *a: subprocess.CompletedProcess(a, 0, '{"attributes": "not a map"}', "")
    with pytest.raises(DriverError, match="unreadable output"):
        d.look()


@pytest.mark.parametrize("kind", DRIVERS)
def test_tap_all_and_a_journey_run_the_same_on_every_driver(kind, tappable_pack_dir, tmp_path):
    """Every feature goes through the driver: tap-all and the runner give the same result on each."""
    import yaml
    from journeys.explore import tap_all
    from journeys.pack import Pack
    from journeys.results import Store
    from journeys.runner import Runner
    pack = Pack.load(tappable_pack_dir)
    app = yaml.safe_load((tappable_pack_dir / "fake" / "app.yaml").read_text())
    d = hooked(kind, Phone(app), tmp_path)
    d.start(pack.app, "emulator-5554")
    store = Store(tmp_path / "state")
    rep = tap_all(pack, d, store, store.new_run())
    assert {s["screen"] for s in rep.screens} == {
        "SETTINGS-HOME", "SETTINGS-SEARCH", "SETTINGS-DISPLAY", "SETTINGS-ABOUT"}, kind
    outs = Runner(pack, d, store, store.new_run(), heal=False).run_all(pack.select("smoke"))
    assert [o.status for o in outs] == ["passed", "passed"], [o.message for o in outs]


@pytest.mark.parametrize("step, says", [("{press: search}", "no Search key"), ("{tap: '/a{b/'}", "pattern subset")])
def test_a_step_maestro_cannot_run_fails_cleanly_in_a_journey(step, says, tappable_pack_dir, tmp_path):
    import yaml
    from journeys.pack import Pack
    from journeys.results import Store
    from journeys.runner import Runner
    (tappable_pack_dir / "journeys" / "nope.yaml").write_text(f"name: nope\nsteps:\n  - do: [{step}]\n")
    pack = Pack.load(tappable_pack_dir)
    app = yaml.safe_load((tappable_pack_dir / "fake" / "app.yaml").read_text())
    d = hooked("maestro", Phone(app), tmp_path)
    d.start(pack.app, "emulator-5554")
    store = Store(tmp_path / "state")
    out, = Runner(pack, d, store, store.new_run(), heal=False).run_all([pack.journeys["nope"]])
    assert out.status == "failed" and says in out.message, out.message

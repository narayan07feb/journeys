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
        "SECOND": {"elements": [{"kind": "Text", "text": "Second page"}]},
        "HELP": {"elements": [{"kind": "Text", "text": "Help"}]},
    },
}
DRIVERS = ["fake", "speedrun", "maestro"]


@pytest.fixture(params=DRIVERS)
def drv(request, tmp_path):
    phone = Phone(APP)
    d = hooked(request.param, phone, tmp_path)
    d.start("com.example.app", "emulator-5554")
    assert d.do(["launchApp"]).ok
    return d


def texts(d):
    return [e.text for e in d.look().elements]


def test_launch_shows_the_start_screen(drv):
    assert "Welcome" in texts(drv) and "next" in ids(drv.look().elements)
    assert drv.app_alive()


def test_tap_by_text_id_and_regex_then_back(drv):
    assert drv.do([{"tapOn": "Next"}]).ok and "Second page" in texts(drv)
    assert drv.do(["back"]).ok and "Welcome" in texts(drv)
    assert drv.do([{"tapOn": {"id": "next"}}]).ok and "Second page" in texts(drv)
    assert drv.do(["back", {"tapOn": "Help.*"}]).ok and "Help" in texts(drv)


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
    monkeypatch.setattr(cli, "availability", lambda: {"maestro": "maestro is not on PATH", "speedrun": None,
                                                      "fake": None})
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
    assert "element:\n      text: Next" in flows[-1]
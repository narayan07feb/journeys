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
DRIVERS = ["fake", "uiautomator", "speedrun", "maestro"]


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


def test_uiautomator_does_not_start_next_to_another_driver_or_blind(tmp_path):
    with pytest.raises(DriverError, match=r"another UI test driver.*dev\.mobile\.maestro"):
        hooked("uiautomator", Phone(APP, busy="dev.mobile.maestro\n"), tmp_path).start("com.example.app", "R58M")
    with pytest.raises(DriverError, match="could not list"):
        hooked("uiautomator", Phone(APP, ps_works=False), tmp_path).start("com.example.app", "R58M")
    with pytest.raises(DriverError, match="not an Android package"):
        hooked("uiautomator", Phone(APP), tmp_path).start("com.x; rm -rf /", "R58M")


def test_uiautomator_writes_no_setting_and_touches_only_the_pack_app(tmp_path):
    phone = Phone(APP)
    d = hooked("uiautomator", phone, tmp_path)
    d.start("com.example.app", "R58M")
    assert d.do(["launchApp", {"tapOn": "Next"}, "back", {"inputText": "it's me"}, "stopApp"]).ok
    assert not d.do([{"stopApp": "com.other"}]).ok and not d.do([{"launchApp": {"appId": "com.other"}}]).ok
    assert not any(c.startswith(("settings put", "settings delete", "pm install", "rm ", "pm uninstall"))
                   for c in phone.shell_log)
    assert phone.typed == ["'it'\"'\"'s%sme'"]
    assert not d.do([{"inputText": "naïve"}]).ok and not d.do([{"inputText": "100%s"}]).ok
    assert {c.split()[-1] for c in phone.shell_log if c.startswith("am force-stop")} == {"com.example.app"}


def test_driver_choice_has_no_silent_fallback(monkeypatch, pack_dir, tmp_path):
    from journeys import cli
    from journeys.pack import Pack
    from journeys.results import Store
    pack = Pack.load(pack_dir)
    monkeypatch.setattr(cli, "availability", lambda: {"uiautomator": None, "maestro": "maestro is not on PATH",
                                                      "speedrun": None, "fake": None})
    with pytest.raises(DriverError, match="maestro is not on PATH.*these can: uiautomator, speedrun"):
        cli.make_driver("maestro", pack, None, Store(tmp_path))
    from journeys.drivers.uiautomator import UiAutomatorDriver
    assert isinstance(cli.make_driver("uiautomator", pack, None, Store(tmp_path)), UiAutomatorDriver)
    with pytest.raises(DriverError, match="not one of"):
        cli.make_driver("espresso", pack, None, Store(tmp_path))


def test_uiautomator_taps_only_in_the_pack_app_and_survives_odd_output(tmp_path):
    other = dict(APP, app="com.other.app")
    phone = Phone(other)
    d = hooked("uiautomator", phone, tmp_path)
    d.app = "com.example.app"  # the pack's app is not on screen
    phone.app.do(["launchApp"])
    assert d.look().elements == []  # another app's texts never pass for the pack's
    res = d.do([{"tapOn": "Next"}])
    assert not res.ok and not any(c.startswith("input tap") for c in phone.shell_log)
    assert not d.do([{"assertNotVisible": "Next"}]).ok  # nothing proven while another app shows


LONG = {"app": "com.example.app", "start": "LIST", "screens": {"LIST": {"elements": [
    {"kind": "Button", "text": f"Row {i}", **({"to": "END"} if i == 7 else {})} for i in range(10)]},
    "END": {"elements": [{"kind": "Text", "text": "Row 7 opened"}]}}}


@pytest.mark.parametrize("kind", ["uiautomator"])
def test_scroll_until_visible_scrolls_and_stops_at_the_end(kind, tmp_path):
    phone = Phone(LONG, page=3)
    d = hooked(kind, phone, tmp_path)
    d.start("com.example.app", "R58M")
    assert d.do(["launchApp", {"scrollUntilVisible": {"element": "Row 7", "direction": "DOWN"}}]).ok
    assert "Row 7" in texts(d) and "Row 0" not in texts(d)
    assert d.do([{"tapOn": "Row 7"}]).ok and "Row 7 opened" in texts(d)
    assert d.do(["back"]).ok
    swipes = sum(c.startswith("input swipe") for c in phone.shell_log)
    res = d.do([{"scrollUntilVisible": {"element": "Row 99", "direction": "DOWN", "maxScrolls": 50}}])
    assert not res.ok and sum(c.startswith("input swipe") for c in phone.shell_log) - swipes < 15  # stops at the end


def test_uiautomator_waits_and_retries_a_bad_dump(tmp_path):
    from journeys.drivers.uiautomator import UiAutomatorDriver
    phone = Phone(APP)
    t = [0.0]
    d = UiAutomatorDriver(wait_s=2, poll_s=0.5, sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0])
    calls = {"n": 0}

    def adb(*args, timeout=60):
        if args[:2] == ("exec-out", "uiautomator"):
            calls["n"] += 1
            if calls["n"] <= 3:  # two bad dumps (tty + file fallback each) then a good one
                return phone.adb("shell", "nonsense")
        return phone.adb(*args)
    d._adb = adb
    d.start("com.example.app", "R58M")
    phone.app.do(["launchApp"])
    assert d.do([{"assertVisible": "Welcome"}]).ok and t[0] > 0
    t[0] = 0
    assert not d.do([{"assertVisible": "Never"}]).ok and t[0] >= 2  # waited the full wait_s


@pytest.mark.parametrize("cmd", [{"tapOn": "Next"}, "back", {"pressKey": "Enter"}, {"inputText": "hunter2"},
                                 {"eraseText": 3}, "scroll",
                                 {"scrollUntilVisible": {"element": "Not here", "direction": "DOWN"}}])
def test_uiautomator_sends_no_input_to_another_window(cmd, tmp_path):
    phone = Phone(APP)
    d = hooked("uiautomator", phone, tmp_path)
    d.start("com.example.app", "R58M")
    phone.app.do(["launchApp"])
    phone.focus = "com.other.app/.Dialog"
    res = d.do([cmd])
    assert not res.ok and "com.other.app" in res.message
    assert not [c for c in phone.shell_log if c.startswith("input")]
    phone.focus = "com.google.android.permissioncontroller/com.android.permissioncontroller.permission.ui.GrantPermissionsActivity"
    assert d.do([{"pressKey": "Enter"}]).ok  # the permission-grant dialog may take input
    phone.focus = "com.google.android.permissioncontroller/com.android.permissioncontroller.permission.ui.ManagePermissionsActivity"
    assert not d.do([{"pressKey": "Enter"}]).ok  # other permission screens may not


def test_uiautomator_shell_commands_are_an_allowlist(tmp_path):
    import re
    phone = Phone(LONG, page=3)
    d = hooked("uiautomator", phone, tmp_path)
    d.start("com.example.app", "R58M")
    d.do(["launchApp", {"launchApp": {"clearState": True}}, {"scrollUntilVisible": {"element": "Row 7"}},
          {"tapOn": "Row 7"}, "back", {"pressKey": "Enter"}, {"inputText": "a b"}, {"eraseText": 2}, "hideKeyboard",
          "scroll", "stopApp"])
    d.app_alive(), d.foreground_app(), d.is_emulator()
    allowed = re.compile(r"(ps -A -o NAME|dumpsys (window|input_method)|pidof com\.example\.app|"
                         r"getprop ro\.kernel\.qemu|cmd package resolve-activity --brief -c "
                         r"android\.intent\.category\.LAUNCHER com\.example\.app|am start -W -n \S+|"
                         r"am force-stop com\.example\.app|pm clear com\.example\.app|input (tap|swipe|keyevent|text) .*)")
    assert [c for c in phone.shell_log if not allowed.fullmatch(c)] == []
    assert any(c.startswith("pm clear") for c in phone.shell_log)


def test_uiautomator_refuses_next_to_espresso_and_parses_control_chars(tmp_path):
    from journeys.drivers.uiautomator import parse_dump
    with pytest.raises(DriverError, match="another UI test driver"):
        hooked("uiautomator", Phone(APP, busy="com.shop.test\n"), tmp_path).start("com.example.app", "R58M")
    p = Phone(APP)
    p.app.do(["launchApp"])
    xml = p.dump_xml().replace('text="Welcome"', 'text="Wel&#1;come"')
    assert "Welcome" in [e.text for e in parse_dump(xml)[0].elements]
    with pytest.raises(DriverError, match="not valid XML"):
        parse_dump("<hierarchy><node></hierarchy>")


def test_uiautomator_checks_focus_right_before_each_input(tmp_path):
    """Focus moves to another app while the driver waits for the element: no tap goes out."""
    phone = Phone(APP)
    d = hooked("uiautomator", phone, tmp_path)
    d.start("com.example.app", "R58M")
    phone.app.do(["launchApp"])
    real = phone.adb
    looks = {"n": 0}

    def adb(*args, timeout=60):
        if args[:2] == ("exec-out", "uiautomator"):
            looks["n"] += 1
            phone.focus = "com.other.app/.Dialog"  # a dialog takes focus once the wait starts
        return real(*args)
    d._adb = adb
    res = d.do([{"tapOn": "Next"}])
    assert not res.ok and "com.other.app" in res.message and looks["n"] >= 1
    assert not [c for c in phone.shell_log if c.startswith("input")]


def test_uiautomator_waits_for_a_brief_null_focus_and_reads_the_rotated_size(tmp_path):
    from journeys.drivers.uiautomator import UiAutomatorDriver
    phone = Phone(APP)
    t = [0.0]
    d = UiAutomatorDriver(wait_s=2, poll_s=0.5, sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0])
    nulls = {"left": 2}

    def adb(*args, timeout=60):
        if args[1:] == ("dumpsys", "window") and nulls["left"]:
            nulls["left"] -= 1
            return phone.adb("shell", "nonsense")  # no focus line, as during an activity change
        if args[:2] == ("exec-out", "uiautomator"):
            return phone.adb(*args).__class__(args, 0, phone.dump_xml().replace("[0,0][1080,2400]", "[0,0][2400,1080]")
                                              .replace("[0,200][1080,290]", "[2200,200][2800,290]").encode(), b"")
        return phone.adb(*args)
    d._adb = adb
    d.start("com.example.app", "R58M")
    phone.app.do(["launchApp"])
    assert d.do([{"tapOn": "Next"}]).ok and t[0] > 0  # waited out the null focus
    assert "input tap 2399 245" in phone.shell_log  # clamped to the rotated width, not 1080


def test_home_leaves_the_app_on_every_driver(drv):
    from journeys.actions import to_commands
    assert drv.do(to_commands([{"press": "home"}])).ok
    assert drv.foreground_app() != "com.example.app"


@pytest.mark.parametrize("focus", ["NotificationShade", "Application Not Responding: com.x", "StatusBar"])
def test_uiautomator_sends_nothing_to_system_windows(focus, tmp_path):
    phone = Phone(APP)
    d = hooked("uiautomator", phone, tmp_path)
    d.start("com.example.app", "R58M")
    phone.app.do(["launchApp"])
    phone.focus = focus
    assert not d.do([{"pressKey": "Enter"}]).ok and not d.do([{"inputText": "1234"}]).ok
    assert not [c for c in phone.shell_log if c.startswith("input")]
    phone.focus = "null"  # nothing focused (screen off, locking): the app behind does not count
    assert not d.do([{"pressKey": "Enter"}]).ok
    assert not [c for c in phone.shell_log if c.startswith("input")]


def test_focused_window_rules():
    from journeys.drivers.base import focused_package, focused_window
    assert focused_window("mCurrentFocus=Window{9f3 u0 NotificationShade}\n"
                          "mFocusedApp=ActivityRecord{12 u0 com.example.app/.Main t5}") == "window:NotificationShade"
    assert focused_package("mCurrentFocus=Window{1 u0 Application Not Responding: com.x}") == \
        "window:Application Not Responding: com.x"
    assert focused_window("mCurrentFocus=null\nmFocusedApp=ActivityRecord{12 u0 com.a/.Main t5}") == "com.a/.Main"
    assert focused_package("mCurrentFocus=Window{1 u0 com.a/com.a.Main}") == "com.a"
    assert focused_window("nothing") is None


def test_uiautomator_odd_text_counts_keyboard_and_own_app_name(tmp_path):
    phone = Phone(APP)
    d = hooked("uiautomator", phone, tmp_path)
    d.start("com.example.app", "R58M")
    phone.app.do(["launchApp"])
    assert not d.do([{"inputText": "a\nb"}]).ok and not d.do([{"eraseText": 10 ** 6}]).ok
    assert not d.do([{"eraseText": [1]}]).ok
    phone.keyboard = True
    assert d.do(["hideKeyboard"]).ok and "input keyevent 4" in phone.shell_log
    test_app = dict(APP, app="com.example.app.test")
    hooked("uiautomator", Phone(test_app, busy="com.example.app.test\n"), tmp_path).start("com.example.app.test", "R")


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


def test_uiautomator_never_shows_a_refused_text(tmp_path):
    phone = Phone(APP)
    d = hooked("uiautomator", phone, tmp_path)
    d.start("com.example.app", "R58M")
    phone.app.do(["launchApp"])
    res = d.do([{"inputText": "hunter%s2"}])
    assert not res.ok and "hunter" not in res.message and "9 characters" in res.message


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


def test_uiautomator_app_packages_and_the_first_focus_line(tmp_path):
    from journeys.drivers.uiautomator import UiAutomatorDriver
    phone = Phone(APP)
    d = UiAutomatorDriver(wait_s=0, sleep=lambda s: None, app_packages=("com.example.search",))
    d._adb = phone.adb
    d.start("com.example.app", "R")
    phone.app.do(["launchApp"])
    phone.focus = "com.example.search/.SearchActivity"
    assert d.do([{"pressKey": "Enter"}]).ok  # a declared package of the app
    phone.focus = "com.other/.X"
    assert not d.do([{"pressKey": "Enter"}]).ok
    real = phone.adb
    d._adb = lambda *a, **k: (subprocess_done(b"mCurrentFocus=Window{1 u0 com.example.app/.Main}\n"
                                              b"mCurrentFocus=null\n") if a[1:] == ("dumpsys", "window") else real(*a))
    assert d.do([{"pressKey": "Enter"}]).ok  # a later display's null line does not override the first


def subprocess_done(out):
    import subprocess
    return subprocess.CompletedProcess([], 0, out, b"")


def test_adb_errors_are_driver_errors_not_crashes_or_empty(tmp_path):
    import subprocess
    d = hooked("uiautomator", Phone(APP), tmp_path)
    d.app = "com.example.app"
    d._adb = lambda *a, **k: subprocess.CompletedProcess(a, 1, b"", b"error: device offline")
    with pytest.raises(DriverError, match="device offline"):
        d.look()
    with pytest.raises(DriverError, match="device offline"):
        d.app_alive()
    d2 = hooked("uiautomator", Phone(APP), tmp_path)
    d2.start("com.example.app", "R")
    assert not d2.do([{"scrollUntilVisible": {}}]).ok

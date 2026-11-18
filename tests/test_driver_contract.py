"""The Driver contract: every driver runs the same flow commands with the same results.

Each real driver is wired to one pretend phone (tests/phone_sim.py), so these run without a device.
"""
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


def test_stop_and_relaunch(drv):
    assert drv.do(["stopApp"]).ok and not drv.app_alive()
    assert drv.do(["launchApp"]).ok and drv.app_alive() and "Welcome" in texts(drv)


def test_foreground_and_screenshot(drv, tmp_path):
    assert drv.foreground_app() == "com.example.app"
    assert drv.screenshot(tmp_path / "s.png") is not None


@pytest.mark.parametrize("kind", ["uiautomator"])
def test_uiautomator_does_not_start_next_to_another_driver(kind, tmp_path):
    phone = Phone(APP, busy="dev.mobile.maestro\n")
    with pytest.raises(DriverError, match="another UI test driver"):
        hooked(kind, phone, tmp_path).start("com.example.app", "R58M")


def test_uiautomator_writes_no_setting_and_touches_only_the_pack_app(tmp_path):
    phone = Phone(APP)
    d = hooked("uiautomator", phone, tmp_path)
    d.start("com.example.app", "R58M")
    assert d.do(["launchApp", {"tapOn": "Next"}, "back", {"inputText": "it's me"}, "stopApp"]).ok
    assert not d.do([{"stopApp": "com.other"}]).ok and not d.do([{"launchApp": {"appId": "com.other"}}]).ok
    assert not any(c.startswith(("settings put", "settings delete", "pm install", "rm ", "pm uninstall"))
                   for c in phone.shell_log)
    assert phone.typed == ["'it'\"'\"'s%sme'"]
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

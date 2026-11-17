import pytest
import yaml

from journeys.drivers.base import DriverError
from journeys.drivers.fake import FakeDriver
from journeys.explore import DKA, tap_all
from journeys.pack import Pack
from journeys.results import Store


@pytest.fixture
def t(tappable_pack_dir, tmp_path):
    pack_dir = tappable_pack_dir
    app = yaml.safe_load((pack_dir / "fake" / "app.yaml").read_text())
    store = Store(tmp_path / "state")
    driver = FakeDriver(app)

    def walk(**kw):
        pack = Pack.load(pack_dir)
        run = store.new_run()
        return tap_all(pack, driver, store, run, **kw), run

    return app, driver, store, walk


def test_walks_every_screen_of_the_example(t):
    app, driver, store, walk = t
    rep, run = walk()
    assert {s["screen"] for s in rep.screens} == {
        "SETTINGS-HOME", "SETTINGS-SEARCH", "SETTINGS-DISPLAY", "SETTINGS-ABOUT"}
    assert rep.crashes == [] and rep.dead_ends == [] and rep.new_screens == []
    assert store.run(run.id)["status"] == "passed"


def test_crash_dead_end_and_new_screen(t):
    app, driver, store, walk = t
    app["screens"]["ABOUT"]["elements"] += [
        {"kind": "Button", "text": "Legal", "to": "LEGAL"},
        {"kind": "Button", "text": "Update", "to": "CRASH"},
    ]
    app["screens"]["LEGAL"] = {"elements": [
        {"kind": "Text", "text": "Legal info"}, {"kind": "Button", "text": "Licenses", "to": "TRAP"}]}
    app["screens"]["TRAP"] = {"elements": [{"kind": "Text", "text": "No way out"}], "back": "TRAP"}
    rep, run = walk()
    assert [c["tap"] for c in rep.crashes] == [{"tapOn": "Update"}]
    f = store.failure(rep.crashes[0]["failure"])
    assert f["log"].startswith("FATAL EXCEPTION") and f["route"][-1] == {"tapOn": "Update"}
    assert any(n.startswith("NEW:LEGAL-INFO:") for n in rep.new_screens)
    assert any(n.startswith("NEW:NO-WAY-OUT:") for n in rep.new_screens)
    assert [d["screen"].split(":")[1] for d in rep.dead_ends] == ["NO-WAY-OUT"]
    legal = next(s for s in rep.screens if s["screen"].startswith("NEW:LEGAL"))
    assert legal["route"] == [{"tapOn": "About phone.*"}, {"tapOn": "Legal"}]
    assert store.run(run.id)["status"] == "failed"


def test_max_screens(t):
    app, driver, store, walk = t
    rep, run = walk(max_screens=2)
    assert len(rep.screens) == 2


def test_dka_set_and_restored_and_finds_dka_crash(t):
    app, driver, store, walk = t
    app["screens"]["DISPLAY"]["elements"].append({"kind": "Button", "text": "Fonts", "dka_crash": True})
    rep, run = walk()
    assert rep.crashes == []
    driver.settings[DKA] = "0"
    rep, run = walk(dka=True)
    assert [c["tap"] for c in rep.crashes] == [{"tapOn": "Fonts"}]
    assert driver.settings[DKA] == "0"  # put back


def test_dka_refused_on_a_real_device(t):
    app, driver, store, walk = t
    driver.emulator = False
    with pytest.raises(DriverError):
        walk(dka=True)
    assert DKA not in driver.settings


def test_deny_list_and_leaving_the_app(t):
    app, driver, store, walk = t
    app["screens"]["HOME"]["elements"] += [
        {"kind": "Switch", "text": "Wi-Fi", "to": "CRASH"},              # denied by default: never tapped
        {"kind": "Button", "text": "Help", "to": "BROWSER"},
    ]
    app["screens"]["BROWSER"] = {"package": "com.android.chrome", "elements": [
        {"kind": "Text", "text": "Help center"}, {"kind": "Button", "text": "Close all", "to": "CRASH"}]}
    rep, run = walk()
    assert rep.crashes == []
    assert {"tapOn": "Wi-Fi"} not in driver.log
    assert rep.left_app == [{"screen": "SETTINGS-HOME", "tap": {"tapOn": "Help"}, "package": "com.android.chrome"}]
    assert {"tapOn": "Close all"} not in driver.log  # the other app is never walked
    assert not any(n.startswith("NEW:HELP") for n in rep.new_screens)


def test_pack_deny_rules(t, tappable_pack_dir):
    app, driver, store, walk = t
    f = tappable_pack_dir / "pack.yaml"
    f.write_text(f.read_text() + "  deny: [{text: /^Battery/}]\n")
    walk()
    assert {"tapOn": "Battery.*"} not in driver.log and {"tapOn": "Apps.*"} in driver.log


def test_settings_example_refuses_taps(pack_dir, tmp_path):
    pack = Pack.load(pack_dir)
    assert pack.tap_allowed is False
    store = Store(tmp_path / "s")
    with pytest.raises(DriverError, match="allowed: false"):
        tap_all(pack, FakeDriver(yaml.safe_load((pack_dir / "fake" / "app.yaml").read_text())), store, store.new_run())


def test_dynamic_screen_found_again_by_heading(tappable_pack_dir, tmp_path):
    """A game table differs after each launch (a new deal): the same heading is the same screen."""
    app = yaml.safe_load((tappable_pack_dir / "fake" / "app.yaml").read_text())
    app["screens"]["HOME"]["elements"].append({"kind": "Button", "text": "Play now", "to": "TABLE"})
    app["screens"]["TABLE"] = {"elements": [
        {"kind": "Text", "text": "Your turn"}, {"kind": "Text", "text": "Red 7"}, {"kind": "Text", "text": "Green 1"}]}

    class Dealing(FakeDriver):
        deals = 0

        def _one(self, cmd):
            if cmd == "launchApp":
                self.deals += 1
                app["screens"]["TABLE"]["elements"][1:] = [
                    {"kind": "Text", "text": f"Red {self.deals}"}, {"kind": "Text", "text": f"Blue {self.deals}"}]
            return super()._one(cmd)

    store = Store(tmp_path / "s")
    rep = tap_all(Pack.load(tappable_pack_dir), Dealing(app), store, store.new_run())
    assert any(s["screen"].startswith("NEW:YOUR-TURN") for s in rep.screens)
    assert rep.unreachable == []


def test_in_app_back_to_a_known_screen_is_not_a_dead_end(t):
    app, driver, store, walk = t
    app["screens"]["ABOUT"]["elements"].append({"kind": "Button", "text": "Done", "to": "HOME", "replace": True})
    rep, run = walk()
    assert rep.dead_ends == []
    about = next(s for s in rep.screens if s["screen"] == "SETTINGS-ABOUT")
    assert {"tap": {"tapOn": "Done"}, "result": "SETTINGS-HOME"} in about["options"]

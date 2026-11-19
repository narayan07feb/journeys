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
        {"kind": "Button", "text": "Refresh", "to": "CRASH"},
    ]
    app["screens"]["LEGAL"] = {"elements": [
        {"kind": "Text", "text": "Legal info"}, {"kind": "Button", "text": "Licenses", "to": "TRAP"}]}
    app["screens"]["TRAP"] = {"elements": [{"kind": "Text", "text": "No way out"}], "back": "TRAP"}
    rep, run = walk()
    assert [c["tap"] for c in rep.crashes] == [{"tap": "Refresh"}]
    f = store.failure(rep.crashes[0]["failure"])
    assert f["log"].startswith("FATAL EXCEPTION") and f["route"][-1] == {"tap": "Refresh"}
    assert any(n.startswith("NEW:LEGAL-INFO:") for n in rep.new_screens)
    assert any(n.startswith("NEW:NO-WAY-OUT:") for n in rep.new_screens)
    assert [d["screen"].split(":")[1] for d in rep.dead_ends] == ["NO-WAY-OUT"]
    legal = next(s for s in rep.screens if s["screen"].startswith("NEW:LEGAL"))
    assert legal["route"] == [{"tap": "/About phone.*/"}, {"tap": "Legal"}]  # neutral words: copy-ready
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
    assert [c["tap"] for c in rep.crashes] == [{"tap": "Fonts"}]
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
    assert rep.left_app == [{"screen": "SETTINGS-HOME", "tap": {"tap": "Help"}, "package": "com.android.chrome"}]
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
    assert {"tap": {"tap": "Done"}, "result": "SETTINGS-HOME"} in about["options"]


def test_tap_targets_skip_label_less_elements_and_name_repeated_ids():
    from journeys.drivers.base import Element, Screen
    from journeys.heal import selector, tap_targets
    from journeys.pack import DEFAULT_DENY
    s = Screen([Element("@1", "View", "", "android:id/content"),
                Element("@2", "Text", "Row A", "com.a:id/title"), Element("@3", "Text", "Row B", "com.a:id/title"),
                Element("@4", "Button", "", "com.a:id/icon"), Element("@5", "Button", "", "com.a:id/icon"),
                Element("@6", "Button", "Delete account"), Element("@7", "Button", "Save", "com.a:id/save")])
    targets = tap_targets(s, DEFAULT_DENY, 15)
    assert [e.ref for e in targets] == ["@2", "@3", "@7"]
    from journeys.drivers.base import find
    for e in targets + [s.elements[4]]:  # every selector finds that element again, and only it
        assert find(s.elements, selector(e, s)) == s.elements.index(e)
    assert selector(s.elements[2], s) == "Row B" and selector(s.elements[4], s) == {"id": "com.a:id/icon", "index": 1}


def test_a_prefix_selector_never_lands_on_a_denied_sibling(pack_dir):
    from journeys.drivers.base import Element, Screen, find
    from journeys.heal import selector
    s = Screen([Element("@1", "Button", "Close account, permanent"), Element("@2", "Button", "Close, window"),
                Element("@3", "Button", "@1"), Element("@4", "Button", "Ok")])
    assert find(s.elements, selector(s.elements[1], s)) == 1  # not "Close.*", which hits @1 first
    assert find(s.elements, selector(s.elements[2], s)) == 2  # the text "@1" is not the ref @1
    twins = Screen([Element("@1", "Button", "Same"), Element("@2", "Button", "Same")])
    assert selector(twins.elements[1], twins) == {"text": "Same", "index": 1}


def test_deny_reads_the_id_name_and_tab_kinds_are_tapped(t):
    app, driver, store, walk = t
    app["screens"]["HOME"]["elements"] += [
        {"kind": "Button", "text": "OK", "id": "com.x:id/delete_account", "to": "CRASH"},  # the id says what it does
        {"kind": "Tab", "text": "Updates", "to": "UPDATES"},
    ]
    app["screens"]["UPDATES"] = {"elements": [{"kind": "Text", "text": "All updates"}]}
    rep, run = walk()
    assert rep.crashes == [] and "OK" not in str(driver.log)
    assert any(n.startswith("NEW:ALL-UPDATES") for n in rep.new_screens)


def test_after_a_crash_the_walk_goes_back_and_taps_the_rest(t):
    app, driver, store, walk = t
    app["screens"]["ABOUT"]["elements"][1:1] = [
        {"kind": "Button", "text": "Refresh", "to": "CRASH"},
        {"kind": "Button", "text": "Legal", "to": "LEGAL"},
    ]
    app["screens"]["LEGAL"] = {"elements": [{"kind": "Text", "text": "Legal info"}]}
    rep, run = walk()
    assert [c["tap"] for c in rep.crashes] == [{"tap": "Refresh"}]
    assert any(n.startswith("NEW:LEGAL-INFO") for n in rep.new_screens)


def test_a_selector_that_also_finds_another_row_is_not_used():
    from journeys.drivers.base import Element, Screen
    from journeys.heal import selector
    rows = [Element("@1", "Button", "Account, Profile"), Element("@2", "Button", "Account, Delete")]
    sel = selector(rows[0], Screen(rows))
    assert sel in ("Account, Profile", {"text": "Account, Profile", "index": 0}), sel


def test_camel_case_ids_zero_width_labels_and_siblings_of_denied_ones():
    from journeys.drivers.base import Element, Screen
    from journeys.heal import tap_targets
    from journeys.pack import DEFAULT_DENY
    els = [Element("@1", "Button", "OK", "btnDeleteAccount"), Element("@2", "Button", "Log​out"),
           Element("@3", "Button", "OK"), Element("@4", "Button", "Next", "row"),
           Element("@5", "Button", "Disable account", "row"), Element("@6", "Button", "Help")]
    assert [e.text for e in tap_targets(Screen(els), DEFAULT_DENY, 20)] == ["Help"]
    for text in ("Terminate session", "Cancel plan", "Opt out", "Install", "Clear"):
        assert any(r.matches(Element("@1", "Button", text)) for r in DEFAULT_DENY), text


def test_after_leaving_the_app_the_walk_comes_back_for_the_rest(t):
    app, driver, store, walk = t
    app["screens"]["HOME"]["elements"].insert(1, {"kind": "Button", "text": "Help", "to": "BROWSER"})
    app["screens"]["BROWSER"] = {"package": "com.android.chrome", "elements": [{"kind": "Text", "text": "Help center"}]}
    rep, run = walk()
    assert rep.left_app and "SETTINGS-DISPLAY" in {s["screen"] for s in rep.screens}


def test_a_lost_link_mid_walk_ends_the_run_as_failed(t):
    app, driver, store, walk = t
    real = driver._one

    def lost(cmd):
        if isinstance(cmd, dict) and "tapOn" in cmd:
            raise DriverError("adb link lost")
        return real(cmd)
    driver._one = lost
    with pytest.raises(DriverError):
        walk()
    (run,) = store.runs()
    assert run["status"] == "failed" and "link lost" in run["error"]


def test_any_stop_ends_the_run_failed_with_an_end_time(t):
    app, driver, store, walk = t
    real = driver._one
    driver._one = lambda cmd: (_ for _ in ()).throw(KeyboardInterrupt()) if isinstance(cmd, dict) else real(cmd)
    with pytest.raises(KeyboardInterrupt):
        walk()
    driver._one = real
    driver.do = lambda cmds: type("R", (), {"ok": False, "message": "did not start"})()
    with pytest.raises(DriverError):
        walk()
    runs = store.runs()
    assert [r["status"] for r in runs] == ["failed", "failed"] and all(r.get("ended") for r in runs)
    assert {"KeyboardInterrupt"} <= {r["error"] for r in runs}


def test_dka_setup_and_restore_failures_are_on_the_run(t):
    app, driver, store, walk = t
    real = driver.global_setting
    driver.global_setting = lambda *a: (_ for _ in ()).throw(DriverError("adb gone"))
    with pytest.raises(DriverError):
        walk(dka=True)
    calls = []

    def restore_fails(name, value=None):
        calls.append(value)
        if len(calls) > 1:
            raise DriverError("adb gone")
        return real(name, value)
    driver.global_setting = restore_fails
    rep, run = walk(dka=True)
    (first,) = [r for r in store.runs() if r["id"] != run.id]
    assert first["status"] == "failed" and first["ended"] and "adb gone" in first["error"]
    assert "set always_finish_activities back to 0 by hand" in store.run(run.id)["dka_restore_error"]


def test_a_new_screen_key_never_shows_a_secret(t):
    from journeys.results import set_secrets
    app, driver, store, walk = t
    app["screens"]["ABOUT"]["elements"].append({"kind": "Button", "text": "Account", "to": "ACCT"})
    app["screens"]["ACCT"] = {"elements": [{"kind": "Text", "text": "hello hunter2"}]}
    set_secrets(["hunter2"])
    try:
        rep, run = walk()
    finally:
        set_secrets([])
    assert rep.new_screens and not any("HUNTER2" in n.upper() for n in rep.new_screens)


def test_more_destructive_words_are_never_tapped():
    from journeys.drivers.base import Element
    from journeys.pack import DEFAULT_DENY
    for text in ("Clear browsing data", "Clear app data", "Clear notifications", "Submit", "Publish", "Post",
                 "Unfollow", "Unfriend", "Leave group", "Unpair", "Switch off", "Hang up", "WLAN",
                 "Mobile network", "Exit"):
        assert any(r.matches(Element("@1", "Button", text)) for r in DEFAULT_DENY), text
    for text in ("Display", "About phone", "Posts and photos", "Next"):
        assert not any(r.matches(Element("@1", "Button", text)) for r in DEFAULT_DENY), text


def test_tap_all_console_masks_like_the_store(tappable_pack_dir, capsys, monkeypatch):
    from journeys.cli import main
    f = tappable_pack_dir / "fake" / "app.yaml"
    app = yaml.safe_load(f.read_text())
    app["screens"]["ABOUT"]["elements"].append({"kind": "Button", "text": "hunter2 account", "to": "CRASH"})
    f.write_text(yaml.safe_dump(app))
    monkeypatch.setenv("DEMO_SEARCH", "hunter2")
    args = ["--pack", str(tappable_pack_dir), "--state", str(tappable_pack_dir / "s"), "tap-all", "--driver", "fake"]
    assert main(args) == 1
    out = capsys.readouterr().out
    assert "crash" in out and "hunter2" not in out
    with pytest.raises(SystemExit):  # a bad run id is a usage error, not a traceback after the driver started
        main(["--pack", str(tappable_pack_dir), "run", "all", "--driver", "fake", "--run-id", "../x"])


def test_connection_cutting_switches_are_never_tapped():
    from journeys.drivers.base import Element
    from journeys.pack import DEFAULT_DENY
    for text in ("Wireless debugging", "ADB over network", "Cellular data", "Data roaming", "VPN", "Tethering",
                 "Internet"):
        assert any(r.matches(Element("@1", "Switch", text)) for r in DEFAULT_DENY), text


def test_stop_and_telephony_words_are_never_tapped():
    from journeys.drivers.base import Element
    from journeys.pack import DEFAULT_DENY
    for text in ("Stop", "Unregister", "Deauthorize", "Dial", "SOS", "Refund", "Abort", "Kill app", "Delink"):
        assert any(r.matches(Element("@1", "Button", text)) for r in DEFAULT_DENY), text


def test_a_confirm_button_next_to_denied_words_is_never_tapped(t):
    """'Delete all your data?' [Yes] [No]: the Yes does what the text says."""
    app, driver, store, walk = t
    app["screens"]["ABOUT"]["elements"].append({"kind": "Button", "text": "Options", "to": "ASK"})
    app["screens"]["ASK"] = {"elements": [
        {"kind": "Text", "text": "Delete all your data?"},
        {"kind": "Button", "text": "Yes", "to": "WIPED"}, {"kind": "Button", "text": "OK", "to": "WIPED"},
        {"kind": "Button", "text": "No"}]}
    app["screens"]["WIPED"] = {"elements": [{"kind": "Text", "text": "Wiped"}]}
    rep, run = walk()
    assert not any(n.startswith("NEW:WIPED") for n in rep.new_screens)
    assert any(n.startswith("NEW:DELETE-ALL") for n in rep.new_screens)  # the dialog itself was reached


def test_unlink_with_a_dash_and_private_dns_are_denied():
    from journeys.drivers.base import Element
    from journeys.pack import DEFAULT_DENY
    for text in ("Un-link account", "Private DNS"):
        assert any(r.matches(Element("@1", "Button", text)) for r in DEFAULT_DENY), text


def test_more_word_list_gaps_closed():
    from journeys.drivers.base import Element, Screen
    from journeys.heal import tap_targets
    from journeys.pack import DEFAULT_DENY
    for text in ("Destroy", "Trash", "Overwrite", "Reformat", "Restore defaults", "Revert", "Sell"):
        assert any(r.matches(Element("@1", "Button", text)) for r in DEFAULT_DENY), text
    s = Screen([Element("@1", "Text", "Delete all your data?"), Element("@2", "Button", "Apply"),
                Element("@3", "Button", "Turn on"), Element("@4", "Button", "No")])
    assert [e.text for e in tap_targets(s, DEFAULT_DENY, 9)] == ["No"]


def test_a_crash_on_back_is_a_crash_with_a_failure(t):
    app, driver, store, walk = t
    app["screens"]["ABOUT"]["elements"].append({"kind": "Button", "text": "Legal", "to": "LEGAL"})
    app["screens"]["LEGAL"] = {"elements": [{"kind": "Text", "text": "Legal info"}], "back": "CRASH"}
    rep, run = walk()
    (crash,) = [c for c in rep.crashes if c["tap"] == "back"]
    assert crash["failure"] and not any(d["screen"].startswith("NEW:LEGAL") for d in rep.dead_ends)


def test_a_dialog_over_the_same_screen_stops_the_stale_option_list(t):
    """'Delete all?' over a long list reads as 'no change'; the old list's next tap must not run."""
    app, driver, store, walk = t
    rows = [{"kind": "Text", "text": f"Row {i}"} for i in range(12)]
    app["screens"]["ABOUT"]["elements"].append({"kind": "Button", "text": "Long list", "to": "LIST"})
    app["screens"]["LIST"] = {"elements": [{"kind": "Button", "text": "More", "to": "OVER"}, *rows,
                                           {"kind": "Button", "text": "Wipe it", "to": "WIPED"}]}
    app["screens"]["OVER"] = {"elements": [{"kind": "Text", "text": "Delete all?"}, *rows,
                                           {"kind": "Button", "text": "Wipe it", "to": "WIPED"}]}
    app["screens"]["LIST"]["elements"][-1]["text"] = "Go"  # on LIST the same button reads Go
    app["screens"]["OVER"]["elements"][-1]["text"] = "Go"
    app["screens"]["WIPED"] = {"elements": [{"kind": "Text", "text": "Wiped"}]}
    rep, run = walk()
    lst = next(s for s in rep.screens if s["screen"].startswith("NEW:LONG") or "More" in str(s["options"]))
    more = next(o for o in lst["options"] if o["tap"] == {"tap": "More"})
    assert more["result"] == "showed denied words; screen reloaded"


def test_a_walk_that_tapped_nothing_fails(t):
    app, driver, store, walk = t
    app["start"] = "ONLY"
    app["screens"]["ONLY"] = {"elements": [{"kind": "Button", "text": "Delete everything"},
                                           {"kind": "Button", "text": "Wi-Fi"}]}
    rep, run = walk()
    data = store.run(run.id)
    assert data["status"] == "failed" and "nothing was tapped" in data["error"]


def test_i_agree_is_a_confirm_word():
    from journeys.drivers.base import Element, Screen
    from journeys.heal import tap_targets
    from journeys.pack import DEFAULT_DENY
    s = Screen([Element("@1", "Text", "Delete your account?"), Element("@2", "Button", "I agree"),
                Element("@3", "Button", "Back")])
    assert [e.text for e in tap_targets(s, DEFAULT_DENY, 9)] == ["Back"]


def test_a_walk_where_every_tap_failed_is_not_a_pass(t):
    from journeys.drivers.base import CommandResult
    app, driver, store, walk = t
    real = driver.do
    driver.do = lambda cmds: CommandResult(False, "Element not found") if any(
        isinstance(c, dict) and "tapOn" in c for c in cmds) else real(cmds)
    rep, run = walk()
    data = store.run(run.id)
    assert data["status"] == "failed" and "nothing was tapped" in data["error"]


def test_a_screen_it_cannot_return_to_says_what_was_not_tapped(t, monkeypatch):
    from journeys.explore import TapAll
    app, driver, store, walk = t
    app["screens"]["HOME"]["elements"].insert(0, {"kind": "Button", "text": "Boom", "to": "CRASH"})
    crashed = []
    real_log, real_reach = driver.crash_log, TapAll._reach
    monkeypatch.setattr(driver, "crash_log", lambda: crashed.append(1) or real_log())
    monkeypatch.setattr(TapAll, "_reach", lambda self, here: not crashed and real_reach(self, here))
    rep, run = walk()
    notes = [o.get("note") for s in rep.screens for o in s["options"]]
    assert "could not return here; the rest was not tapped" in notes and rep.unreachable


def test_screens_that_only_share_a_brand_header_are_all_walked(tappable_pack_dir, tmp_path):
    """'MyBank' on top of every unmapped screen is not identity: each screen's own buttons are."""
    app = {"app": "com.android.settings", "start": "HOME", "screens": {
        "HOME": {"elements": [{"kind": "Text", "text": "MyBank"}, {"kind": "Button", "text": "Cards", "to": "CARDS"},
                              {"kind": "Button", "text": "Loans", "to": "LOANS"}]},
        "CARDS": {"elements": [{"kind": "Text", "text": "MyBank"}, {"kind": "Button", "text": "Card list"}]},
        "LOANS": {"elements": [{"kind": "Text", "text": "MyBank"}, {"kind": "Button", "text": "Details", "to": "CRASH"}]}}}
    store = Store(tmp_path / "s")
    rep = tap_all(Pack.load(tappable_pack_dir), FakeDriver(app), store, store.new_run())
    assert len(rep.screens) == 3 and [c["tap"] for c in rep.crashes] == [{"tap": "Details"}]


def test_limits_say_what_was_not_tested_and_a_killing_failed_tap_is_a_crash(t):
    from journeys.drivers.base import CommandResult
    app, driver, store, walk = t
    rep, run = walk(max_taps=1)
    assert any("beyond --max-taps" in (o.get("note") or "") for s in rep.screens for o in s["options"])
    rep, run = walk(max_screens=1)
    assert store.run(run.id)["not_visited"]
    app["screens"]["HOME"]["elements"].insert(0, {"kind": "Button", "text": "Boom", "to": "CRASH"})
    real = driver.do

    def do(cmds):
        res = real(cmds)
        return CommandResult(False, "tool error") if cmds == [{"tapOn": "Boom"}] else res
    driver.do = do
    rep, run = walk()
    assert [c["tap"] for c in rep.crashes] == [{"tap": "Boom"}]


def test_update_and_connect_buttons_are_denied():
    from journeys.drivers.base import Element
    from journeys.pack import DEFAULT_DENY
    for text in ("Update", "Connect", "System update"):
        assert any(r.matches(Element("@1", "Button", text)) for r in DEFAULT_DENY), text


def test_tap_all_writes_the_live_view_and_a_failed_dka_restore_fails_the_command(tappable_pack_dir, capsys,
                                                                                  monkeypatch):
    import json
    from journeys.cli import main
    from journeys.drivers.fake import FakeDriver
    state = tappable_pack_dir / "s"
    real = FakeDriver.global_setting

    def setting(self, name, value=None):
        if value == "0":
            raise DriverError("adb gone")
        return real(self, name, value)
    monkeypatch.setattr(FakeDriver, "global_setting", setting)
    assert main(["--pack", str(tappable_pack_dir), "--state", str(state), "tap-all", "--driver", "fake",
                 "--dka"]) == 1
    assert "dka not restored" in capsys.readouterr().out
    live = json.loads((state / "live.json").read_text())
    assert live["journey"] == "tap-all" and live["status"] in ("passed", "failed")


def test_explore_records_a_failed_tap_that_killed_the_app_as_a_crash():
    from journeys.appmap import AppMap
    from journeys.drivers.base import CommandResult
    from journeys.heal import explore
    app = {"app": "a.b", "start": "S", "screens": {"S": {"elements": [
        {"kind": "Text", "text": "Hi"}, {"kind": "Button", "text": "Boom", "to": "CRASH"},
        {"kind": "Button", "text": "Other"}]}}}
    d = FakeDriver(app)
    d.do(["launchApp"])
    real = d.do
    d.do = lambda cmds: (real(cmds), CommandResult(False, "tool error"))[1] if cmds == [{"tapOn": "Boom"}] else real(cmds)
    opts = explore(d, AppMap(), "a.b", [])
    boom = next(o for o in opts if o["tap"] == {"tap": "Boom"})
    assert boom["leads_to"] == "CRASH" and opts[-1] is boom  # explore stops at the crash


def test_the_live_view_follows_a_walk_and_its_stop(t):
    import json
    app, driver, store, walk = t
    seen = []
    real_look, real_one = driver.look, driver._one

    def look():
        seen.append(json.loads((store.root / "live.json").read_text())["status"])
        return real_look()
    driver.look = look

    def lost(cmd):
        if isinstance(cmd, dict) and "tapOn" in cmd and len(seen) > 3:
            raise DriverError("adb link lost")
        return real_one(cmd)
    driver._one = lost
    with pytest.raises(DriverError):
        walk()
    assert seen[0] == "running"
    assert json.loads((store.root / "live.json").read_text())["status"] == "failed"


def test_round_26_minors(t, tappable_pack_dir, tmp_path, monkeypatch):
    import json
    from journeys.drivers.base import Element, Screen
    from journeys.heal import new_denied, tap_targets
    from journeys.pack import DEFAULT_DENY, fake_app_errors
    app, driver, store, walk = t
    # back: CRASH is a valid fake app value
    assert fake_app_errors({"start": "A", "screens": {"A": {"elements": [], "back": "CRASH"}}}, "f") == []
    # warning words without a denied word still protect their confirm buttons
    for warn, ok in (("Are you sure?", "Proceed"), ("Permanently lose your photos", "Confirm"),
                     ("Account closure", "I agree")):
        s = Screen([Element("@1", "Text", warn), Element("@2", "Button", ok), Element("@3", "Button", "Back")])
        assert [e.text for e in tap_targets(s, DEFAULT_DENY, 9)] == ["Back"], warn
    # a dialog over a screen that already showed denied words still makes the option list stale
    base = Screen([Element("@1", "Button", "Delete account")] + [Element(f"@{i}", "Text", f"Row {i}") for i in range(9)])
    after = Screen(base.elements + [Element("@99", "Button", "Yes")])
    assert new_denied(base, after, DEFAULT_DENY)
    # tap_allowed false through the API ends the run failed
    pack = Pack.load(tappable_pack_dir)
    pack.tap_allowed = False
    run = store.new_run()
    with pytest.raises(DriverError):
        tap_all(pack, driver, store, run)
    assert store.run(run.id)["status"] == "failed"
    # a failed dka restore fails the run and the live view
    real = driver.global_setting
    monkeypatch.setattr(driver, "global_setting",
                        lambda name, value=None: (_ for _ in ()).throw(DriverError("gone")) if value == "0"
                        else real(name, value))
    rep, run = walk(dka=True)
    assert store.run(run.id)["status"] == "failed"
    assert json.loads((store.root / "live.json").read_text())["status"] == "failed"

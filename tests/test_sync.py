"""Two-way sync: one source of truth (pack.yaml sync:), and every drift reported."""
import json

import pytest

from journeys.cli import main
from journeys.pack import Pack
from journeys.sync import MARK, check_sync, generated, sync


def cli(pack_dir, *args):
    return main(["--pack", str(pack_dir), "--state", str(pack_dir / "s"), *args])


def kinds(pack_dir):
    return sorted((d.kind, d.file) for d in check_sync(Pack.load(pack_dir)))


@pytest.fixture
def synced(pack_dir):
    f = pack_dir / "pack.yaml"
    f.write_text(f.read_text() + "sync:\n  dir: flows\n")
    return pack_dir


@pytest.fixture
def framework(pack_dir):
    f = pack_dir / "pack.yaml"
    f.write_text(f.read_text() + "sync:\n  source: framework\n  dir: flows\n")
    for j in (pack_dir / "journeys").glob("*.yaml"):
        j.unlink()  # the flows are the truth here: the pack starts with none of its own
    (pack_dir / "sets.yaml").write_text("sets: {}\n")
    (pack_dir / "flows").mkdir()
    return pack_dir


def flow(name, *steps):
    return "appId: com.android.settings\n---\n- launchApp\n" + "".join(f"- {s}\n" for s in steps)


def test_journeys_are_the_truth_generated_files_carry_a_header_and_hash(synced, capsys):
    assert kinds(synced) == [("missing", "android-version.yaml"), ("missing", "open-display.yaml"),
                             ("missing", "search-settings.yaml")]
    assert cli(synced, "check-sync") == 1
    assert cli(synced, "sync", "--dry-run") == 0 and not (synced / "flows").exists()  # a dry run changes nothing
    assert cli(synced, "sync") == 0
    text = (synced / "flows" / "open-display.yaml").read_text()
    assert text.startswith(f"{MARK} from journey open-display;") and generated(text)[0] == "open-display"
    assert "appId: com.android.settings" in text
    assert kinds(synced) == [] and cli(synced, "check-sync") == 0
    capsys.readouterr()
    assert cli(synced, "sync") == 0 and "0 changes" in capsys.readouterr().out  # nothing left to do


def test_a_hand_edit_is_reported_and_never_overwritten(synced):
    sync(Pack.load(synced))
    f = synced / "flows" / "open-display.yaml"
    f.write_text(f.read_text() + "- tapOn: Extra\n")
    assert ("hand-edited", "open-display.yaml") in kinds(synced)
    done, left = sync(Pack.load(synced))
    assert "- tapOn: Extra" in f.read_text() and [x.kind for x in left] == ["hand-edited"]
    assert cli(synced, "sync") == 1  # left for a person


def test_a_changed_journey_makes_its_flow_stale_and_sync_rewrites_it(synced):
    sync(Pack.load(synced))
    j = synced / "journeys" / "display.yaml"
    j.write_text(j.read_text().replace('"/Dark theme.*/"', '"/Brightness.*/"'))
    assert kinds(synced) == [("stale", "open-display.yaml")]
    sync(Pack.load(synced))
    assert "Brightness" in (synced / "flows" / "open-display.yaml").read_text() and kinds(synced) == []


def test_a_deleted_journey_leaves_an_orphan_that_prune_removes(synced):
    sync(Pack.load(synced))
    (synced / "journeys" / "display.yaml").unlink()
    (synced / "sets.yaml").write_text("sets: {}\n")  # no set names it any more
    assert kinds(synced) == [("orphan", "open-display.yaml")]
    sync(Pack.load(synced))
    assert (synced / "flows" / "open-display.yaml").exists()  # only with --prune
    assert cli(synced, "sync", "--prune") == 0 and not (synced / "flows" / "open-display.yaml").exists()


def test_a_developers_new_flow_becomes_a_draft_then_a_generated_file(synced, monkeypatch):
    sync(Pack.load(synced))
    dev = synced / "flows" / "check-about.yaml"
    dev.write_text(flow("check-about", 'tapOn: "About phone"'))
    assert kinds(synced) == [("unimported", "check-about.yaml")]
    sync(Pack.load(synced))
    assert (synced / "drafts" / "check-about.yaml").exists() and kinds(synced) == []
    state = json.loads((synced / "sync-state.json").read_text())
    assert state["flows"]["check-about.yaml"]["journey"] == "check-about"
    dev.write_text(dev.read_text() + "- back\n")  # the developer edits it after the import
    assert kinds(synced) == [("flow-edited", "check-about.yaml")]
    dev.write_text(flow("check-about", 'tapOn: "About phone"'))
    d = synced / "drafts" / "check-about.yaml"
    d.write_text("name: check-about\nsteps:\n  - expect: SETTINGS-HOME\n  - goto: SETTINGS-ABOUT\n")
    assert cli(synced, "promote-journey", "check-about") == 0
    assert kinds(synced) == [("stale", "check-about.yaml")]  # the flow it came from, unchanged
    sync(Pack.load(synced))
    assert generated(dev.read_text())[0] == "check-about" and kinds(synced) == []


def test_a_developers_flow_with_a_journeys_name_is_never_overwritten(synced):
    (synced / "flows").mkdir()
    dev = synced / "flows" / "open-display.yaml"
    dev.write_text(flow("open-display", 'tapOn: "Display"'))
    assert ("name-taken", "open-display.yaml") in kinds(synced)
    sync(Pack.load(synced))
    assert generated(dev.read_text()) is None


def test_framework_flows_are_the_truth(framework):
    f = framework / "flows" / "about.yaml"
    f.write_text(flow("about", 'tapOn: "About phone"'))
    assert kinds(framework) == [("new-flow", "about.yaml")]
    sync(Pack.load(framework))
    d = framework / "drafts" / "about.yaml"
    assert d.read_text().startswith("# synced from flows/about.yaml") and kinds(framework) == []
    f.write_text(flow("about", 'tapOn: "About phone"', 'assertVisible: "Android version"'))
    assert kinds(framework) == [("flow-changed", "about.yaml")]
    sync(Pack.load(framework))
    assert "Android version" in d.read_text() and kinds(framework) == []
    d.write_text(d.read_text().replace("About phone", "About tablet"))  # an edit in Journeys
    assert kinds(framework) == [("journey-edited", "drafts/about.yaml")]
    done, left = sync(Pack.load(framework))
    assert "About tablet" in d.read_text() and [x.kind for x in left] == ["journey-edited"]
    f.unlink()
    assert ("flow-removed", "about.yaml") in kinds(framework)


def test_framework_mode_reports_a_journey_with_no_flow(framework):
    (framework / "journeys" / "mine.yaml").write_text("name: mine\nsteps:\n  - expect: SETTINGS-HOME\n")
    assert kinds(framework) == [("journey-only", "journeys/mine.yaml")]


def test_sync_settings_are_checked(pack_dir):
    f = pack_dir / "pack.yaml"
    keep = f.read_text()
    for bad, words in (("sync: {source: both, dir: x}", "sync.source is journeys"),
                       ("sync: {source: journeys}", "sync.dir is the folder"),
                       ("sync: {dir: x, format: appium}", "sync.format is maestro"),
                       ("sync: {dir: x, extra: 1}", "sync keys are"), ("sync: [x]", "sync is a map")):
        f.write_text(keep + bad + "\n")
        p = Pack.load(pack_dir)
        assert any(words in e for e in p.errors) and not p.sync, bad
    f.write_text(keep)
    assert cli(pack_dir, "check-sync") == 2  # no sync: section is a clear error


def test_mcp_check_sync(synced, tmp_path):
    from journeys.mcp_server import Server
    out = Server(str(synced), str(tmp_path / "s"))._call("check_sync", {})
    drift = json.loads(out["content"][0]["text"])
    assert {d["kind"] for d in drift} == {"missing"} and len(drift) == 3


def test_round_1_a_developer_flow_with_another_name_is_replaced_once_promoted(synced):
    sync(Pack.load(synced))
    dev = synced / "flows" / "Login_Flow.yaml"
    dev.write_text(flow("Login_Flow", 'tapOn: "About phone"'))
    sync(Pack.load(synced))
    d = synced / "drafts" / "login-flow.yaml"
    assert d.exists() and kinds(synced) == []
    d.write_text("name: login-flow\nsteps:\n  - expect: SETTINGS-HOME\n  - goto: SETTINGS-ABOUT\n")
    assert cli(synced, "promote-journey", "login-flow") == 0
    assert ("superseded", "Login_Flow.yaml") in kinds(synced)
    done, _ = sync(Pack.load(synced), dry_run=True)
    assert "delete Login_Flow.yaml (now generated as login-flow.yaml)" in done  # predicted like the real run
    sync(Pack.load(synced))
    assert not dev.exists() and generated((synced / "flows" / "login-flow.yaml").read_text())
    assert kinds(synced) == []


def test_round_1_a_yml_flow_named_like_a_journey_is_reported(synced):
    sync(Pack.load(synced))
    (synced / "flows" / "open-display.yml").write_text(flow("x", 'tapOn: "Display"'))
    assert ("name-taken", "open-display.yml") in kinds(synced)


def test_round_1_a_broken_flow_edit_is_left_not_reported_done(framework, capsys):
    f = framework / "flows" / "about.yaml"
    f.write_text(flow("about", 'tapOn: "About phone"'))
    sync(Pack.load(framework))
    f.write_text("appId: com.android.settings\n---\n- tapOn: [unclosed\n")
    capsys.readouterr()
    assert cli(framework, "sync") == 1
    out = capsys.readouterr().out
    assert "0 changes" in out and "bad-flow" in out and "flow-removed" not in out


def test_round_1_a_reimport_keeps_tags_and_reports_both_sides_changed(framework):
    f = framework / "flows" / "about.yaml"
    f.write_text(flow("about", 'tapOn: "About phone"'))
    sync(Pack.load(framework))
    d = framework / "drafts" / "about.yaml"
    d.write_text(d.read_text().replace("- imported", "- smoke\n- mine"))
    f.write_text(flow("about", 'tapOn: "About phone"', 'assertVisible: "Android version"'))
    sync(Pack.load(framework))
    text = d.read_text()
    assert "Android version" in text and "smoke" in text and "mine" in text and kinds(framework) == []
    d.write_text(text.replace("About phone", "About tablet"))  # Journeys side
    f.write_text(flow("about", 'tapOn: "About phone"', 'assertVisible: "Build number"'))  # flow side
    assert kinds(framework) == [("conflict", "drafts/about.yaml")]
    sync(Pack.load(framework))
    assert "About tablet" in d.read_text()  # never overwritten


def test_round_1_odd_state_and_unreadable_flows(synced):
    (synced / "flows").mkdir()
    (synced / "flows" / "bad.yaml").write_bytes(b"\xff\xfe")
    (synced / "sync-state.json").write_text(json.dumps({"flows": {"a.yaml": {"journey": ["x"], "hash": 1}}}))
    assert ("unreadable", "bad.yaml") in kinds(synced)


def test_round_1_a_superseded_copy_stays_until_its_generated_flow_exists(synced):
    sync(Pack.load(synced))
    dev = synced / "flows" / "Login_Flow.yaml"
    dev.write_text(flow("Login_Flow", 'tapOn: "About phone"'))
    sync(Pack.load(synced))
    (synced / "drafts" / "login-flow.yaml").write_text(
        "name: login-flow\nsteps:\n  - expect: SETTINGS-HOME\n  - goto: SETTINGS-ABOUT\n")
    assert cli(synced, "promote-journey", "login-flow") == 0
    (synced / "flows" / "login-flow.yaml").write_text(flow("other", 'tapOn: "Display"'))  # someone else's file
    sync(Pack.load(synced))
    assert dev.exists()  # no generated login-flow.yaml, so the copy it came from stays


def test_round_1_a_state_record_with_a_bad_hash_is_ignored(synced):
    (synced / "flows").mkdir()
    (synced / "flows" / "x.yaml").write_text(flow("x", 'tapOn: "Display"'))
    (synced / "sync-state.json").write_text(json.dumps({"flows": {"x.yaml": {"journey": "open-display", "hash": [1]}}}))
    assert ("unimported", "x.yaml") in kinds(synced)


def test_round_2_a_journey_named_config_never_touches_maestros_config(synced):
    (synced / "flows").mkdir()
    cfg = synced / "flows" / "config.yaml"
    cfg.write_text('flows:\n  - "*.yaml"\n')
    (synced / "journeys" / "cfg.yaml").write_text("name: config\nsteps:\n  - expect: SETTINGS-HOME\n")
    assert ("name-taken", "config.yaml") in kinds(synced)
    sync(Pack.load(synced))
    assert cfg.read_text() == 'flows:\n  - "*.yaml"\n'
    done, _ = sync(Pack.load(synced))
    assert done == []  # it settles


def test_round_2_subfolders_are_flows_too(synced):
    sync(Pack.load(synced))
    (synced / "flows" / "checkout").mkdir()
    (synced / "flows" / "checkout" / "pay.yml").write_text(flow("pay", 'tapOn: "Display"'))
    (synced / "flows" / ".hidden").mkdir()
    (synced / "flows" / ".hidden" / "x.yaml").write_text(flow("x", 'tapOn: "Display"'))
    assert kinds(synced) == [("unimported", "checkout/pay.yml")]
    sync(Pack.load(synced))
    assert (synced / "drafts" / "pay.yaml").exists() and kinds(synced) == []


def test_round_2_a_declined_flow_stays_declined_until_it_changes(synced):
    sync(Pack.load(synced))
    dev = synced / "flows" / "extra.yaml"
    dev.write_text(flow("extra", 'tapOn: "Display"'))
    sync(Pack.load(synced))
    (synced / "drafts" / "extra.yaml").unlink()
    assert kinds(synced) == [("unlinked", "extra.yaml")]  # deleted on purpose, or renamed? a person says
    assert cli(synced, "sync", "--decline", "extra.yaml") == 0
    assert kinds(synced) == []
    dev.write_text(flow("extra", 'tapOn: "Display"', "back"))
    assert kinds(synced) == [("unimported", "extra.yaml")]


def test_round_2_an_empty_import_is_reported_until_it_gets_steps(synced):
    sync(Pack.load(synced))
    (synced / "flows" / "other.yaml").write_text("appId: com.android.settings\n---\n- runFlow: sub.yaml\n")
    sync(Pack.load(synced))
    assert kinds(synced) == [("empty-import", "other.yaml")]
    (synced / "drafts" / "other.yaml").write_text("name: other\nsteps:\n  - expect: SETTINGS-HOME\n")
    assert kinds(synced) == []


def test_round_2_duplicates_unexportable_journeys_and_stale_state(synced):
    sync(Pack.load(synced))
    src = synced / "flows" / "open-display.yaml"
    (synced / "flows" / "copy.yaml").write_text(src.read_text())
    assert ("duplicate", "copy.yaml") in kinds(synced)
    assert cli(synced, "sync", "--prune") == 0 and not (synced / "flows" / "copy.yaml").exists()
    j = synced / "journeys" / "display.yaml"
    j.write_text(j.read_text().replace("scroll_to", "frobnicate"))
    assert ("unexportable", "open-display.yaml") in kinds(synced)  # listed, not a traceback
    # a developer file deleted after its import leaves no record behind
    j.write_text(j.read_text().replace("frobnicate", "scroll_to"))
    dev = synced / "flows" / "gone.yaml"
    dev.write_text(flow("gone", 'tapOn: "Display"'))
    sync(Pack.load(synced))
    dev.unlink()
    (synced / "drafts" / "gone.yaml").unlink()
    sync(Pack.load(synced))
    assert "gone.yaml" not in json.loads((synced / "sync-state.json").read_text())["flows"]


def test_round_2_prune_never_deletes_a_hand_edited_orphan(synced):
    sync(Pack.load(synced))
    f = synced / "flows" / "open-display.yaml"
    f.write_text(f.read_text() + "- back\n")
    (synced / "journeys" / "display.yaml").unlink()
    (synced / "sets.yaml").write_text("sets: {}\n")
    assert cli(synced, "sync", "--prune") == 1 and f.exists()


def test_round_3_a_name_differing_only_in_case_is_never_written_over(synced):
    (synced / "flows").mkdir()
    dev = synced / "flows" / "Open-Display.yaml"
    dev.write_text(flow("x", 'tapOn: "Display"'))
    assert [k for k in kinds(synced) if k[1] == "Open-Display.yaml"] == [("name-taken", "Open-Display.yaml")]
    sync(Pack.load(synced))
    assert generated(dev.read_text()) is None and 'tapOn: "Display"' in dev.read_text()


def test_round_3_a_lossy_flow_stays_until_accepted(synced, capsys):
    sync(Pack.load(synced))
    dev = synced / "flows" / "checkout.yaml"
    dev.write_text(flow("checkout", "runFlow: login.yaml", 'tapOn: "About phone"'))
    sync(Pack.load(synced))
    (synced / "drafts" / "checkout.yaml").write_text(
        "name: checkout\nsteps:\n  - expect: SETTINGS-HOME\n  - goto: SETTINGS-ABOUT\n")
    assert cli(synced, "promote-journey", "checkout") == 0
    assert kinds(synced) == [("lossy", "checkout.yaml")]
    sync(Pack.load(synced))
    assert "runFlow" in dev.read_text()  # kept: the import left that out
    done, left = sync(Pack.load(synced), dry_run=True, accept=["checkout.yaml"])
    assert "write checkout.yaml from journey checkout" in done and not left  # a dry run predicts the real one
    assert "runFlow" in dev.read_text()
    capsys.readouterr()
    assert cli(synced, "sync", "--accept", "./checkout.yaml") == 0
    assert generated(dev.read_text())[0] == "checkout" and kinds(synced) == []


def test_round_3_same_name_in_two_subfolders_and_bad_yaml(synced):
    sync(Pack.load(synced))
    for sub in ("auth", "checkout"):
        (synced / "flows" / sub).mkdir()
        (synced / "flows" / sub / "login.yaml").write_text(flow("login", 'tapOn: "Display"'))
    sync(Pack.load(synced))
    assert (synced / "drafts" / "login.yaml").exists() and (synced / "drafts" / "checkout-login.yaml").exists()
    (synced / "flows" / "broken.yaml").write_text("- tapOn: [x\n")
    assert kinds(synced) == [("bad-flow", "broken.yaml")]


def test_round_3_superseding_removes_the_empty_folder(synced):
    sync(Pack.load(synced))
    (synced / "flows" / "team").mkdir()
    (synced / "flows" / "team" / "Pay.yaml").write_text(flow("Pay", 'tapOn: "About phone"'))
    sync(Pack.load(synced))
    (synced / "drafts" / "pay.yaml").write_text("name: pay\nsteps:\n  - expect: SETTINGS-HOME\n  - goto: SETTINGS-ABOUT\n")
    assert cli(synced, "promote-journey", "pay") == 0
    sync(Pack.load(synced))
    assert not (synced / "flows" / "team").exists() and kinds(synced) == []


def test_round_3_a_state_record_without_journey_hash_is_ignored(framework):
    f = framework / "flows" / "about.yaml"
    f.write_text(flow("about", 'tapOn: "About phone"'))
    (framework / "sync-state.json").write_text(json.dumps({"flows": {"about.yaml": {"journey": "about", "hash": "x"}}}))
    assert kinds(framework) == [("new-flow", "about.yaml")]


def test_round_4_a_capitalised_developer_flow_is_replaced_after_promotion(synced):
    sync(Pack.load(synced))
    dev = synced / "flows" / "Login.yaml"
    dev.write_text(flow("Login", 'tapOn: "About phone"'))
    sync(Pack.load(synced))
    (synced / "drafts" / "login.yaml").write_text(
        "name: login\nsteps:\n  - expect: SETTINGS-HOME\n  - goto: SETTINGS-ABOUT\n")
    assert cli(synced, "promote-journey", "login") == 0
    assert kinds(synced) == [("replace", "Login.yaml")]
    done, _ = sync(Pack.load(synced), dry_run=True)
    assert done == ["replace Login.yaml with the generated login.yaml"]
    sync(Pack.load(synced))
    names = sorted(p.name for p in (synced / "flows").iterdir())
    assert "login.yaml" in names and "Login.yaml" not in names and kinds(synced) == []


def test_round_4_a_draft_renamed_before_promotion_keeps_its_link(synced):
    sync(Pack.load(synced))
    dev = synced / "flows" / "pay.yaml"
    dev.write_text(flow("pay", 'tapOn: "About phone"'))
    sync(Pack.load(synced))
    d = synced / "drafts" / "pay.yaml"
    d.write_text(d.read_text().replace("name: pay", "name: checkout-pay"))
    d.rename(synced / "drafts" / "checkout-pay.yaml")
    assert cli(synced, "promote-journey", "checkout-pay") == 0
    assert ("superseded", "pay.yaml") in kinds(synced)
    sync(Pack.load(synced))
    assert not dev.exists() and (synced / "flows" / "checkout-pay.yaml").exists() and kinds(synced) == []


def test_round_4_a_list_form_journey_keeps_its_tags_on_reimport(framework):
    f = framework / "flows" / "pay.yaml"
    f.write_text(flow("pay", 'tapOn: "About phone"'))
    sync(Pack.load(framework))
    d = framework / "drafts" / "pay.yaml"
    import yaml
    data = yaml.safe_load(d.read_text())
    data["tags"] = ["smoke", "payments"]
    d.write_text(yaml.safe_dump([data], sort_keys=False))
    f.write_text(flow("pay", 'tapOn: "About phone"', 'assertVisible: "Android version"'))
    sync(Pack.load(framework))
    text = d.read_text()
    assert "payments" in text and "Android version" in text and text.split("\n", 1)[1].startswith("- ")
    assert kinds(framework) == []


def test_round_4_accept_says_when_it_matched_nothing(synced, capsys):
    sync(Pack.load(synced))
    capsys.readouterr()
    assert cli(synced, "sync", "--accept", "./nothing.yaml") == 1
    assert "not-accepted" in capsys.readouterr().out


def test_round_5_a_broken_file_with_a_journeys_name_is_never_written_over(synced):
    sync(Pack.load(synced))
    f = synced / "flows" / "open-display.yaml"
    f.write_text(f.read_text() + '- tapOn: "unclosed\n')  # mid-edit
    assert kinds(synced) == [("bad-flow", "open-display.yaml")]  # not also "missing"
    sync(Pack.load(synced))
    assert "unclosed" in f.read_text()


def test_round_5_a_removed_flow_whose_journey_is_gone_clears(framework):
    f = framework / "flows" / "open.yaml"
    f.write_text(flow("open", 'tapOn: "About phone"'))
    sync(Pack.load(framework))
    f.unlink()
    assert kinds(framework) == [("flow-removed", "open.yaml")]
    (framework / "drafts" / "open.yaml").unlink()
    assert kinds(framework) == []
    sync(Pack.load(framework))
    assert "open.yaml" not in json.loads((framework / "sync-state.json").read_text())["flows"]


def test_round_5_typed_text_is_not_a_loss_and_notes_are_not_changes(synced, capsys):
    sync(Pack.load(synced))
    dev = synced / "flows" / "search.yaml"
    dev.write_text(flow("search", 'tapOn: "Search settings"', "inputText: battery"))
    capsys.readouterr()
    assert cli(synced, "sync") == 0
    assert "1 changes" in capsys.readouterr().out  # the import; its note is not a change
    (synced / "drafts" / "search.yaml").write_text(
        "name: search\nsteps:\n  - expect: SETTINGS-HOME\n  - goto: SETTINGS-SEARCH\n")
    assert cli(synced, "promote-journey", "search") == 0
    assert kinds(synced) == [("stale", "search.yaml")]  # replaceable: typing was carried over, nothing lost


def test_round_5_sync_dir_must_be_a_flow_folder(pack_dir):
    f = pack_dir / "pack.yaml"
    keep = f.read_text()
    for d in (".", ".."):
        f.write_text(keep + f"sync:\n  dir: '{d}'\n")
        assert any("holds the pack itself" in e for e in Pack.load(pack_dir).errors), d
    (pack_dir / "flowfile").write_text("")
    f.write_text(keep + "sync:\n  dir: flowfile\n")
    assert cli(pack_dir, "check-sync") == 2  # a file, not a folder: a clear error


def test_round_5_env_values_never_reach_a_flow_file(synced, monkeypatch):
    monkeypatch.setenv("DEMO_SEARCH", "SECRETVAL")
    assert cli(synced, "sync") == 0
    assert not any("SECRETVAL" in p.read_text() for p in (synced / "flows").iterdir())
    assert "${DEMO_SEARCH}" in (synced / "flows" / "search-settings.yaml").read_text()


def test_round_6_a_draft_renamed_and_edited_leaves_no_silent_copy(synced):
    sync(Pack.load(synced))
    dev = synced / "flows" / "Net_Check.yaml"
    dev.write_text(flow("Net_Check", 'tapOn: "About phone"'))
    sync(Pack.load(synced))
    (synced / "drafts" / "net-check.yaml").unlink()
    (synced / "journeys" / "signin.yaml").write_text(
        "name: signin\nsteps:\n  - expect: SETTINGS-HOME\n  - goto: SETTINGS-DISPLAY\n")
    sync(Pack.load(synced))
    assert ("unlinked", "Net_Check.yaml") in kinds(synced)  # reported until a person decides


def test_round_6_a_dry_run_predicts_refused_imports(synced):
    sync(Pack.load(synced))
    (synced / "flows" / "empty.yaml").write_text("")
    (synced / "flows" / "hdr.yaml").write_text("appId: com.android.settings\n")
    assert ("bad-flow", "empty.yaml") in kinds(synced) and ("bad-flow", "hdr.yaml") in kinds(synced)
    (synced / "drafts").mkdir(exist_ok=True)
    (synced / "drafts" / "taken.yaml").write_text("name: taken\nsteps:\n  - expect: SETTINGS-HOME\n")
    (synced / "flows" / "taken.yaml").write_text(flow("taken", 'tapOn: "Display"'))
    dry, dry_left = sync(Pack.load(synced), dry_run=True)
    real, real_left = sync(Pack.load(synced))
    assert dry == real and [x.kind for x in dry_left] == [x.kind for x in real_left]


def test_round_6_a_yml_twin_blocks_writing(synced):
    (synced / "flows").mkdir()
    (synced / "flows" / "open-display.yml").write_text(flow("x", 'tapOn: "Display"'))
    sync(Pack.load(synced))
    assert not (synced / "flows" / "open-display.yaml").exists()
    assert ("name-taken", "open-display.yml") in kinds(synced)


def test_round_6_framework_unreadable_flow_is_not_removed(framework):
    f = framework / "flows" / "about.yaml"
    f.write_text(flow("about", 'tapOn: "About phone"'))
    sync(Pack.load(framework))
    f.write_bytes(b"\xff\xfe")
    assert kinds(framework) == [("unreadable", "about.yaml")]


def test_round_6_bad_state_is_never_written_over(synced):
    sync(Pack.load(synced))
    (synced / "sync-state.json").write_text("{broken")
    assert ("bad-state", "sync-state.json") in kinds(synced)
    (synced / "flows" / "new.yaml").write_text(flow("new", 'tapOn: "Display"'))
    assert cli(synced, "sync") == 1
    assert (synced / "sync-state.json").read_text() == "{broken"


def test_round_6_sync_dir_inside_the_packs_own_folders_is_refused(pack_dir):
    f = pack_dir / "pack.yaml"
    f.write_text(f.read_text() + "sync:\n  dir: drafts\n")
    assert any("inside the pack's own" in e for e in Pack.load(pack_dir).errors)

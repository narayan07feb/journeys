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

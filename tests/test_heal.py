import pytest
import yaml

from journeys.drivers.base import DriverError
from journeys.drivers.fake import FakeDriver
from journeys.heal import PromoteError, promote, reject
from journeys.pack import Pack
from journeys.results import Store
from journeys.runner import Runner


@pytest.fixture
def h(tappable_pack_dir, tmp_path):
    pack_dir = tappable_pack_dir
    app = yaml.safe_load((pack_dir / "fake" / "app.yaml").read_text())
    store = Store(tmp_path / "state")

    def run(name, heal=True):
        pack = Pack.load(pack_dir)
        assert pack.errors == []
        r = Runner(pack, FakeDriver(app), store, store.new_run(), variables={}, sleep=lambda s: None, heal=heal)
        (out,) = r.run_all(pack.select(name))
        return out

    return pack_dir, app, store, run


def _display_tap_to(app, target):
    for e in app["screens"]["HOME"]["elements"]:
        if e["text"].startswith("Display"):
            e["to"] = target


def test_expect_detour_then_promote(h):
    pack_dir, app, store, run = h
    app["start"] = "ABOUT"  # the app now opens on About (back goes Home); the journey expects Home
    app["screens"]["ABOUT"]["back"] = "HOME"
    out = run("open-display")
    assert out.status == "passed" and out.changes == ["P-1"]
    p = store.proposal("P-1")
    assert p["change"]["kind"] == "expect-to-goto" and p["status"] == "proposed"
    assert run("open-display").changes == ["P-1"]  # same change again: no new proposal
    assert len(store.proposals()) == 1
    assert run("open-display", heal=False).status == "failed"
    done = promote(Pack.load(pack_dir), store, "P-1")
    assert "expect -> goto SETTINGS-HOME" in done
    assert "  - goto: SETTINGS-HOME\n" in (pack_dir / "journeys" / "display.yaml").read_text()
    out = run("open-display", heal=False)
    assert out.status == "passed" and out.changes == []
    with pytest.raises(PromoteError):
        promote(Pack.load(pack_dir), store, "P-1")


def test_edge_detour_through_known_screen(h):
    pack_dir, app, store, run = h
    app["screens"]["WHATSNEW"] = {"elements": [
        {"kind": "Text", "text": "What's new"}, {"kind": "Button", "text": "Got it", "to": "DISPLAY", "replace": True}]}
    _display_tap_to(app, "WHATSNEW")
    with open(pack_dir / "map" / "screens.yaml", "a") as f:
        f.write("  WHATS-NEW:\n    match:\n      all: [{text: \"What's new\"}]\n")
    with open(pack_dir / "map" / "edges.yaml", "a") as f:
        f.write("  - from: WHATS-NEW\n    to: SETTINGS-DISPLAY\n    do: [{tap: \"Got it\"}]\n")
    out = run("open-display")
    assert out.status == "passed" and out.changes == ["P-1"]
    assert store.proposal("P-1")["change"] == {
        "kind": "edge-to", "src": "SETTINGS-HOME", "old": "SETTINGS-DISPLAY", "new": "WHATS-NEW"}
    promote(Pack.load(pack_dir), store, "P-1")
    pack = Pack.load(pack_dir)
    assert pack.errors == []
    assert [(e.src, e.dst) for e in pack.map.path("SETTINGS-HOME", "SETTINGS-DISPLAY")] == [
        ("SETTINGS-HOME", "WHATS-NEW"), ("WHATS-NEW", "SETTINGS-DISPLAY")]
    out = run("open-display", heal=False)
    assert out.status == "passed"


def test_unknown_screen_explored_and_promoted(h):
    pack_dir, app, store, run = h
    app["screens"]["TOUR"] = {"elements": [
        {"kind": "Text", "text": "Welcome tour"},
        {"kind": "Button", "text": "Skip", "to": "DISPLAY", "replace": True},
        {"kind": "Button", "text": "Next tip"}]}
    _display_tap_to(app, "TOUR")
    out = run("open-display")
    assert out.status == "failed" and "landed on an unknown screen" in out.message
    f = store.failure(out.failure)
    assert {"tap": {"tap": "Skip"}, "leads_to": "SETTINGS-DISPLAY", "heading": None,
            "note": "back did not return; explore stopped"} in f["options"]
    assert any(o.get("note") == "no change" for o in f["options"])
    p = store.proposal(f["proposal"])
    assert p["change"]["kind"] == "new-screen" and p["change"]["name"] == "WELCOME-TOUR"
    promote(Pack.load(pack_dir), store, p["id"], name="TOUR")
    pack = Pack.load(pack_dir)
    assert pack.errors == [] and "TOUR" in pack.map.screens
    assert any(e.src == "TOUR" and e.dst == "SETTINGS-DISPLAY" for e in pack.map.edges)
    out = run("open-display")  # now a known screen: the run detours through it
    assert out.status == "passed" and store.proposal(out.changes[0])["change"]["kind"] == "edge-to"


def test_missing_option_on_known_screen_gives_new_edge(h):
    pack_dir, app, store, run = h
    # Display moved off Home: Home has only "Screen" now, which opens Display.
    els = app["screens"]["HOME"]["elements"]
    app["screens"]["HOME"]["elements"] = [e for e in els if not e["text"].startswith("Display")] + [
        {"kind": "Button", "text": "Screen", "to": "DISPLAY"}]
    out = run("open-display")
    assert out.status == "failed" and "Element not found" in out.message
    p = store.proposal(store.failure(out.failure)["proposal"])
    assert p["change"] == {"kind": "new-edge", "src": "SETTINGS-HOME", "dst": "SETTINGS-DISPLAY",
                           "do": [{"tap": "Screen"}]}
    reject(store, p["id"], "the real fix is to edit the old edge")
    assert store.proposal(p["id"])["status"] == "rejected"
    with pytest.raises(PromoteError):
        promote(Pack.load(pack_dir), store, p["id"])


def test_crash_during_explore_stops_it(h):
    pack_dir, app, store, run = h
    app["screens"]["TOUR"] = {"elements": [
        {"kind": "Text", "text": "Welcome tour"}, {"kind": "Button", "text": "Boom", "to": "CRASH"},
        {"kind": "Button", "text": "Skip", "to": "DISPLAY"}]}
    _display_tap_to(app, "TOUR")
    out = run("open-display")
    opts = store.failure(out.failure)["options"]
    assert opts[-1]["leads_to"] == "CRASH" and len(opts) == 2


def test_no_explore_when_pack_forbids_taps(pack_dir, tmp_path):
    app = yaml.safe_load((pack_dir / "fake" / "app.yaml").read_text())
    _display_tap_to(app, "ABOUT")
    pack, store, driver = Pack.load(pack_dir), Store(tmp_path / "s"), FakeDriver(app)
    (out,) = Runner(pack, driver, store, store.new_run(), variables={}, sleep=lambda s: None).run_all(
        pack.select("open-display"))
    assert out.status == "failed" and "options" not in store.failure(out.failure)
    assert sum(1 for c in driver.log if c == {"tapOn": "Network & internet"}) == 0


def test_explore_stops_when_a_tap_leaves_the_app(h):
    pack_dir, app, store, run = h
    app["screens"]["TOUR"] = {"elements": [
        {"kind": "Text", "text": "Welcome tour"}, {"kind": "Button", "text": "Help", "to": "BROWSER"},
        {"kind": "Button", "text": "Skip", "to": "DISPLAY"}]}
    app["screens"]["BROWSER"] = {"package": "com.android.chrome", "elements": [{"kind": "Text", "text": "Help"}]}
    _display_tap_to(app, "TOUR")
    out = run("open-display")
    opts = store.failure(out.failure)["options"]
    assert "left the app for com.android.chrome" in opts[-1]["note"]
    assert opts[-1]["tap"] == {"tap": "Help"} and len(opts) == 2  # Skip is never tapped after it


def test_a_lost_link_during_explore_keeps_the_failure_and_ends_the_run(h, monkeypatch):
    pack_dir, app, store, run = h
    _display_tap_to(app, "NOWHERE")  # the journey fails on an unknown screen, then explore taps
    app["screens"]["NOWHERE"] = {"elements": [{"kind": "Text", "text": "Nowhere"}, {"kind": "Button", "text": "Go"}]}
    real = FakeDriver._one

    def lost(self, cmd):
        if isinstance(cmd, dict) and cmd.get("tapOn") == "Go":
            raise DriverError("adb link lost")
        return real(self, cmd)
    monkeypatch.setattr(FakeDriver, "_one", lost)
    out = run("open-display")
    assert out.status == "failed" and "link lost" in store.failure(out.failure)["explore_error"]
    (r,) = store.runs()
    assert r["status"] == "failed"


def test_anything_that_stops_a_run_ends_it_as_failed(h, monkeypatch):
    pack_dir, app, store, run = h

    def stop(self, j):
        raise KeyboardInterrupt
    monkeypatch.setattr(Runner, "journey", stop)
    with pytest.raises(KeyboardInterrupt):
        run("open-display")
    (r,) = store.runs()
    assert r["status"] == "failed" and r["error"] == "KeyboardInterrupt"

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


def test_a_new_screen_proposal_never_names_a_secret(h):
    from journeys.results import set_secrets
    pack_dir, app, store, run = h
    app["screens"]["TOUR"] = {"elements": [{"kind": "Text", "text": "Hello hunter2"},
                                           {"kind": "Button", "text": "Next hunter2", "to": "TOUR2"}]}
    app["screens"]["TOUR2"] = {"elements": [{"kind": "Text", "text": "Bye hunter2"}]}
    _display_tap_to(app, "TOUR")
    pack = Pack.load(pack_dir)
    try:
        r = Runner(pack, FakeDriver(app), store, store.new_run(), variables={"PW": "hunter2"}, sleep=lambda s: None)
        (out,) = r.run_all(pack.select("open-display"))
    finally:
        set_secrets([])
    f = store.failure(out.failure)
    p = store.proposal(f["proposal"])
    assert "HUNTER2" not in str(p).upper() and "HUNTER2" not in str(f).upper()


def test_a_proposal_with_a_masked_value_is_not_promoted(h):
    import pytest
    from journeys.heal import PromoteError
    pack_dir, app, store, run = h
    pid = store.add_proposal({"status": "proposed", "summary": "x", "change": {
        "kind": "new-screen", "name": "S", "match": {"all": [{"text": "Second •••"}]}, "edges": []}})
    with pytest.raises(PromoteError, match="masked"):
        promote(Pack.load(pack_dir), store, pid)


def _screen(*els):
    from journeys.drivers.base import Element, Screen
    return Screen([Element(f"@{i + 1}", k, t, rid) for i, (k, t, rid) in enumerate(els)])


def test_heal_gives_no_id_selector_that_maestro_would_read_as_other_views():
    """Maestro matches a bare name on the whole id or the part after the last "/": {id: next} would also hit
    pkg:id/group/next. Heal then falls back to the text; a name with a / is never an id selector."""
    from journeys.heal import selector
    s = _screen(("Button", "A", "com.x:id/group/next"), ("Button", "B", "com.x:id/next"))
    assert selector(s.elements[0], s) == "A" and selector(s.elements[1], s) == "B"


def test_the_deny_list_and_its_siblings_read_the_ids_last_part():
    from journeys.appmap import Rule
    from journeys.heal import denied, tap_targets
    s = _screen(("Button", "Open", "one:id/action"), ("Button", "Blocked", "two:id/action"),
                ("Button", "Manage", "com.x:id/settings/delete_account"), ("Button", "Fine", "com.x:id/ok"))
    deny = [Rule.parse({"text": "Blocked"}), Rule.parse({"text": "delete account"})]
    assert denied(s.elements[2], deny)  # the id's last part says what the button does
    assert [e.text for e in tap_targets(s, deny, 10)] == ["Fine"]  # Open shares the name action with Blocked


def test_a_package_change_alone_is_the_same_screen():
    from journeys.explore import _actions
    from journeys.heal import fingerprint
    a = _screen(("Button", "Open", "one:id/open"), ("Button", "Help", "one:id/help"))
    b = _screen(("Button", "Open", "two:id/open"), ("Button", "Help", "two:id/help"))
    assert fingerprint(a) == fingerprint(b) and _actions(a) == _actions(b)


def test_an_id_with_an_empty_name_is_no_id():
    from journeys.drivers.base import id_matches
    from journeys.drivers.maestro import parse_hierarchy
    tree = {"attributes": {}, "children": [{"attributes": {"resource-id": "group/"}},
                                           {"attributes": {"text": "Next", "resource-id": "com.x:id/next"}}]}
    assert [e.text for e in parse_hierarchy(tree).elements] == ["Next"]
    assert not id_matches("another/", "group/") and not id_matches("com.x:id/", "com.x:id/")


def test_round_2_deny_and_identity_keep_their_meaning():
    from journeys.appmap import Rule
    from journeys.heal import denied, fingerprint, new_denied, selector, tap_targets
    from journeys.explore import _actions
    nested = _screen(("Button", "Manage", "com.x:id/settings/delete_account"))
    assert denied(nested.elements[0], [Rule.parse({"id": "delete_account"})])  # as when drivers cut the id
    base = _screen(("Button", "Manage", "safe.pkg:id/action"))
    after = _screen(("Button", "Manage", "danger.pkg:id/action"))
    danger = [Rule.parse({"id": "danger.pkg:id/action"})]
    assert not denied(base.elements[0], danger) and denied(after.elements[0], danger)  # the package counts
    assert new_denied(base, after, danger)  # a package change, newly denied
    a = _screen(("Button", "Open", "com.x:id/inbox/open"))
    b = _screen(("Button", "Open", "com.x:id/archive/open"))
    assert fingerprint(a) != fingerprint(b) and _actions(a) != _actions(b)  # the path in the name stays identity
    v = _screen(("Button", "Open", "com.x:id/${NEXT}"))
    assert selector(v.elements[0], v) == "Open"  # Maestro refuses ${ in an id
    g = _screen(("Button", "Blocked", "group/"), ("Button", "Open", ""), ("Button", "Fine", "com.x:id/ok"))
    assert [e.text for e in tap_targets(g, [Rule.parse({"text": "Blocked"})], 10)] == ["Open", "Fine"]


def test_an_id_whose_name_ends_in_a_slash_matches_nothing():
    from journeys.drivers.base import id_matches
    assert not id_matches("com.x:id/group/", "com.x:id/group/") and not id_matches("group/", "group/")


def test_a_name_that_ends_in_a_slash_is_no_id_for_screen_identity():
    from journeys.explore import _actions
    from journeys.heal import fingerprint
    a, b = _screen(("Button", "Open", "com.x:id/group/")), _screen(("Button", "Open", ""))
    assert fingerprint(a) == fingerprint(b) and _actions(a) == _actions(b)

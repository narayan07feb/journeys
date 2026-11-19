"""The tracker: runs, work queue, agent activity and agent usage, from the state folder."""
import json
import threading
import urllib.request

from journeys.cli import main
from journeys.results import Store, current_actor


def cli(pack_dir, *args):
    return main(["--pack", str(pack_dir), "--state", str(pack_dir / "s"), *args])


def test_who_is_working(monkeypatch):
    for k in ("JOURNEYS_ACTOR", "AI_AGENT", "CLAUDECODE"):
        monkeypatch.delenv(k, raising=False)
    assert current_actor() == "person"
    monkeypatch.setenv("CLAUDECODE", "1")
    assert current_actor() == "claude-code"
    monkeypatch.setenv("AI_AGENT", "some-agent_1-2_agent")
    assert current_actor() == "some-agent"
    monkeypatch.setenv("JOURNEYS_ACTOR", "maya")
    assert current_actor() == "maya"


def test_agents_building_journeys_and_solving_issues_are_counted(pack_dir, monkeypatch, capsys):
    monkeypatch.setenv("JOURNEYS_ACTOR", "builder-agent")
    assert cli(pack_dir, "new", "check-about", "--start", "SETTINGS-HOME") == 0
    d = pack_dir / "drafts" / "check-about.yaml"
    d.write_text("name: check-about\nsteps:\n  - expect: SETTINGS-HOME\n  - goto: SETTINGS-ABOUT\n")
    assert cli(pack_dir, "promote-journey", "check-about") == 0
    monkeypatch.setenv("JOURNEYS_ACTOR", "ci")
    monkeypatch.delenv("DEMO_SEARCH", raising=False)
    assert cli(pack_dir, "run", "search-settings", "--driver", "fake") == 1  # DEMO_SEARCH missing: a failure
    monkeypatch.setenv("JOURNEYS_ACTOR", "fixer-agent")
    monkeypatch.setenv("DEMO_SEARCH", "display")
    assert cli(pack_dir, "run", "search-settings", "--driver", "fake") == 0  # passes again: fixed
    capsys.readouterr()
    assert cli(pack_dir, "tracker", "--json") == 0
    t = json.loads(capsys.readouterr().out)
    u = t["usage"]["actors"]
    assert u["builder-agent"]["drafts"] == 1 and u["builder-agent"]["journeys_built"] == 1
    assert u["fixer-agent"]["fixed"] == 1 and u["fixer-agent"]["solved"] == 1
    assert u["fixer-agent"]["avg_fix_minutes"] is not None and u["ci"]["runs"] == 1
    assert [r["actor"] for r in t["runs"]] == ["ci", "fixer-agent"]
    assert t["queue"]["failures"] == []  # the fixed failure left the queue
    acts = [(a["actor"], a["action"]) for a in t["activity"]]
    assert ("builder-agent", "draft.new") in acts and ("builder-agent", "journey.promote") in acts
    assert ("fixer-agent", "run.end") in acts
    assert any(t["usage"]["days"].values())


def test_decisions_count_as_solved_and_the_queue_lists_open_work(pack_dir, monkeypatch, capsys):
    from journeys.heal import reject
    store = Store(pack_dir / "s")
    store.add_failure({"journey": "open-display", "message": "landed on an unknown screen"})
    pid = store.add_proposal({"summary": "new screen X", "failure": "F-1", "journey": "open-display",
                              "change": {"kind": "new-screen", "name": "X", "match": {"all": [{"text": "X"}]}}})
    store.add_proposal({"summary": "still open", "change": {}})
    (pack_dir / "drafts").mkdir(exist_ok=True)
    (pack_dir / "drafts" / "wip.yaml").write_text("name: wip\nsteps:\n  - expect: SETTINGS-HOME\n")
    monkeypatch.setenv("JOURNEYS_ACTOR", "triage-agent")
    reject(store, pid, "a one-off dialog")
    assert cli(pack_dir, "tracker", "--json") == 0
    t = json.loads(capsys.readouterr().out)
    assert t["usage"]["actors"]["triage-agent"]["decided"] == 1
    assert [p["summary"] for p in t["queue"]["proposals"]] == ["still open"]
    assert t["queue"]["failures"] == [] and t["queue"]["drafts"] == ["wip"]  # a decided failure is not open work


def test_the_activity_log_masks_secrets_and_survives_a_cut_line(tmp_path, monkeypatch):
    from journeys.results import set_secrets
    monkeypatch.setenv("JOURNEYS_ACTOR", "a")
    store = Store(tmp_path)
    set_secrets(["hunter2"])
    try:
        store.log("mcp.call", tool="start_run", args={"target": "hunter2"})
    finally:
        set_secrets([])
    with open(tmp_path / "activity.jsonl", "a") as f:
        f.write('{"time": 1, "act')  # a crash mid-write
    (item,) = store.activity()
    assert "hunter2" not in json.dumps(item) and item["actor"] == "a"


def test_mcp_names_its_client_and_logs_each_tool_call(pack_dir, tmp_path, monkeypatch):
    from journeys.mcp_server import Server
    monkeypatch.delenv("JOURNEYS_ACTOR", raising=False)
    srv = Server(str(pack_dir), str(tmp_path / "s"))
    srv.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"clientInfo": {"name": "an-agent"}}})
    out = srv.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "tracker"}})
    assert not out["result"].get("isError")
    srv._call("failure", {"id": "F-99"})
    calls = [a for a in Store(tmp_path / "s").activity() if a["action"] == "mcp.call"]
    assert [(c["actor"], c["tool"], c["ok"]) for c in calls] == [("an-agent", "tracker", True),
                                                                 ("an-agent", "failure", False)]  # an unknown id
    assert Store(tmp_path / "s").activity()[0]["ms"] >= 0


def test_the_page_shows_all_four_parts(pack_dir, monkeypatch):
    from journeys.live import serve
    monkeypatch.setenv("JOURNEYS_ACTOR", "a")
    store = Store(pack_dir / "s")
    store.log("draft.new", journey="x")
    srv = serve(store, 0, lambda: ["wip"])
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}/"
        page = urllib.request.urlopen(base).read().decode()
        for part in ("Runs", "Work queue", "Agent activity", "Agent usage"):
            assert f">{part}<" in page
        t = json.loads(urllib.request.urlopen(base + "tracker.json").read())
        assert set(t) == {"runs", "queue", "activity", "usage"} and t["queue"]["drafts"] == ["wip"]
        assert t["usage"]["actors"]["a"]["drafts"] == 1
    finally:
        srv.shutdown()
        srv.server_close()


def test_tracker_text_output(pack_dir, monkeypatch, capsys):
    monkeypatch.setenv("JOURNEYS_ACTOR", "a")
    assert cli(pack_dir, "run", "open-display", "--driver", "fake") == 0
    capsys.readouterr()
    assert cli(pack_dir, "tracker") == 0
    out = capsys.readouterr().out
    assert "work queue:" in out and "  a: 0 / 0 / 0 / 0 / 1 / 0" in out


def test_a_fix_goes_to_whoever_promoted_it_not_the_run_that_showed_it(pack_dir, monkeypatch, capsys):
    from journeys.heal import promote
    from journeys.pack import Pack
    store = Store(pack_dir / "s")
    store.add_failure({"journey": "open-display", "message": "landed on an unknown screen"})
    pid = store.add_proposal({"summary": "new screen TIPS", "failure": "F-1", "journey": "open-display",
                              "change": {"kind": "new-screen", "name": "TIPS",
                                         "match": {"all": [{"text": "Tips for you"}]}, "edges": []}})
    monkeypatch.setenv("JOURNEYS_ACTOR", "healer-agent")
    promote(Pack.load(pack_dir), store, pid)
    monkeypatch.setenv("JOURNEYS_ACTOR", "ci")
    assert cli(pack_dir, "run", "open-display", "--driver", "fake") == 0
    capsys.readouterr()
    assert cli(pack_dir, "tracker", "--json") == 0
    u = json.loads(capsys.readouterr().out)["usage"]["actors"]
    assert u["healer-agent"]["decided"] == 1 and u["healer-agent"]["fixed"] == 1 and u["ci"]["fixed"] == 0

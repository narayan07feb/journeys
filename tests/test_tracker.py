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
    assert t["usage"]["actors"]["triage-agent"]["solved"] == 0  # a reject solves nothing
    assert [p["summary"] for p in t["queue"]["proposals"]] == ["still open"]
    assert [f["id"] for f in t["queue"]["failures"]] == ["F-1"]  # rejected: it still needs a heal by hand
    assert t["queue"]["drafts"] == ["wip"]


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
    assert u["healer-agent"]["solved"] == 1  # one issue, counted once


def test_odd_state_files_never_break_the_tracker_and_logging_never_breaks_a_command(pack_dir, monkeypatch, capsys):
    from journeys.heal import reject
    from journeys.tracker import summary
    store = Store(pack_dir / "s")
    (pack_dir / "s" / "runs" / "a").mkdir(parents=True)
    (pack_dir / "s" / "runs" / "a" / "run.json").write_text("[1]")
    (pack_dir / "s" / "runs" / "b").mkdir()
    (pack_dir / "s" / "runs" / "b" / "run.json").write_text(json.dumps(
        {"id": "b", "started": "x", "ended": "y", "actor": ["a"], "journeys": [None, {"name": 3, "status": "passed"}]}))
    (pack_dir / "s" / "failures.json").write_text(json.dumps({"a": 1}))
    (pack_dir / "s" / "activity.jsonl").write_text('{"time": "x", "action": "draft.new", "actor": ["a"]}\n'
                                                   '{"action": 5}\n[1]\n')
    t = summary(store, [])
    assert len(t["runs"]) == 1 and t["queue"]["failures"] == []
    assert cli(pack_dir, "tracker") == 0
    (pack_dir / "s" / "activity.jsonl").unlink()
    (pack_dir / "s" / "activity.jsonl").mkdir()  # the log cannot be written
    pid = store.add_proposal({"summary": "x", "change": {}})
    reject(store, pid, "not this one")  # the decision still stands, without its log line
    assert store.proposal(pid)["status"] == "rejected"
    assert cli(pack_dir, "new", "logless", "--start", "SETTINGS-HOME") == 0


def test_cli_and_mcp_mask_what_they_log(pack_dir, tmp_path, monkeypatch):
    from journeys.mcp_server import Server
    from journeys.results import set_secrets
    set_secrets([])
    monkeypatch.setenv("DEMO_SEARCH", "hunter2")
    srv = Server(str(pack_dir), str(tmp_path / "s"))
    pid = Store(tmp_path / "s").add_proposal({"summary": "x", "change": {}})
    srv._call("reject", {"id": pid, "why": "password hunter2 was on screen"})
    set_secrets([])
    monkeypatch.setenv("JOURNEYS_ACTOR", "a")
    (pack_dir / "drafts").mkdir(exist_ok=True)
    (pack_dir / "drafts" / "d.yaml").write_text("name: d\nsteps:\n  - expect: SETTINGS-HOME\n  - goto: SETTINGS-ABOUT\n")
    Store(pack_dir / "s").add_proposal({"summary": "y", "change": {}})
    assert cli(pack_dir, "reject", "P-1", "--why", "hunter2 seen") == 0  # the CLI path masks too
    for f in (tmp_path / "s" / "activity.jsonl", tmp_path / "s" / "proposals.json", pack_dir / "s" / "activity.jsonl"):
        assert "hunter2" not in f.read_text(), f
    set_secrets([])


def test_round_2_counts(pack_dir, monkeypatch, capsys):
    from journeys.tracker import summary
    monkeypatch.setenv("JOURNEYS_ACTOR", "a")
    store = Store(pack_dir / "s")
    store.log("draft.new", journey="j")
    store.log("journey.promote", journey="j")
    for i in range(80):
        store.add_failure({"journey": "open-display", "message": f"m{i}"})
    store.add_failure({"journey": "tap-all", "message": "the app crashed"})
    pid1 = store.add_proposal({"summary": "a", "failure": "F-1", "change": {}})
    pid2 = store.add_proposal({"summary": "b", "failure": "F-1", "change": {}})
    for pid in (pid1, pid2):
        store.update_proposal(pid, status="promoted")
        store.log("proposal.promote", id=pid, failure="F-1")
    t = summary(store, [])
    u = t["usage"]["actors"]["a"]
    assert (u["drafts"], u["journeys_built"], u["solved"]) == (1, 1, 1)  # two promotes, one issue
    q = t["queue"]
    assert len(q["failures"]) == 50 and q["failures_open"] == 80  # F-1 healed; 79 + the tap-all crash
    assert cli(pack_dir, "tracker") == 0
    out = capsys.readouterr().out
    assert "80 open failures" in out and "  a: 1 / 1 / 2 / 1 / 0 / 0" in out  # solved 1: healed, not fixed
    tappable = pack_dir / "pack.yaml"
    tappable.write_text(tappable.read_text().replace("allowed: false", "allowed: true"))
    assert cli(pack_dir, "tap-all", "--driver", "fake") == 0  # a clean walk closes the old crash
    capsys.readouterr()
    assert summary(store, [])["queue"]["failures_open"] == 79


def test_a_reject_earns_no_fix_credit(pack_dir, monkeypatch):
    from journeys.heal import reject
    from journeys.tracker import summary
    store = Store(pack_dir / "s")
    store.add_failure({"journey": "open-display", "message": "x"})
    pid = store.add_proposal({"summary": "a", "failure": "F-1", "journey": "open-display", "change": {}})
    monkeypatch.setenv("JOURNEYS_ACTOR", "rejecter")
    reject(store, pid, "no")
    monkeypatch.setenv("JOURNEYS_ACTOR", "hand-healer")
    assert cli(pack_dir, "run", "open-display", "--driver", "fake") == 0
    u = summary(store, [])["usage"]["actors"]
    assert u["rejecter"]["fixed"] == 0 and u["hand-healer"]["fixed"] == 1


def test_bad_bytes_odd_ids_and_impossible_times(pack_dir, monkeypatch):
    from journeys.results import set_secrets
    from journeys.tracker import summary
    store = Store(pack_dir / "s")
    (pack_dir / "s" / "activity.jsonl").write_bytes(b'\xff\xfe\n{"time": 1e30, "action": "draft.new", "actor": "a"}\n'
                                                     b'{"time": NaN, "action": "mcp.call", "actor": "a", "failure": ["x"]}\n')
    (pack_dir / "s" / "failures.json").write_text(json.dumps([{"id": ["a"], "journey": "x"}, {"id": "F-2", "journey": "x"}]))
    (pack_dir / "s" / "proposals.json").write_text(json.dumps([{"id": "P-1", "status": "promoted", "failure": {"a": 1}}]))
    t = summary(store, [])
    assert t["usage"]["actors"]["a"]["drafts"] == 1 and [f["id"] for f in t["queue"]["failures"]] == ["F-2"]
    monkeypatch.setenv("JOURNEYS_ACTOR", "a")
    set_secrets(["hunter2-SECRET"])
    try:
        store.log("proposal.reject", why="x" * 490 + "hunter2-SECRET", args={"why": "y" * 5000})
    finally:
        set_secrets([])
    last = store.activity()[-1]
    assert "hunter2" not in json.dumps(last) and len(last["args"]["why"]) <= 501


def test_round_3_what_counts_as_solved(pack_dir, monkeypatch):
    from journeys.tracker import summary
    monkeypatch.setenv("JOURNEYS_ACTOR", "a")
    store = Store(pack_dir / "s")
    # a detour promote is a change, not an issue
    for i in range(2):
        pid = store.add_proposal({"summary": f"detour {i}", "change": {}})
        store.update_proposal(pid, status="promoted")
        store.log("proposal.promote", id=pid)
    u = summary(store, [])["usage"]["actors"]["a"]
    assert (u["changes"], u["solved"], u["decided"]) == (2, 0, 2)


def _run(store, actor, target, started, ended, journeys=(), **meta):
    import time as _time
    r = store.new_run(target=target, **meta)
    r.data.update(actor=actor, started=started, ended=ended, status=meta.get("status", "passed"),
                  journeys=[{"name": n, "status": s} for n, s in journeys])
    r.save()
    _time.sleep(0.001)
    return r


def test_round_3_when_a_failure_is_fixed(pack_dir):
    from journeys.tracker import summary
    store = Store(pack_dir / "s")
    t0 = 1_700_000_000.0
    nights = []
    for night in range(3):  # one bug, failing three nights
        nights.append(store.add_failure({"journey": "checkout", "message": "x"}))
        store.update_failure(nights[-1], time=t0 + night * 86400)
    _run(store, "parallel", "all", t0 + 2 * 86400 - 60, t0 + 2 * 86400 + 60, [("checkout", "passed")])
    t = summary(store, [])
    assert t["queue"]["failures_open"] == 1  # a run that started before the last failure fixes only older ones
    _run(store, "fixer", "all", t0 + 3 * 86400, t0 + 3 * 86400 + 60, [("checkout", "passed")])
    u = summary(store, [])["usage"]["actors"]
    assert u["fixer"]["fixed"] == 1 and u["parallel"]["fixed"] == 1  # one pass, one issue each
    # a --dka crash is not closed by a plain or partial walk
    crash_run = _run(store, "w", "tap-all", t0 + 5 * 86400, t0 + 5 * 86400 + 60, status="failed", dka=True)
    f = store.add_failure({"journey": "tap-all", "message": "the app crashed", "run": crash_run.id})
    store.update_failure(f, time=t0 + 5 * 86400 + 30)
    _run(store, "w", "tap-all", t0 + 6 * 86400, t0 + 6 * 86400 + 60, dka=False)
    _run(store, "w", "tap-all", t0 + 7 * 86400, t0 + 7 * 86400 + 60, dka=True, not_visited=["NEW:X:1"])
    assert f in [x["id"] for x in summary(store, [])["queue"]["failures"]]
    _run(store, "w", "tap-all", t0 + 8 * 86400, t0 + 8 * 86400 + 60, dka=True)
    assert f not in [x["id"] for x in summary(store, [])["queue"]["failures"]]


def test_round_4_logging_and_odd_state(pack_dir, tmp_path, monkeypatch):
    from journeys.mcp_server import Server
    from journeys.tracker import summary
    blocker = tmp_path / "file"
    blocker.write_text("")
    assert main(["--pack", str(pack_dir), "--state", str(blocker / "s"), "new", "bar",
                 "--start", "SETTINGS-HOME"]) == 0  # the draft is written; a state folder it cannot make is no error
    assert (pack_dir / "drafts" / "bar.yaml").exists()
    # a broken pack.yaml does not hide results that are already in the state folder
    state = tmp_path / "st"
    Store(state).add_failure({"journey": "x", "message": "m"})
    (pack_dir / "pack.yaml").write_text("name: [\n")
    out = Server(str(pack_dir), str(state))._call("failures", {})
    assert not out.get("isError") and "F-1" in out["content"][0]["text"]
    # unhashable ids and NaN never break the tracker or the page's JSON
    store = Store(tmp_path / "odd")
    (tmp_path / "odd" / "runs" / "r").mkdir(parents=True)
    (tmp_path / "odd" / "runs" / "r" / "run.json").write_text(json.dumps({"id": ["a"], "target": "tap-all"}))
    (tmp_path / "odd" / "failures.json").write_text('[{"id": "F-1", "journey": "tap-all", "run": ["x"],'
                                                    ' "time": NaN, "message": NaN}]')
    t = summary(store, [])
    json.dumps(t, allow_nan=False)
    assert t["queue"]["failures"][0]["message"] is None


def test_one_pass_is_one_issue_credited_to_the_latest_promoter(pack_dir):
    from journeys.tracker import summary
    store = Store(pack_dir / "s")
    t0 = 1_700_000_000.0
    f1 = store.add_failure({"journey": "j", "message": "x"})
    store.update_failure(f1, time=t0)
    f2 = store.add_failure({"journey": "j", "message": "x"})
    store.update_failure(f2, time=t0 + 86400)
    with open(pack_dir / "s" / "activity.jsonl", "w") as f:  # A heals the second night's failure
        f.write(json.dumps({"time": t0 + 86400 + 100, "actor": "A", "action": "proposal.promote", "failure": f2}) + "\n")
    r = store.new_run(target="all")
    r.data.update(actor="ci", started=t0 + 2 * 86400, ended=t0 + 2 * 86400 + 60, status="passed",
                  journeys=[{"name": "j", "status": "passed"}])
    r.save()
    u = summary(store, [])["usage"]["actors"]
    assert u["A"]["fixed"] == 1 and u.get("ci", {}).get("fixed", 0) == 0


def test_the_page_answers_only_local_hosts(pack_dir):
    import http.client
    from journeys.live import serve
    srv = serve(Store(pack_dir / "s"), 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        port = srv.server_address[1]
        for host, code in (("evil.example", 403), (f"127.0.0.1:{port}", 200), (f"localhost:{port}", 200)):
            c = http.client.HTTPConnection("127.0.0.1", port)
            c.request("GET", "/tracker.json", headers={"Host": host})
            assert c.getresponse().status == code, host
            c.close()
    finally:
        srv.shutdown()
        srv.server_close()


def test_round_5_minors(pack_dir, tmp_path):
    from journeys.mcp_server import Server
    from journeys.tracker import summary
    store = Store(tmp_path / "s")
    (tmp_path / "s" / "activity.jsonl").write_text('{"time": NaN, "action": "draft.new", "actor": "a", "x": NaN}\n')
    (tmp_path / "s" / "runs" / "r").mkdir(parents=True)
    (tmp_path / "s" / "runs" / "r" / "run.json").write_text('{"id": "r", "error": NaN, "started": 1700000000}')
    json.dumps(summary(store, []), allow_nan=False)  # the page's JSON never holds NaN
    # an old day with activity is outside the last 14 calendar days
    with open(tmp_path / "s" / "activity.jsonl", "a") as f:
        f.write(json.dumps({"time": 1_600_000_000, "action": "draft.new", "actor": "old"}) + "\n")
    t = summary(store, [])
    assert t["usage"]["actors"]["old"]["drafts"] == 1 and not any("old" in r for r in t["usage"]["days"].values())
    # a detour promote between a failure and the pass takes no credit
    t0 = 1_700_000_000.0
    f = store.add_failure({"journey": "j", "message": "x"})
    store.update_failure(f, time=t0 + 10)
    with open(tmp_path / "s" / "activity.jsonl", "a") as fh:
        fh.write(json.dumps({"time": t0 + 20, "actor": "detourer", "action": "proposal.promote", "journey": "j"}) + "\n")
    r = store.new_run(target="all")
    r.data.update(actor="ci", started=t0 + 30, ended=t0 + 40, status="passed", journeys=[{"name": "j", "status": "passed"}])
    r.save()
    u = summary(store, [])["usage"]["actors"]
    assert u["ci"]["fixed"] == 1 and u["detourer"]["fixed"] == 0
    # the MCP tracker still answers with --state while pack.yaml is broken
    (pack_dir / "pack.yaml").write_text("name: [\n")
    out = Server(str(pack_dir), str(tmp_path / "s"))._call("tracker", {})
    assert not out.get("isError")


def test_a_failed_set_hook_or_cleanup_is_open_work_until_it_passes_again(pack_dir, monkeypatch, capsys):
    from journeys.mcp_server import Server
    (pack_dir / "hooks" / "flaky.sh").write_text('#!/bin/sh\n[ -e "$JOURNEYS_PACK/broken" ] && { echo down >&2; exit 3; }\nexit 0\n')
    (pack_dir / "hooks" / "flaky.sh").chmod(0o755)
    f = pack_dir / "pack.yaml"
    f.write_text(f.read_text().replace("hooks:\n", "hooks:\n  flaky: [hooks/flaky.sh]\n"))
    (pack_dir / "sets.yaml").write_text("sets:\n  smoke: {tags: [smoke], before: [flaky]}\n")
    (pack_dir / "journeys" / "cl.yaml").write_text("name: cl\nafter: [flaky]\nsteps:\n  - expect: SETTINGS-HOME\n")
    (pack_dir / "broken").write_text("")
    monkeypatch.setenv("JOURNEYS_ACTOR", "ci")
    assert cli(pack_dir, "run", "smoke", "--driver", "fake") == 1
    assert cli(pack_dir, "run", "cl", "--driver", "fake") == 1
    assert cli(pack_dir, "run", "smoke", "--driver", "fake") == 1  # fails again: the first one stays open
    capsys.readouterr()
    assert cli(pack_dir, "tracker", "--json") == 0
    q = json.loads(capsys.readouterr().out)["queue"]
    assert sorted(x["journey"] for x in q["failures"]) == ["cl", "set smoke before flaky", "set smoke before flaky"]
    assert cli(pack_dir, "failures") == 0
    assert "set smoke before flaky step 0 (set before: hook flaky): hook flaky exited 3: down" in capsys.readouterr().out
    t = Server(str(pack_dir), str(pack_dir / "s")).tracker()
    assert t["queue"]["failures_open"] == 3
    assert cli(pack_dir, "tracker") == 0
    assert "work queue: 3 open failures" in capsys.readouterr().out
    (pack_dir / "broken").unlink()
    monkeypatch.setenv("JOURNEYS_ACTOR", "fixer-agent")
    assert cli(pack_dir, "run", "smoke", "--driver", "fake") == 0  # the hook passes again: fixed
    assert cli(pack_dir, "run", "cl", "--driver", "fake") == 0
    capsys.readouterr()
    assert cli(pack_dir, "tracker", "--json") == 0
    t = json.loads(capsys.readouterr().out)
    assert t["queue"]["failures"] == [] and t["usage"]["actors"]["fixer-agent"]["solved"] == 2

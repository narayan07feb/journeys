import json
import subprocess
import sys


def rpc(proc, mid, method, params=None):
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}}) + "\n")
    proc.stdin.flush()
    return json.loads(proc.stdout.readline())


def call(proc, mid, name, **args):
    r = rpc(proc, mid, "tools/call", {"name": name, "arguments": args})["result"]
    text = r["content"][0]["text"]
    return r.get("isError", False), (json.loads(text) if text[:1] in "[{" else text)


def test_mcp_over_a_pipe(pack_dir, tmp_path):
    proc = subprocess.Popen(
        [sys.executable, "-m", "journeys", "--pack", str(pack_dir), "--state", str(tmp_path / "s"), "mcp"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        init = rpc(proc, 1, "initialize", {"protocolVersion": "2025-06-18"})
        assert init["result"]["serverInfo"]["name"] == "journeys"
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        tools = {t["name"] for t in rpc(proc, 2, "tools/list")["result"]["tools"]}
        assert {"start_run", "wait_run", "failure", "promote", "reject"} <= tools

        err, doc = call(proc, 3, "doctor")
        assert not err and doc["errors"] == [] and doc["journeys"] == 3
        err, msg = call(proc, 4, "start_run", target="smoke")
        assert err and "serial is required" in msg
        err, msg = call(proc, 5, "start_run", target="nope", driver="fake")
        assert err and "no journey or set named nope" in msg

        err, started = call(proc, 6, "start_run", target="everything", driver="fake")
        assert not err
        err, done = call(proc, 7, "wait_run", run_id=started["run_id"], timeout_s=60)
        assert done["status"] == "failed"  # search-settings needs DEMO_SEARCH, which is not set
        by = {j["name"]: j for j in done["journeys"]}
        assert by["open-display"]["status"] == "passed"
        fid = by["search-settings"]["failure"]
        err, f = call(proc, 8, "failure", id=fid)
        assert f["message"] == "env var DEMO_SEARCH is not set"
        err, fl = call(proc, 9, "failures")
        assert fl[-1]["id"] == fid
        err, msg = call(proc, 10, "promote", id="P-9")
        assert err and "no proposal P-9" in msg
        assert rpc(proc, 11, "nope")["error"]["code"] == -32601
        err, msg = call(proc, 12, "no_such_tool")
        assert err
    finally:
        proc.stdin.close()
        proc.wait(timeout=10)


def test_run_that_cannot_start_reports_it(pack_dir, tmp_path):
    from journeys.mcp_server import Server
    (pack_dir / "journeys" / "x.yaml").write_text("name: x\nsteps:\n  - expect: NOPE\n")
    srv = Server(str(pack_dir), str(tmp_path / "s"))
    started = srv.start_run("smoke", driver="fake")  # the target exists; the child refuses the broken pack
    st = srv.wait_run(started["run_id"], timeout_s=30)
    assert st["status"] == "failed to start" and "unknown screen NOPE" in st["log"]


def test_start_run_asks_for_a_driver_the_pack_does_not_name(pack_dir, tmp_path):
    """Owner, 19 Nov: never assume a driver; the agent asks the user and passes the answer."""
    import pytest
    from journeys.mcp_server import Server
    f = pack_dir / "pack.yaml"
    f.write_text("\n".join(ln for ln in f.read_text().splitlines() if not ln.startswith("driver:")) + "\n")
    srv = Server(str(pack_dir), str(tmp_path / "s"))
    with pytest.raises(ValueError, match="ask the user which tool"):
        srv.start_run("smoke", serial="emulator-5554")
    assert not (tmp_path / "s").exists()

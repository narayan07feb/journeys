import json
import threading
import urllib.request

from journeys.cli import main
from journeys.live import panel, serve, watch
from journeys.results import Store, write_json


def test_live_json_written_during_run(pack_dir, tmp_path, capsys):
    base = ["--pack", str(pack_dir), "--state", str(tmp_path / "s")]
    assert main(base + ["run", "open-display", "--driver", "fake"]) == 0
    live = json.loads((tmp_path / "s" / "live.json").read_text())
    assert live["journey"] == "open-display" and live["status"] == "passed"
    assert live["step"] == "5 of 5" and live["screen"] == "SETTINGS-HOME"
    p = panel(live)
    assert p["status"] == "passed" and ["Screen", "SETTINGS-HOME"] in p["rows"]


def test_history_marks_flaky(tmp_path):
    store = Store(tmp_path)
    for statuses in (["passed", "passed"], ["failed", "passed"], ["passed", "passed"], ["failed", "passed"]):
        run = store.new_run()
        run.data["journeys"] = [{"name": "a", "status": statuses[0]}, {"name": "b", "status": statuses[1]}]
        run.save()
    h = store.history()
    assert h["a"]["results"] == ["passed", "failed", "passed", "failed"] and h["a"]["flaky"]
    assert not h["b"]["flaky"] and h["b"]["passed"] == 4
    run = store.new_run()
    run.data["journeys"] = [{"name": "c", "status": "passed"}]
    run.save()
    for _ in range(2):
        run = store.new_run()
        run.data["journeys"] = [{"name": "c", "status": "failed"}]
        run.save()
    assert not store.history()["c"]["flaky"]  # broke once and stays broken: a real break


def test_server_serves_page_and_json(tmp_path):
    store = Store(tmp_path)
    write_json(tmp_path / "live.json", {"run": "r1", "journey": "j"})
    srv = serve(store, 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        assert b"Journeys" in urllib.request.urlopen(base + "/").read()
        assert json.load(urllib.request.urlopen(base + "/live.json"))["run"] == "r1"
        assert json.load(urllib.request.urlopen(base + "/failures.json")) == []
        assert srv.server_address[0] == "127.0.0.1"
    finally:
        srv.shutdown()
        srv.server_close()


def test_watch_yields_changes_only(tmp_path):
    store = Store(tmp_path)
    write_json(tmp_path / "live.json", {"run": "r", "step": "1 of 2"})
    got = list(watch(store, interval=0.01, stop_after=0.1))
    assert got == [{"run": "r", "step": "1 of 2"}]

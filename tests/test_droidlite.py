import json
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGIN = REPO / "droid-lite-plugin"


def lines(out: str):
    return [json.loads(ln) for ln in out.splitlines() if ln.strip()]


def run_cmd(key, pack_dir, tmp_path, env=None, view=None, **fmt):
    spec = json.loads((PLUGIN / "plugin.json").read_text())
    argv = spec[key] if view is None else next(v["run"] for v in spec[key] if v["name"] == view)
    argv = [a.replace("{plugin}", str(PLUGIN.resolve())).replace("{id}", fmt.get("id", "")) for a in argv]
    e = {k: v for k, v in os.environ.items() if not k.startswith("JOURNEYS_")}
    e.update({"JOURNEYS_PACK": str(pack_dir), "JOURNEYS_STATE": str(tmp_path / "s"),
              "PYTHONDONTWRITEBYTECODE": "1", **(env or {})})
    cwd = (PLUGIN.resolve() / spec["dir"]).resolve()
    return subprocess.run(argv, cwd=cwd, env=e, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)


def test_plugin_json_shape():
    spec = json.loads((PLUGIN / "plugin.json").read_text())
    assert isinstance(spec["name"], str) and isinstance(spec["dir"], str)
    for key in ("list", "run", "watch"):
        assert all(isinstance(a, str) for a in spec[key])
    assert "{id}" in spec["run"]
    for item in spec["views"] + spec["actions"]:
        assert isinstance(item["name"], str) and all(isinstance(a, str) for a in item["run"])
    assert set(spec) <= {"name", "dir", "list", "run", "watch", "views", "actions", "links"}
    # nothing but plugin.json in the plugin folder: the OK hash stays stable
    assert sorted(p.name for p in PLUGIN.iterdir()) == ["plugin.json"]


def test_list_run_views_doctor(pack_dir, tmp_path):
    p = run_cmd("list", pack_dir, tmp_path)
    tree = json.loads(p.stdout)
    assert tree[0]["id"] == "all" and {c["id"] for c in tree[0]["children"]} == {
        "open-display", "android-version", "search-settings"}
    assert tree[0]["children"][0]["file"].endswith(".yaml")
    assert {c["id"] for c in tree[1]["children"]} == {"set:smoke", "set:everything"}

    p = run_cmd("run", pack_dir, tmp_path, env={"JOURNEYS_DRIVER": "maestro"}, id="open-display")
    assert p.returncode == 1 and "JOURNEYS_SERIAL" in p.stdout  # no device, no fake: refused

    p = run_cmd("run", pack_dir, tmp_path, env={"JOURNEYS_DRIVER": "fake"}, id="set:everything")
    out = lines(p.stdout)
    final = {ln["case"]: ln for ln in out if "case" in ln and ln["status"] != "running"}
    assert final["open-display"]["status"] == "passed"
    assert final["search-settings"]["status"] == "failed"
    assert final["search-settings"]["line"] == 13 and final["search-settings"]["file"].endswith("about.yaml")
    assert final["set:everything"]["status"] == "failed" and p.returncode == 1
    assert any(ln.get("status") == "running" for ln in out)

    f = json.loads(run_cmd("views", pack_dir, tmp_path, view="Failures").stdout)
    assert f[0]["message"] == "env var DEMO_SEARCH is not set"
    assert json.loads(run_cmd("views", pack_dir, tmp_path, view="Proposals").stdout) == []

    d = lines(run_cmd("actions", pack_dir, tmp_path, view="Doctor").stdout)
    assert d[0]["status"] == "passed" and d[2]["status"] == "skipped"
    assert sorted(p.name for p in PLUGIN.iterdir()) == ["plugin.json"]  # runs wrote nothing there


def test_droid_lite_never_takes_a_driver_nobody_chose(pack_dir, tmp_path, monkeypatch):
    from journeys import droidlite
    from journeys.pack import Pack
    f = pack_dir / "pack.yaml"
    f.write_text("\n".join(ln for ln in f.read_text().splitlines() if not ln.startswith("driver:")) + "\n")
    p = run_cmd("run", pack_dir, tmp_path, env={"JOURNEYS_SERIAL": "emulator-5554"}, id="open-display")
    assert p.returncode == 1 and "ask the user which tool" in p.stdout
    for k in ("JOURNEYS_PACK", "JOURNEYS_DRIVER", "DROID_LITE_PROJECT"):
        monkeypatch.delenv(k, raising=False)
    example = Pack.load(droidlite.pack_path())  # the fallback names no driver: nobody chose one
    assert example.driver == "" and droidlite.driver_kind(example) is None
    monkeypatch.setenv("JOURNEYS_DRIVER", "speedrun")
    assert droidlite.driver_kind(example) == "speedrun"


def test_a_stopped_droid_lite_run_ends_failed(pack_dir, tmp_path):
    import signal
    import sys
    import time
    import yaml
    (pack_dir / "hooks" / "note.sh").write_text("#!/bin/sh\nsleep 30\n")
    state = tmp_path / "s"
    env = {k: v for k, v in os.environ.items() if not k.startswith("JOURNEYS_")}
    env.update({"JOURNEYS_PACK": str(pack_dir), "JOURNEYS_STATE": str(state), "JOURNEYS_DRIVER": "fake",
                "PYTHONDONTWRITEBYTECODE": "1"})
    proc = subprocess.Popen([sys.executable, "-m", "journeys.droidlite", "run", "android-version"], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=REPO)
    for _ in range(100):
        runs = list(state.rglob("run.json"))
        if runs and "running" in runs[0].read_text():
            break
        time.sleep(0.1)
    time.sleep(1.0)  # inside the slow hook
    proc.send_signal(signal.SIGTERM)
    assert proc.wait(timeout=20) != 0
    data = yaml.safe_load(runs[0].read_text())
    assert data["status"] == "failed" and data.get("ended")


def test_droid_lite_names_a_bad_driver(pack_dir, tmp_path):
    p = run_cmd("run", pack_dir, tmp_path, env={"JOURNEYS_DRIVER": "foo"}, id="open-display")
    assert p.returncode == 1 and "driver foo is not one of" in p.stdout


def test_droid_lite_a_failing_stop_keeps_the_results(pack_dir, tmp_path, monkeypatch, capsys):
    from journeys import droidlite
    from journeys.drivers.base import DriverError
    from journeys.drivers.fake import FakeDriver
    from journeys.pack import Pack
    monkeypatch.setattr(FakeDriver, "stop", lambda self: (_ for _ in ()).throw(DriverError("timeout")))
    monkeypatch.setenv("JOURNEYS_DRIVER", "fake")
    monkeypatch.setenv("JOURNEYS_STATE", str(tmp_path / "s"))
    assert droidlite.cmd_run(Pack.load(pack_dir), "set:smoke") == 0
    assert '"case": "set:smoke", "status": "passed"' in capsys.readouterr().out

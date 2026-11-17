import json

from journeys.cli import main


def test_doctor_list_run(pack_dir, tmp_path, capsys, monkeypatch):
    base = ["--pack", str(pack_dir), "--state", str(tmp_path / "s")]
    assert main(base + ["doctor"]) == 0
    assert "OK   pack settings-demo" in capsys.readouterr().out
    assert main(base + ["list", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["sets"]["smoke"] == ["android-version", "open-display"]
    assert main(base + ["run", "smoke", "--driver", "fake"]) == 0
    assert "2/2 passed" in capsys.readouterr().out
    monkeypatch.delenv("DEMO_SEARCH", raising=False)
    assert main(base + ["run", "search-settings", "--driver", "fake"]) == 1
    assert main(base + ["failures"]) == 0
    assert "F-1  search-settings" in capsys.readouterr().out
    assert main(base + ["failures", "F-1"]) == 0
    assert json.loads(capsys.readouterr().out)["message"] == "env var DEMO_SEARCH is not set"


def test_broken_pack_refuses_to_run(pack_dir, tmp_path, capsys):
    (pack_dir / "journeys" / "x.yaml").write_text("name: x\nsteps:\n  - expect: NOPE\n")
    base = ["--pack", str(pack_dir), "--state", str(tmp_path / "s")]
    assert main(base + ["doctor"]) == 1
    assert main(base + ["run", "x", "--driver", "fake"]) == 2
    assert "unknown screen NOPE" in capsys.readouterr().err


def test_no_pack(capsys, monkeypatch):
    monkeypatch.delenv("JOURNEYS_PACK", raising=False)
    assert main(["list"]) == 2


def test_real_driver_needs_serial(pack_dir, tmp_path, capsys):
    base = ["--pack", str(pack_dir), "--state", str(tmp_path / "s")]
    assert main(base + ["run", "smoke"]) == 2
    assert "--serial" in capsys.readouterr().err


def test_proposal_commands(pack_dir, tmp_path, capsys):
    import yaml
    app = yaml.safe_load((pack_dir / "fake" / "app.yaml").read_text())
    app["start"] = "ABOUT"
    app["screens"]["ABOUT"]["back"] = "HOME"
    fake = tmp_path / "app.yaml"
    fake.write_text(yaml.safe_dump(app))
    base = ["--pack", str(pack_dir), "--state", str(tmp_path / "s")]
    assert main(base + ["run", "open-display", "--driver", "fake", "--fake-app", str(fake)]) == 0
    assert "passed  open-display  (changes: P-1)" in capsys.readouterr().out
    assert main(base + ["proposals"]) == 0
    assert "P-1  proposed" in capsys.readouterr().out
    assert main(base + ["reject", "P-1", "--why", "test"]) == 0
    assert main(base + ["promote", "P-1"]) == 2
    assert "already rejected" in capsys.readouterr().err

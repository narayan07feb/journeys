import yaml

from journeys.cli import main
from journeys.pack import Pack


def cli(pack_dir, *args):
    return main(["--pack", str(pack_dir), "--state", str(pack_dir / "s"), *args])


def test_draft_lifecycle_new_validate_run_promote(pack_dir, capsys):
    assert cli(pack_dir, "new", "check-about", "--start", "SETTINGS-HOME") == 0
    f = pack_dir / "drafts" / "check-about.yaml"
    f.write_text("name: check-about\ntags: [smoke]\nsteps:\n  - expect: SETTINGS-HOME\n  - goto: SETTINGS-ABOUT\n"
                 "  - do: [{scroll_to: \"/Android version.*/\"}]\n  - see: \"/Android version.*/\"\n")
    assert cli(pack_dir, "validate") == 0
    pack = Pack.load(pack_dir)
    assert "check-about" in pack.drafts and "check-about" not in [j.name for j in pack.select("all")]
    assert "check-about" not in [j.name for j in pack.select("smoke")]  # drafts never join sets
    assert cli(pack_dir, "run", "check-about", "--draft", "--driver", "fake") == 0
    assert cli(pack_dir, "run", "check-about", "--driver", "fake") == 2  # a draft is not a journey yet
    assert cli(pack_dir, "promote-journey", "check-about") == 0
    assert (pack_dir / "journeys" / "check-about.yaml").exists() and not f.exists()
    assert "check-about" in [j.name for j in Pack.load(pack_dir).select("smoke")]


def test_validate_reports_lines_and_promote_refuses_errors_and_duplicates(pack_dir, capsys):
    d = pack_dir / "drafts"
    d.mkdir()
    sf = pack_dir / "map" / "screens.yaml"
    sf.write_text(sf.read_text() + "  ORPHAN:\n    match: {all: [{text: Lost}]}\n")
    (d / "bad.yaml").write_text("name: bad\nsteps:\n  - expect: SETTINGS-ABOUT\n  - goto: ORPHAN\n"
                                "  - do: [{tapOn: X}]\n")
    assert cli(pack_dir, "validate") == 1
    out = capsys.readouterr().out
    assert "bad.yaml:4: goto ORPHAN: the map has no path from SETTINGS-ABOUT" in out
    assert "bad.yaml:5: unknown action tapOn" in out
    assert cli(pack_dir, "promote-journey", "bad") == 2
    (d / "bad.yaml").unlink()
    (d / "twin.yaml").write_text((pack_dir / "journeys" / "display.yaml").read_text().replace("open-display", "twin"))
    assert cli(pack_dir, "promote-journey", "twin") == 2
    assert "duplicate" in capsys.readouterr().err


def test_export_to_maestro_and_import_back(pack_dir, tmp_path, capsys):
    out = tmp_path / "open-display.yaml"
    assert cli(pack_dir, "export", "maestro", "open-display", "-o", str(out)) == 0
    flow = list(yaml.safe_load_all(out.read_text()))
    assert flow[0] == {"appId": "com.android.settings"}
    cmds = flow[1]
    assert cmds[0] == "launchApp"
    assert {"assertVisible": {"text": ".*(?:^Network & internet).*"}} in cmds  # expect SETTINGS-HOME
    assert {"tapOn": "Display.*"} in cmds and "back" in cmds        # goto via the map's edges
    assert {"scrollUntilVisible": {"element": {"text": "Dark theme.*"}, "direction": "DOWN"}} in cmds
    assert cli(pack_dir, "export", "maestro", "android-version") == 0
    assert "hook steps run only in Journeys" in capsys.readouterr().err

    (tmp_path / "flows").mkdir()
    (tmp_path / "flows" / "login.yaml").write_text(
        "appId: com.android.settings\n---\n- launchApp:\n    clearState: true\n- tapOn: \"Sign in\"\n"
        "- tapOn: {id: email}\n- inputText: me@example.com\n- assertVisible: \"Welcome.*\"\n"
        "- tapOn: {point: \"50%,50%\"}\n- runFlow: other.yaml\n- pressKey: Enter\n")
    assert cli(pack_dir, "import", "maestro", str(tmp_path / "flows")) == 0
    notes = capsys.readouterr().out
    assert "command 6" in notes and "command 7" in notes
    draft = yaml.safe_load((pack_dir / "drafts" / "login.yaml").read_text())
    assert draft == {"name": "login", "tags": ["imported"], "start": {"launch": True, "clear_state": True},
                     "steps": [{"do": [{"tap": "Sign in"}, {"tap": {"id": "email"}}, {"type": "me@example.com"}]},
                               {"see": "/Welcome.*/"}, {"do": [{"press": "enter"}]}]}
    assert not [e for e in Pack.load(pack_dir).errors if "login" in e]

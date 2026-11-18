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
    err = capsys.readouterr().err
    assert "goto SETTINGS-HOME: start screen unknown" in err and "screen SETTINGS-HOME: its any rules" in err
    flow = list(yaml.safe_load_all(out.read_text()))
    assert flow[0] == {"appId": "com.android.settings"}
    cmds = flow[1]
    assert cmds[0] == "launchApp"
    assert {"assertVisible": {"text": ".*(?:^Network & internet).*"}} in cmds  # expect SETTINGS-HOME
    assert {"tapOn": "Display.*"} in cmds                            # goto via the map's edges
    assert "back" not in cmds  # the last goto follows a `do`: the screen is unknown, so it is left out
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


def test_review_round_1_fixes(pack_dir, tmp_path, capsys):
    d = pack_dir / "drafts"
    d.mkdir()
    # `new` never writes over a draft file, even one that does not load
    (d / "unloadable.yaml").write_text("name: unloadable\nsteps:\n  - hook\n")  # does not load at all
    assert "unloadable" not in Pack.load(pack_dir).drafts
    assert cli(pack_dir, "new", "unloadable") == 2 and "- hook" in (d / "unloadable.yaml").read_text()
    (d / "unloadable.yaml").unlink()
    (d / "broken.yaml").write_text("name: broken\nsteps:\n  - do: [{tapOn: X}]\n")
    # an unfinished draft never stops journeys and sets; it stops only its own draft run
    assert cli(pack_dir, "run", "smoke", "--driver", "fake") == 0
    assert cli(pack_dir, "run", "broken", "--draft", "--driver", "fake") == 2
    # promote of a draft that does not load shows its errors
    capsys.readouterr()
    assert cli(pack_dir, "promote-journey", "broken") == 2
    assert "unknown action tapOn" in capsys.readouterr().err
    # import: dict eraseText, keys with no neutral word, names, config.yaml and non-flows
    fl = tmp_path / "fl"
    fl.mkdir()
    (fl / "Sign In_2.yaml").write_text("appId: a\n---\n- eraseText: {charactersToErase: 5}\n- pressKey: Volume Up\n")
    (fl / "config.yaml").write_text("flows: ['*']\n")
    (fl / "notes.yaml").write_text("just: a map\n")
    assert cli(pack_dir, "import", "maestro", str(fl)) == 0
    out = capsys.readouterr().out
    assert "pressKey Volume Up: not a neutral key" in out and "SKIP" in out and "notes.yaml" in out
    assert "config" not in [p.stem for p in d.iterdir()]
    assert yaml.safe_load((d / "sign-in-2.yaml").read_text())["steps"] == [{"do": [{"erase": 5}]}]
    # a bad regex is a validate error, not a run-time "not found"
    (d / "re.yaml").write_text("name: re\nsteps:\n  - see: \"/(/\"\n")
    assert cli(pack_dir, "validate") == 1 and "bad regex /(/" in capsys.readouterr().out


def test_export_then_import_is_a_round_trip(pack_dir, tmp_path):
    from journeys.formats.maestro import export, import_flow
    (pack_dir / "journeys" / "rt.yaml").write_text(
        "name: rt\nstart: {launch: true, clear_state: true}\nsteps:\n  - see: \"/Network.*/\"\n"
        "  - do: [{tap: \"Display (new)\"}, {type: \"a b\"}, {press: enter}, {scroll_to: {id: list}}, back]\n"
        "  - not_see: Gone\n")
    pack = Pack.load(pack_dir)
    flow, notes = export(pack, pack.journeys["rt"])
    f = tmp_path / "rt.yaml"
    f.write_text(flow)
    draft, notes = import_flow(f)
    assert notes == []
    assert draft["start"] == {"launch": True, "clear_state": True}
    assert draft["steps"] == [{"see": "/Network.*/"},
                              {"do": [{"tap": "Display (new)"}, {"type": "a b"}, {"press": "enter"},
                                      {"scroll_to": {"id": "list"}}, "back"]},
                              {"not_see": "Gone"}]

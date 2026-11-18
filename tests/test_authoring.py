import pytest
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


def test_drafts_are_safe_and_import_survives_odd_flows(pack_dir, tmp_path, capsys):
    d = pack_dir / "drafts"
    d.mkdir()
    # `new` never writes over a draft file, even one that does not load
    (d / "unloadable.yaml").write_text("name: unloadable\nsteps: [\n")  # not even YAML: does not load
    assert "unloadable" not in Pack.load(pack_dir).drafts
    assert cli(pack_dir, "new", "unloadable") == 2 and "steps: [" in (d / "unloadable.yaml").read_text()
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


def test_selectors_step_words_and_launch_arguments_are_checked(pack_dir, tmp_path, capsys):
    d = pack_dir / "drafts"
    d.mkdir()
    (d / "a.yaml").write_text("name: a\nsteps:\n  - see: {text: Nope, index: -1}\n  - see: {text: X, index: x}\n"
                              "  - see: \"\"\n  - see: true\n")
    (d / "b.yaml").write_text("name: b\nsteps:\n  - tapOn: Display\n  - assertVisible: Foo\n")
    assert cli(pack_dir, "validate") == 1
    out = capsys.readouterr().out
    assert out.count("index is a whole number") == 2 and "write `do: [{tap: ...}]`" in out
    assert "write `see: ...`" in out and "non-empty text" in out and "not True" in out
    # import: launch/stop arguments are kept or listed, never guessed
    f = tmp_path / "f.yaml"
    f.write_text("appId: com.android.settings\n---\n- launchApp\n- tapOn: Hi\n- launchApp: {clearState: true}\n"
                 "- stopApp: {appId: other.app}\n- scrollUntilVisible: {element: X, timeout: 5000}\n- stopApp\n")
    from journeys.formats.maestro import import_flow
    draft, notes = import_flow(f, "com.android.settings")
    assert draft["steps"] == [{"do": [{"tap": "Hi"}, {"launch": {"clear_state": True}}, "stop"]}]
    assert any("command 4" in n and "only the pack's app" in n for n in notes)
    assert any("command 5" in n and "timeout" in n for n in notes)


def test_journeys_differing_only_in_start_are_not_duplicates(pack_dir):
    from journeys.authoring import promote_journey
    d = pack_dir / "drafts"
    d.mkdir()
    (d / "fresh.yaml").write_text((pack_dir / "journeys" / "display.yaml").read_text()
                                  .replace("open-display", "fresh").replace("tags:", "start: {clear_state: true}\ntags:"))
    promote_journey(Pack.load(pack_dir), "fresh")


def test_init_packs_take_proposals_and_import_lists_what_it_cannot_carry(pack_dir, tmp_path, capsys):
    from journeys.detect import init_pack
    from journeys.heal import apply
    # a pack made by init takes promoted proposals without breaking its YAML
    new = tmp_path / "newpack"
    init_pack(new, None, "pk", "com.example.app")
    (new / "map" / "screens.yaml").write_text("screens:\n  A:\n    match: {all: [{text: A}]}\n")
    (new / "map" / "edges.yaml").write_text("edges: []\n")  # a hand-written empty list
    apply(Pack.load(new), {"kind": "new-screen", "name": "B", "match": {"all": [{"text": "B"}]},
                           "edges": [{"from": "A", "to": "B", "do": [{"tapOn": "Go"}]}]})  # an old-style proposal
    p = Pack.load(new)
    assert p.errors == [] and p.map.edges[0].do == [{"tap": "Go"}]
    with pytest.raises(ValueError, match="not an Android package"):
        init_pack(tmp_path / "bad", None, "x", "x y")
    # import lists what it cannot carry over, and survives files it cannot read
    fl = tmp_path / "fl"
    fl.mkdir()
    (fl / "vars.yaml").write_text("appId: com.android.settings\n---\n- tapOn: \"${NAME}\"\n- inputText: ${X}\n"
                                  "- tapOn: {id: \"btn_.*\"}\n- inputText:\n- tapOn: \"{{who}}\"\n- tapOn: Ok\n")
    (fl / "latin1.yaml").write_bytes(b"appId: a\n---\n- tapOn: caf\xe9\n")
    assert cli(pack_dir, "import", "maestro", str(fl), str(tmp_path / "missing.yaml")) == 0
    out = capsys.readouterr().out
    assert out.count("Maestro variable") == 3 and "is a pattern" in out and "not a plain text" in out
    assert "SKIP" in out and "latin1.yaml" in out and "missing.yaml" in out
    assert yaml.safe_load((pack_dir / "drafts" / "vars.yaml").read_text())["steps"] == [{"do": [{"tap": "Ok"}]}]
    # validate reports odd step values instead of crashing
    (pack_dir / "drafts" / "odd.yaml").write_text(
        "name: odd\nsteps:\n  - expect: [a]\n  - hook: 5\n  - input:\n  - do: [{stop: 1}]\n  - see: \"//\"\n"
        "  - do: [{type: [1]}]\n  - input: 1234\n")
    assert cli(pack_dir, "validate") == 1
    out = capsys.readouterr().out
    for want in ("expect takes one text", "hook takes a name or a map", "input takes one text",
                 "stop takes no value", "empty regex", "type takes a non-empty text"):
        assert want in out, want
    assert "odd.yaml:9" not in out  # a number is fine as input


def test_validate_reports_bad_start_kinds_screens_and_every_step_error(pack_dir, capsys):
    sf = pack_dir / "map" / "screens.yaml"
    sf.write_text(sf.read_text() + "  ODD:\n    match: {all: [{kind: Foo}]}\n  F: bad\n")
    d = pack_dir / "drafts"
    d.mkdir()
    (d / "s.yaml").write_text("name: s\nstart: foo\nsteps:\n  - tapOn: X\n  - do: [{erase: true}]\n")
    assert cli(pack_dir, "validate") == 1
    out = capsys.readouterr().out
    for want in ("start is a map of launch and clear_state", "kind is one of", "a screen is a map",
                 "s.yaml:4: a step is one of", "s.yaml:5: erase takes a number"):
        assert want in out, want


def test_not_see_fails_when_the_app_is_not_on_screen(pack_dir, capsys):
    d = pack_dir / "drafts"
    d.mkdir()
    (d / "gone.yaml").write_text("name: gone\nsteps:\n  - expect: SETTINGS-HOME\n  - do: [stop]\n  - not_see: Display\n")
    assert cli(pack_dir, "run", "gone", "--draft", "--driver", "fake") == 1
    assert "is not on screen" in capsys.readouterr().out


def test_reserved_names_appid_note_export_note_and_no_empty_run(pack_dir, tmp_path, capsys):
    from journeys.actions import to_selector
    assert to_selector("/@3/") == r"\@3"
    assert cli(pack_dir, "new", "all") == 2 and cli(pack_dir, "new", "smoke") == 2
    fl = tmp_path / "fl"
    fl.mkdir()
    (fl / "all.yaml").write_text("appId: com.other.app\n---\n- tapOn: Ok\n")
    assert cli(pack_dir, "import", "maestro", str(fl)) == 0
    out = capsys.readouterr().out
    assert "all-flow.yaml" in out and "the flow is for com.other.app" in out
    assert cli(pack_dir, "export", "maestro", "search-settings") == 0
    assert "uses ${NAME}" in capsys.readouterr().err
    app = tmp_path / "other.yaml"
    app.write_text("app: com.other\nstart: A\nscreens: {A: {elements: []}}\n")
    assert cli(pack_dir, "run", "smoke", "--driver", "fake", "--fake-app", str(app)) == 2
    assert not list((pack_dir / "s").glob("runs/*"))  # a refused start leaves no run behind


def test_init_export_import_and_shapes_are_strict(pack_dir, tmp_path, capsys):
    from journeys.detect import init_pack
    from journeys.formats.maestro import import_flow
    old = tmp_path / "np"
    (old / "map").mkdir(parents=True)
    (old / "map" / "screens.yaml").write_text("screens:\n  A: {match: {all: [A]}}\n")
    with pytest.raises(FileExistsError):
        init_pack(old, None, "np", "com.example.app")
    assert "A:" in (old / "map" / "screens.yaml").read_text() and not (old / "pack.yaml").exists()
    d = pack_dir / "drafts"
    d.mkdir()
    (d / "badact.yaml").write_text("name: badact\nsteps:\n  - do: [{tap: {text: \"\"}}]\n")
    assert cli(pack_dir, "export", "maestro", "badact") == 2
    (d / "badhook.yaml").write_text("name: badhook\nsteps:\n  - see: Hi\n  - hook: nothere\n")
    assert cli(pack_dir, "export", "maestro", "badhook") == 2  # validate errors stop an export
    f = tmp_path / "f.yaml"
    f.write_text("appId: com.android.settings\n---\n- tapOn: {text: a, index: x}\n- tapOn: {text: a, index: -1}\n"
                 "- eraseText: -5\n- eraseText: 0\n- launchApp: {clearState: 'false'}\n- assertVisible: {id: null}\n"
                 "- tapOn: Ok\n")
    draft, notes = import_flow(f, "com.android.settings")
    assert draft["steps"] == [{"do": [{"tap": "Ok"}]}] and len(notes) == 6, notes
    (d / "shapes.yaml").write_text("name: shapes\nstart: {clear_state: \"no\"}\nsteps: 5\n")
    (d / "acts.yaml").write_text("name: acts\nsteps:\n  - do: [{launch: {clear_state: \"no\"}}]\n"
                                 "  - do: [{tap: {id: null}}]\n  - do: [{scroll_to: {element: x, foo: 1}}]\n")
    ef = pack_dir / "map" / "edges.yaml"
    ef.write_text(ef.read_text() + "  - from: [a]\n    to: B\n    do: [back]\n")
    (pack_dir / "sets.yaml").write_text("sets: [1]\n")
    pf = pack_dir / "pack.yaml"
    pf.write_text(pf.read_text() + "devices: 5\n")
    assert cli(pack_dir, "validate") == 1
    out = capsys.readouterr().out
    for want in ("steps is a list", "(true or false)", "id is a non-empty text", "scroll_to takes element",
                 "from and to are screen names", "sets is a map", "devices is a map"):
        assert want in out, want

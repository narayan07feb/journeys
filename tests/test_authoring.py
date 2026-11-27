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
    assert {"assertVisible": {"text": "(?-ims)(?:Network & internet)[\\s\\S]*"}} in cmds  # the rule's ^: no padding  # expect SETTINGS-HOME
    assert {"tapOn": "(?-ims)Display.*"} in cmds                            # goto via the map's edges
    assert "back" not in cmds  # the last goto follows a `do`: the screen is unknown, so it is left out
    assert {"scrollUntilVisible": {"element": {"text": "(?-ims)Dark theme.*"}, "direction": "DOWN"}} in cmds
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
    assert [n for n in notes if "move it to env:" not in n] == []  # only the typed-text reminder
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
    assert "write `see: ...`" in out and "non-empty text" in out and "True is not a text" in out
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


def test_journeys_differing_only_in_after_are_not_duplicates(pack_dir):
    from journeys.authoring import promote_journey
    d = pack_dir / "drafts"
    d.mkdir()
    (d / "clean.yaml").write_text((pack_dir / "journeys" / "display.yaml").read_text()
                                  .replace("open-display", "clean").replace("tags:", "after: [note]\ntags:"))
    promote_journey(Pack.load(pack_dir), "clean")


def test_init_packs_take_proposals_and_import_lists_what_it_cannot_carry(pack_dir, tmp_path, capsys):
    from journeys.detect import init_pack
    from journeys.heal import apply
    # a pack made by init takes promoted proposals without breaking its YAML
    new = tmp_path / "newpack"
    init_pack(new, None, "pk", "com.example.app", "maestro")
    (new / "map" / "screens.yaml").write_text("screens:\n  A:\n    match: {all: [{text: A}]}\n")
    (new / "map" / "edges.yaml").write_text("edges: []\n")  # a hand-written empty list
    apply(Pack.load(new), {"kind": "new-screen", "name": "B", "match": {"all": [{"text": "B"}]},
                           "edges": [{"from": "A", "to": "B", "do": [{"tapOn": "Go"}]}]})  # an old-style proposal
    p = Pack.load(new)
    assert p.errors == [] and p.map.edges[0].do == [{"tap": "Go"}]
    with pytest.raises(ValueError, match="not an Android package"):
        init_pack(tmp_path / "bad", None, "x", "x y", "maestro")
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
    assert "odd.yaml:9: input takes one text, not 1234; quote it" in out  # YAML made it a number


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
        init_pack(old, None, "np", "com.example.app", "maestro")
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
    assert draft["steps"] == [{"do": [{"tap": "Ok"}]}] and len(notes) == 7, notes
    assert notes.count("f.yaml: Maestro matched this flow's texts and ids without case; the draft matches them "
                       "with case: check them") == 1, notes  # one case note per flow, not one per selector
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


def test_look_hints_tags_maestro_parent_and_export_never_overwrites(pack_dir, tmp_path, capsys):
    assert cli(pack_dir, "look", "--driver", "fake") == 0
    out = capsys.readouterr().out
    assert "map screen:" in out
    d = pack_dir / "drafts"
    d.mkdir()
    (d / "h.yaml").write_text("name: h\ntags: 5\nsteps:\n  - stopApp: x\n")
    assert cli(pack_dir, "validate") == 1
    out = capsys.readouterr().out
    assert "write `do: [stop]`" in out and "tags is a list" in out
    from journeys.detect import scan
    proj = tmp_path / ".maestro" / "p2"
    proj.mkdir(parents=True)
    (proj / "a.yaml").write_text("x: 1\n")
    assert scan(proj) == ([], None)
    (d / "h.yaml").unlink()
    out_file = tmp_path / "flow.yaml"
    out_file.write_text("the team's own flow\n")
    assert cli(pack_dir, "export", "maestro", "open-display", "-o", str(out_file)) == 2
    assert out_file.read_text() == "the team's own flow\n"


def test_names_variables_empty_do_and_import_header(pack_dir, tmp_path, capsys):
    d = pack_dir / "drafts"
    d.mkdir()
    (d / "a.yaml").write_text("name: all\nsteps:\n  - see: Hi\n")
    (d / "b.yaml").write_text("name: smoke\nsteps:\n  - see: Hi\n")
    (d / "c.yaml").write_text("name: b 7\nsteps:\n  - input: \"${NOPE}\"\n  - do: []\n  - do: [{type: \"${main.user}\"}]\n")
    (d / "e.yaml").write_text("name: e\nsteps: [1, 2]\n")
    assert cli(pack_dir, "validate") == 1
    out = capsys.readouterr().out
    assert out.count("journey name") == 3 and "${NOPE} is not declared" in out
    assert "do needs at least one action" in out and "${main.user} is not declared" in out
    assert "step 1:" in out and "step 2:" in out
    from journeys.formats.maestro import import_flow
    f = tmp_path / "f.yaml"
    f.write_text("appId: com.android.settings\nonFlowStart: [{runFlow: login.yaml}]\nenv: {USER: bob}\n---\n"
                 "- launchApp: com.android.settings\n- tapOn: {id: \"com.android.settings:id/search_action_bar\"}\n"
                 "- stopApp: com.android.settings\n- stopApp: {clearState: true}\n")
    draft, notes = import_flow(f, "com.android.settings")
    assert draft["start"]["launch"] and draft["steps"] == [
        {"do": [{"tap": {"id": "com.android.settings:id/search_action_bar"}}, "stop"]}]
    assert any("header onFlowStart" in n for n in notes) and any("header env" in n for n in notes)
    assert any("command 4" in n and "clearState" not in n.split(":", 1)[1].split("only")[1] for n in notes)
    from journeys.actions import ActionError
    from journeys.formats.maestro import export
    (pack_dir / "journeys" / "k.yaml").write_text("name: k\nsteps:\n  - do: [{press: search}]\n")
    with pytest.raises(ActionError, match="Maestro has no Search key"):  # cli-2.12.1 KeyCode has no Search
        export(Pack.load(pack_dir), Pack.load(pack_dir).journeys["k"])
    (pack_dir / "journeys" / "k.yaml").write_text("name: k\nsteps:\n  - do: [{press: tab}]\n")
    flow, notes = export(Pack.load(pack_dir), Pack.load(pack_dir).journeys["k"])
    assert "pressKey: Tab" in flow and not notes  # Tab is a Maestro key


def test_edge_variables_get_their_value(pack_dir, monkeypatch):
    from journeys.drivers.fake import FakeDriver
    from journeys.results import Store
    from journeys.runner import Runner
    ef = pack_dir / "map" / "edges.yaml"
    ef.write_text(ef.read_text().replace('do: [{tap: "Search settings"}]',
                                         'do: [{tap: "Search settings"}, {type: "${DEMO_SEARCH}"}]'))
    monkeypatch.setenv("DEMO_SEARCH", "wifi")
    pack = Pack.load(pack_dir)
    assert pack.errors == []
    driver = FakeDriver.from_file(pack_dir / "fake" / "app.yaml")
    store = Store(pack_dir / "s")
    Runner(pack, driver, store, store.new_run(), variables=pack.variables()).run_all([pack.journeys["search-settings"]])
    assert {"inputText": "wifi"} in driver.log and {"inputText": "${DEMO_SEARCH}"} not in driver.log


def test_odd_pack_yaml_shapes_are_errors_not_crashes(pack_dir, capsys):
    pf = pack_dir / "pack.yaml"
    for bad in ("accounts: [a]", "accounts: {main: hello}", "hooks: [a]", "events: hello", "tap_all: hello",
                "app_packages: x"):
        pf.write_text("name: x\napp: com.android.settings\n" + bad + "\n")
        assert cli(pack_dir, "validate") == 1, bad
        assert "FAIL" in capsys.readouterr().out
    (pack_dir / "map" / "screens.yaml").write_text("screens: [a]\n")
    (pack_dir / "sets.yaml").write_text("sets:\n  all: {tags: [smoke]}\n")
    d = pack_dir / "drafts"
    d.mkdir()
    (d / "h.yaml").write_text("name: h\nsteps:\n  - hook: {name: [x]}\n")
    assert cli(pack_dir, "validate") == 1
    out = capsys.readouterr().out
    assert "screens is a map" in out and "`all` is every journey" in out and "a hook is a name or {name: ..., args: {...}}, not {'name': ['x']}" in out


def test_draft_hint_list_drafts_and_odd_selectors(pack_dir, capsys):
    from journeys.actions import from_selector, to_commands
    from journeys.drivers.base import Element, Screen, find
    from journeys.heal import selector
    d = pack_dir / "drafts"
    d.mkdir()
    (d / "x.yaml").write_text("name: x\nsteps:\n  - see: Hi\n")
    assert cli(pack_dir, "run", "x", "--driver", "fake") == 2 and "add --draft" in capsys.readouterr().err
    assert cli(pack_dir, "list") == 0 and "draft x" in capsys.readouterr().out
    assert from_selector(r"//") == "////" and to_commands([{"tap": "////"}]) == [{"tapOn": "//"}]
    s = Screen([Element("@1", "Button", "…"), Element("@2", "Button", ", x")])
    assert selector(s.elements[0], s) == "…" and selector(s.elements[1], s) == ", x"  # never ".*"
    assert find(s.elements, {}) is None and find(s.elements, {"point": "5,5"}) is None
    with pytest.raises(Exception, match="1 to 1000"):
        to_commands([{"erase": 5000}])


def test_app_packages_count_as_the_app_for_not_see_and_tap_all(pack_dir):
    from journeys.drivers.fake import FakeDriver
    from journeys.heal import left_app
    from journeys.results import Store
    from journeys.runner import Runner
    app = yaml.safe_load((pack_dir / "fake" / "app.yaml").read_text())
    app["screens"]["SEARCH"]["package"] = "com.android.settings.intelligence"
    driver = FakeDriver(app)
    pack = Pack.load(pack_dir)
    store = Store(pack_dir / "s")
    out = Runner(pack, driver, store, store.new_run(), variables={"DEMO_SEARCH": "wifi"}).run_all(
        [pack.journeys["search-settings"]])
    assert out[0].status == "passed", out[0].message
    assert left_app(driver, pack.owns) is None and left_app(driver, pack.app) == "com.android.settings.intelligence"


def test_broken_pack_files_are_fail_lines(pack_dir, capsys):
    (pack_dir / "map" / "screens.yaml").write_text("screens: [\n")
    (pack_dir / "map" / "edges.yaml").write_text("- a\n")
    (pack_dir / "sets.yaml").write_text("sets: {a: [\n")
    assert cli(pack_dir, "validate") == 1
    out = capsys.readouterr().out
    assert "screens.yaml:2: not valid YAML" in out and "edges.yaml:1: the file is a map" in out
    assert "sets.yaml" in out and "not valid YAML" in out
    pf = pack_dir / "pack.yaml"
    keep = pf.read_text()
    pf.write_text("name: x\napp: [\n")
    assert cli(pack_dir, "validate") == 1 and "FAIL" in (o := capsys.readouterr().out) and "not valid YAML" in o
    for bad in ("events: {check: 5}", "events: {check: hooks/x.sh}", "hooks: {h: hooks/h.sh}"):
        pf.write_text(keep + bad + "\n")
        assert cli(pack_dir, "validate") == 1 and "command list" in capsys.readouterr().out, bad
    pf.write_text(keep.replace("app: com.android.settings", "app: \"not a package!\""))
    assert cli(pack_dir, "validate") == 1 and "FAIL" in (o := capsys.readouterr().out) and "Android package name" in o


def test_deny_list_stems_and_dashes_and_two_key_steps(pack_dir, capsys):
    from journeys.drivers.base import Element
    from journeys.pack import DEFAULT_DENY
    for text in ("Wi‑Fi", "Wi–Fi", "Payments", "Subscriptions", "Force stop", "Disconnect", "Deleted items"):
        assert any(r.matches(Element("@1", "Button", text)) for r in DEFAULT_DENY), text
    for text in ("Display", "Dark theme", "About phone", "Calendar"):
        assert not any(r.matches(Element("@1", "Button", text)) for r in DEFAULT_DENY), text
    d = pack_dir / "drafts"
    d.mkdir()
    (d / "two.yaml").write_text("name: two\nsteps:\n  - {expect: SETTINGS-HOME, see: foo}\n")
    assert cli(pack_dir, "validate") == 1 and "one step, one key" in capsys.readouterr().out


def test_rule_lists_sets_and_fake_app_are_checked(pack_dir, capsys):
    sf = pack_dir / "map" / "screens.yaml"
    sf.write_text(sf.read_text() + "  ODD1:\n    match: {all: Hi}\n  ODD2:\n    match: {all: {text: Hi}}\n")
    pf = pack_dir / "pack.yaml"
    pf.write_text(pf.read_text().replace("  allowed: false", "  allowed: false\n  deny: delete"))
    (pack_dir / "sets.yaml").write_text("sets:\n  s: {journeys: [[j]]}\n  t: {journeys: open-display}\n")
    fa = pack_dir / "fake" / "app.yaml"
    fa.write_text(fa.read_text().replace("start: HOME", "start: NOPE"))
    assert cli(pack_dir, "validate") == 1
    out = capsys.readouterr().out
    assert out.count("match.all is a list") == 2 and "tap_all.deny is a list" in out
    assert out.count("journeys: [names]") == 2 and "start names a screen" in out
    for bad in ("a: [\n", "- x\n"):
        fa.write_text(bad)
        assert cli(pack_dir, "validate") == 1
        assert "app.yaml" in capsys.readouterr().out


def test_failure_options_and_secrets_in_messages(pack_dir, monkeypatch):
    from journeys.drivers.fake import FakeDriver
    from journeys.results import Store
    from journeys.runner import Runner
    pf = pack_dir / "pack.yaml"
    pf.write_text(pf.read_text().replace("allowed: false", "allowed: true"))
    d = pack_dir / "drafts"
    d.mkdir()
    (d / "sec.yaml").write_text("name: sec\nsteps:\n  - expect: SETTINGS-HOME\n  - see: \"${DEMO_SEARCH}\"\n")
    pack = Pack.load(pack_dir)
    store = Store(pack_dir / "s")
    out = Runner(pack, FakeDriver.from_file(pack_dir / "fake" / "app.yaml"), store, store.new_run(),
                 variables={"DEMO_SEARCH": "hunter2.x"}).run_all([pack.drafts["sec"]])
    assert out[0].status == "failed" and "hunter2" not in out[0].message and "•••" in out[0].message
    f = store.failure(out[0].failure)
    assert "hunter2" not in f["message"]
    assert all("tapOn" not in str(o["tap"]) for o in f.get("options") or [])  # neutral words, copy-ready
    assert any(isinstance(o["tap"], dict) and "tap" in o["tap"] for o in f["options"])


def test_small_round_10_fixes(pack_dir, tmp_path, capsys):
    from journeys.detect import init_pack, scan
    from journeys.drivers.base import Element
    from journeys.pack import DEFAULT_DENY
    for text in ("Sign-out", "Log off", "Clear all data", "Unlink", "Close account", "Block", "Revoke",
                 "Forget network", "Reboot", "Power off", "Turn off"):
        assert any(r.matches(Element("@1", "Button", text)) for r in DEFAULT_DENY), text
    assert cli(pack_dir, "tap-all", "--driver", "fake") == 2 and not list((pack_dir / "s").glob("runs/*"))
    assert cli(pack_dir, "export", "maestro", "open-display", "-o", str(tmp_path / "no" / "dir.yaml")) == 2
    assert "cannot write" in capsys.readouterr().err
    proj = tmp_path / "proj"
    (proj / "app").mkdir(parents=True)
    (proj / "app" / "build.gradle").write_text('// applicationId "com.old.thing"\napplicationId "com.new.thing"\n')
    assert scan(proj)[1] == "com.new.thing"
    with pytest.raises(ValueError) as e:
        init_pack(tmp_path / "x", None, "x", None)
    assert "Gradle" not in str(e.value)
    pf = pack_dir / "pack.yaml"
    pf.write_text(pf.read_text() + "app_packages: [\"\"]\n")
    assert cli(pack_dir, "validate") == 1 and "app_packages is a list of package names" in capsys.readouterr().out


def test_yaml_values_that_are_not_text_must_be_quoted(pack_dir, capsys):
    d = pack_dir / "drafts"
    d.mkdir()
    (d / "q.yaml").write_text("name: q\nsteps:\n  - do: [{tap: 12:30}]\n  - do: [{type: 0123}]\n  - see: 1_000\n"
                              "  - do: [{tap: Yes}]\n  - input: 0123\n  - see: \"12:30\"\n  - do: [{type: \"0123\"}]\n")
    assert cli(pack_dir, "validate") == 1
    out = capsys.readouterr().out
    assert out.count("quote it") == 5 and "q.yaml:8" not in out and "q.yaml:9" not in out


def test_tap_all_fails_when_the_app_does_not_start(pack_dir, tmp_path, capsys):
    pf = pack_dir / "pack.yaml"
    pf.write_text(pf.read_text().replace("allowed: false", "allowed: true"))
    app = tmp_path / "app.yaml"
    app.write_text("app: com.android.settings\nstart: A\nscreens: {A: {elements: [{kind: Button, text: Go, to: CRASH}]}}\n")
    from journeys.drivers.fake import FakeDriver
    from journeys.explore import tap_all
    from journeys.results import Store
    driver = FakeDriver.from_file(app)
    driver.do = lambda cmds: __import__("journeys.drivers.base", fromlist=["x"]).CommandResult(False, "no launcher activity")
    store = Store(tmp_path / "s")
    run = store.new_run()
    with pytest.raises(Exception, match="did not start: no launcher activity"):
        tap_all(Pack.load(pack_dir), driver, store, run)
    assert run.data["status"] == "failed"


def test_round_11_small_fixes(pack_dir, tmp_path, capsys, monkeypatch):
    from journeys.drivers.base import Element
    from journeys.drivers.fake import FakeDriver
    from journeys.pack import DEFAULT_DENY
    from journeys.results import Store
    from journeys.runner import Runner
    for text in ("Clear history", "Clear all", "Place order", "Order now", "Transfer", "Withdraw", "Donate",
                 "Discard", "Empty trash", "Format SD card", "Top up", "Add money"):
        assert any(r.matches(Element("@1", "Button", text)) for r in DEFAULT_DENY), text
    fa = pack_dir / "fake" / "app.yaml"
    keep = fa.read_text()
    for bad in (keep.replace("start: HOME", "start: [HOME]"), keep.replace("to: DISPLAY", "to: [x]", 1)):
        fa.write_text(bad)
        assert cli(pack_dir, "validate") == 1
        capsys.readouterr()
    fa.write_text(keep)
    pf = pack_dir / "pack.yaml"
    pf.write_text(pf.read_text() + "build: [\"./gradlew assemble\"]\n")
    assert cli(pack_dir, "validate") == 1 and "command lists" in capsys.readouterr().out
    pf.write_text(pf.read_text().replace("build: [\"./gradlew assemble\"]\n", ""))
    # the typed secret is masked in the failure listing too, and messages use pack words
    d = pack_dir / "drafts"
    d.mkdir()
    (d / "m.yaml").write_text("name: m\nsteps:\n  - goto: SETTINGS-SEARCH\n  - input: \"${DEMO_SEARCH}\"\n"
                              "  - see: \"Wi-Fi (2)\"\n")
    app = yaml.safe_load(keep)
    driver = FakeDriver(app)
    real = driver._one

    def typed(cmd):  # the field shows what was typed, like a real phone
        if isinstance(cmd, dict) and "inputText" in cmd:
            driver.app["screens"][driver.stack[-1]]["elements"][0]["text"] = cmd["inputText"]
        return real(cmd)
    driver._one = typed
    pack = Pack.load(pack_dir)
    store = Store(tmp_path / "st")
    out = Runner(pack, driver, store, store.new_run(), variables={"DEMO_SEARCH": "hunter22secret"}).run_all(
        [pack.drafts["m"]])
    f = store.failure(out[0].failure)
    assert "hunter22secret" not in json_dump(f) and "Wi-Fi (2)" in f["message"] and "\\(" not in f["message"]


def json_dump(obj):
    import json
    return json.dumps(obj)


def test_deny_list_catches_spaces_and_dashes_of_any_kind_and_more_stems():
    from journeys.drivers.base import Element
    from journeys.pack import DEFAULT_DENY
    for text in ("Log out", "Sign‑out", "Clear data", "Force stop", "Turn off", "Wi Fi",
                 "Top-up", "Unsubscribe", "Account deletion", "Remove device"):
        assert any(r.matches(Element("@1", "Button", text)) for r in DEFAULT_DENY), text
    for text in ("Display", "Profile", "Sound", "Battery"):
        assert not any(r.matches(Element("@1", "Button", text)) for r in DEFAULT_DENY), text


def test_a_broken_tap_all_block_stops_tap_all_and_look_only_warns(tappable_pack_dir, capsys):
    f = tappable_pack_dir / "pack.yaml"
    keep = f.read_text()
    f.write_text(keep + "  deny: [{text: \"/(?i)nuke/\", kind: Btn}]\n")
    assert cli(tappable_pack_dir, "tap-all", "--driver", "fake") == 2
    assert "kind is one of" in capsys.readouterr().err + capsys.readouterr().out
    assert not list((tappable_pack_dir / "s").glob("runs/*"))
    capsys.readouterr()
    assert cli(tappable_pack_dir, "look", "--driver", "fake") == 0  # look taps nothing: it warns, and helps fix
    err = capsys.readouterr().err
    assert "WARN" in err and "kind is one of" in err
    for bad in ("tap_all: false\n", "tap_all:\n  allowed: \"false\"\n"):
        f.write_text(keep.split("tap_all:")[0] + bad)
        pack = Pack.load(tappable_pack_dir)
        assert pack.tap_allowed is False and any("tap_all" in e for e in pack.errors), bad


def test_tap_all_fails_when_a_screen_cannot_be_reached_again(tappable_pack_dir, monkeypatch):
    import journeys.explore
    from journeys.explore import TapAllReport
    monkeypatch.setattr(journeys.explore, "tap_all", lambda *a, **k: TapAllReport(unreachable=["SETTINGS-ABOUT"]))
    assert cli(tappable_pack_dir, "tap-all", "--driver", "fake") == 1


def test_fake_app_element_fields_are_checked(tappable_pack_dir, capsys):
    f = tappable_pack_dir / "fake" / "app.yaml"
    app = yaml.safe_load(f.read_text())
    app["screens"][app["start"]]["elements"].append({"kind": "Text", "text": 12})
    f.write_text(yaml.safe_dump(app))
    assert cli(tappable_pack_dir, "validate") == 1 and "quoted text" in capsys.readouterr().out


def test_mask_hides_the_raw_and_the_regex_escaped_secret():
    from journeys.results import mask, set_secrets
    set_secrets(["p@ss.w(rd"])
    try:
        assert mask({"m": ["typed p@ss.w(rd", "no /p@ss\\.w\\(rd/ found"]}) == {"m": ["typed •••", "no /•••/ found"]}
    finally:
        set_secrets([])


def test_rule_values_must_be_text_and_string_rules_must_compile(tappable_pack_dir, capsys):
    f = tappable_pack_dir / "pack.yaml"
    keep = f.read_text()
    for deny, said in (("[{text: Yes}]", "rule values are text"), ("[{text: 0123}]", "rule values are text"),
                       ('["/[/"]', "unterminated")):
        f.write_text(keep + f"  deny: {deny}\n")
        assert cli(tappable_pack_dir, "validate") == 1 and said in capsys.readouterr().out, deny
        assert cli(tappable_pack_dir, "tap-all", "--driver", "fake") == 2, deny  # refused, no traceback
    f.write_text(keep + '  deny: [{text: "Yes"}]\n')
    assert cli(tappable_pack_dir, "validate") == 0


def test_device_settings_are_checked(tappable_pack_dir, capsys):
    f = tappable_pack_dir / "pack.yaml"
    keep = f.read_text()
    for bad in ("allow_settings_changes: emulator", "allow_settings_changes: true"), \
               ("animations: normal", "animations: 5"):
        f.write_text(keep.replace(*bad))
        assert cli(tappable_pack_dir, "validate") == 1 and "devices." in capsys.readouterr().out, bad


def test_tap_all_masks_env_values_and_a_refused_dka_leaves_no_run(tappable_pack_dir, monkeypatch):
    f = tappable_pack_dir / "fake" / "app.yaml"
    app = yaml.safe_load(f.read_text())
    app["screens"][app["start"]]["elements"].append({"kind": "Button", "text": "Hello hunter22", "to": "CRASH"})
    f.write_text(yaml.safe_dump(app))
    monkeypatch.setenv("DEMO_SEARCH", "hunter22")
    assert cli(tappable_pack_dir, "tap-all", "--driver", "fake") == 1  # the crash
    state = tappable_pack_dir / "s"
    written = "".join(p.read_text() for p in state.rglob("*.json"))
    assert "Hello" in written and "hunter22" not in written
    runs = len(list(state.rglob("run.json")))
    p = tappable_pack_dir / "pack.yaml"
    p.write_text(p.read_text().replace("allow_settings_changes: emulator", "allow_settings_changes: []"))
    assert cli(tappable_pack_dir, "tap-all", "--driver", "fake", "--dka") == 2
    assert len(list(state.rglob("run.json"))) == runs


def test_a_short_common_secret_does_not_break_statuses(pack_dir, monkeypatch, capsys):
    monkeypatch.setenv("DEMO_SEARCH", "pass")
    assert cli(pack_dir, "run", "all", "--driver", "fake") == 0
    (run,) = (pack_dir / "s").rglob("run.json")
    data = yaml.safe_load(run.read_text())
    assert data["status"] == "passed" and all(j["status"] == "passed" for j in data["journeys"])
    capsys.readouterr()
    assert cli(pack_dir, "history") == 0 and "passed" in capsys.readouterr().out


def test_tap_limits_below_1_are_refused_and_a_pack_needs_a_driver(tappable_pack_dir, capsys):
    for flag in ("--max-taps", "--max-screens"):
        with pytest.raises(SystemExit):
            cli(tappable_pack_dir, "tap-all", "--driver", "fake", flag, "0")
    capsys.readouterr()  # the example pack names no driver: validate warns, device commands ask
    assert cli(tappable_pack_dir, "validate") == 0 and "WARN" in capsys.readouterr().out
    assert cli(tappable_pack_dir, "tap-all") == 2 and "Which tool does your team use" in capsys.readouterr().err
    assert cli(tappable_pack_dir, "run", "all") == 2 and not (tappable_pack_dir / "s").exists()  # asks, never guesses
    assert cli(tappable_pack_dir, "tap-all", "--driver", "fake") == 0  # the answer given on the command line


def test_import_warns_that_typed_text_may_be_a_secret(tmp_path):
    from journeys.formats.maestro import import_flow
    f = tmp_path / "login.yaml"
    f.write_text("appId: com.x.y\n---\n- launchApp\n- inputText: hunter2\n")
    draft, notes = import_flow(f)
    assert any("move it to env:" in n for n in notes) and not any("hunter2" in n for n in notes)


def test_an_exported_flow_imports_back_without_the_case_flag(pack_dir, tmp_path):
    from journeys.formats.maestro import export, import_flow
    pack = Pack.load(pack_dir)
    flow, _ = export(pack, pack.journeys["open-display"])
    assert "(?-ims)" in flow
    (tmp_path / "f.yaml").write_text(flow)
    draft, _ = import_flow(tmp_path / "f.yaml", pack.app)
    assert "(?-ims)" not in str(draft) and "Dark theme.*" in str(draft)


@pytest.mark.parametrize("rx", ["a{b", "(?P<n>a)b", "x(?P=n)", "\\\\{", "(?#c)a", "(a)?(?(1)b|c)", "a{2[x]}"])
@pytest.mark.parametrize("shape", ["tap", "see", "scroll"])
def test_a_selector_java_cannot_read_is_refused_for_maestro(rx, shape):
    from journeys.actions import ActionError
    from journeys.drivers.maestro import to_maestro
    cmd = {"tap": {"tapOn": rx}, "see": {"assertVisible": {"text": rx}},
           "scroll": {"scrollUntilVisible": {"element": rx}}}[shape]
    with pytest.raises(ActionError, match="pattern subset"):  # java.util.regex (Java 17) throws or reads each differently
        to_maestro(cmd)


@pytest.mark.parametrize("rx", ["a{2}", "a{2,}", "a{2,3}", "\\{x\\}", "[{]x", "[^{]x", "a\\(?P", "x}"])
def test_a_selector_java_reads_goes_to_maestro_unchanged(rx):
    from journeys.drivers.maestro import SAME, to_maestro
    assert to_maestro({"tapOn": {"text": rx}}) == {"tapOn": {"text": SAME + rx}}


@pytest.mark.parametrize("want, sent", [("next", "next"), ("com.x:id/next", "com\\.x:id/next"), ("a.b", "a\\.b"),
                                        ("com.x:id/a{b", "com\\.x:id/a\\{b"),
                                        ("(?P<n>a)", "\\(\\?P<n>a\\)")])
def test_an_id_goes_to_maestro_as_plain_text(want, sent):
    """Maestro matches an id pattern against the whole resource-id and the part after its last / (Filters.idMatches,
    cli-2.12.1): a bare name goes alone (any package), pkg:id/name goes whole (that package only), escaped."""
    from journeys.drivers.maestro import SAME, to_maestro
    assert to_maestro({"tapOn": {"id": want}}) == {"tapOn": {"id": SAME + sent}}
    assert to_maestro({"scrollUntilVisible": {"element": {"id": want}}})["scrollUntilVisible"]["element"] == \
        {"id": SAME + sent}


@pytest.mark.parametrize("want, says", [("com.x:id/", "Maestro can match only"), ("com.x:id/a/b", "Maestro can match only"),
                                        ("x/", "Maestro can match only"), ("x/y", "Maestro can match only"),
                                        (":id/next", "Maestro can match only"), ("p/q:id/next", "Maestro can match only"),
                                        ("${ID}", "variable's value as a pattern"),
                                        ("com.x:id/${ID}", "variable's value as a pattern")])
def test_an_id_maestro_cannot_match_as_journeys_does_is_refused(want, says):
    from journeys.actions import ActionError
    from journeys.drivers.maestro import to_maestro
    with pytest.raises(ActionError, match=says):
        to_maestro({"tapOn": {"id": want}})


def test_an_exported_id_imports_back_as_its_name_and_an_older_export_still_imports(pack_dir, tmp_path):
    from journeys.formats.maestro import export, import_flow
    (pack_dir / "journeys" / "ids.yaml").write_text(
        "name: ids\nsteps:\n  - do: [{tap: {id: \"com.x:id/a.b\"}}, {tap: {id: \"a{b\"}}, {tap: {id: \"a\\\\b\"}}]\n")
    pack = Pack.load(pack_dir)
    flow, notes = export(pack, pack.journeys["ids"])
    assert "id: (?-ims)com\\.x:id/a\\.b" in flow and notes == []
    f = tmp_path / "ids.yaml"
    f.write_text(flow)
    draft, notes = import_flow(f)
    assert draft["steps"] == [{"do": [{"tap": {"id": "com.x:id/a.b"}}, {"tap": {"id": "a{b"}}, {"tap": {"id": "a\\b"}}]}]
    assert not [n for n in notes if "id" in n]
    f.write_text("appId: com.android.settings\n---\n- tapOn: {id: '(?-ims)com.x:id/next'}\n")  # before this change
    assert import_flow(f, "com.android.settings")[0]["steps"] == [{"do": [{"tap": {"id": "com.x:id/next"}}]}]


def test_an_id_flow_that_matches_other_views_than_its_text_is_refused_or_noted(pack_dir, tmp_path):
    from journeys.formats.maestro import export, import_flow
    f = tmp_path / "f.yaml"
    f.write_text("appId: com.android.settings\n---\n- tapOn: {id: '(?-ims)x/a\\.b'}\n")  # only the id x/a.b
    assert any("is a pattern; Journeys ids match exactly; left out" in n for n in import_flow(f, "com.android.settings")[1])
    f.write_text("appId: com.android.settings\n---\n- tapOn: {id: '(?-ims)a.b'}\n")  # also axb in Maestro
    draft, notes = import_flow(f, "com.android.settings")
    assert draft["steps"] == [{"do": [{"tap": {"id": "a.b"}}]}] and any("read it as a pattern" in n for n in notes)
    for old in ("com.x.app:id/next", "x/y"):  # an older flow: comXx.app:id/next, and only the whole id x/y
        f.write_text(f"appId: com.android.settings\n---\n- tapOn: {{id: '(?-ims){old}'}}\n")
        assert any("read it as a pattern" in n for n in import_flow(f, "com.android.settings")[1]), old
    f.write_text("appId: com.android.settings\n---\n- tapOn: {id: '(?-ims)\\$\\{TARGET\\}'}\n")
    assert any("would read as a Journeys variable" in n for n in import_flow(f, "com.android.settings")[1])
    (pack_dir / "journeys" / "v.yaml").write_text("name: v\nsteps:\n  - do: [{tap: {id: \"{{save}}\"}}]\n")
    pack = Pack.load(pack_dir)
    f.write_text(export(pack, pack.journeys["v"])[0])
    assert import_flow(f)[0]["steps"] == [{"do": [{"tap": {"id": "{{save}}"}}]}]  # escaped: no Maestro variable


def test_a_next_line_character_survives_the_flow_file_and_the_written_draft(pack_dir, tmp_path, capsys):
    from journeys.formats.maestro import export, import_flow
    (pack_dir / "journeys" / "n.yaml").write_text(
        "name: n\nsteps:\n  - do: [{tap: {id: \"a\\x85b\"}}, {tap: \"c\\x85d\"}]\n")
    pack = Pack.load(pack_dir)
    f = tmp_path / "n2.yaml"
    f.write_text(export(pack, pack.journeys["n"])[0])
    steps = [{"do": [{"tap": {"id": "a\x85b"}}, {"tap": "c\x85d"}]}]
    assert import_flow(f)[0]["steps"] == steps
    assert cli(pack_dir, "import", "maestro", str(f)) == 0 and [st.arg for st in Pack.load(pack_dir).drafts["n2"].steps] == [steps[0]["do"]]


def test_import_notes_case_once_for_a_hand_written_flow_and_never_for_a_generated_one(tmp_path):
    from journeys.formats.maestro import import_flow
    case = "Maestro matched this flow's texts and ids without case"
    f = tmp_path / "f.yaml"
    f.write_text("appId: com.android.settings\n---\n- tapOn: Ok\n- assertVisible: {id: a}\n"
                 "- scrollUntilVisible: {element: Done}\n")
    _, notes = import_flow(f, "com.android.settings")
    assert [n for n in notes if case in n] == [f"f.yaml: {case}; the draft matches them with case: check them"]
    f.write_text("appId: com.android.settings\n---\n- tapOn: {index: '1'}\n")
    assert not [n for n in import_flow(f, "com.android.settings")[1] if case in n]  # no text or id: no note
    f.write_text("appId: com.android.settings\n---\n- scrollUntilVisible: {element: Done}\n")
    assert any(case in n for n in import_flow(f, "com.android.settings")[1])  # a scroll's element too
    f.write_text("appId: com.android.settings\n---\n- tapOn: '(?-ims)Ok'\n- assertVisible: {id: '(?-ims)a'}\n")
    draft, notes = import_flow(f, "com.android.settings")
    assert not [n for n in notes if case in n] and draft["steps"] == [{"do": [{"tap": "Ok"}]}, {"see": {"id": "a"}}]

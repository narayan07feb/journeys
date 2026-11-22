"""Appium export: the WebdriverIO spec is run under node against a pretend WebdriverIO (tests/wdio_sim.js)."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from journeys.cli import main
from journeys.formats.appium import export
from journeys.pack import Pack

SIM = Path(__file__).with_name("wdio_sim.js")
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

APP = {
    "app": "com.android.settings",
    "start": "HOME",
    "screens": {
        "HOME": [{"text": "Network & internet"}, {"text": "Display", "below": 1, "go": "DISPLAY"},
                 {"desc": "Search settings", "go": "SEARCH"}],
        "DISPLAY": [{"text": "Brightness level"}, {"text": "Dark theme", "below": 2}],
        "SEARCH": [{"id": "com.android.settings.intelligence:id/search_src_text", "field": True},
                   {"desc": "Row", "id": "second"}, {"text": "Row", "y": 50, "go": "DISPLAY"}, {"text": 'He said "hi" \\\\ ok'}],
    },
}


def journey(pack_dir, body: str, name="t"):
    (pack_dir / "journeys" / f"{name}.yaml").write_text(f"name: {name}\n{body}")
    pack = Pack.load(pack_dir)
    assert not pack.errors, pack.errors
    return pack, pack.journeys[name]


def run(tmp_path, spec: str, app=APP, env=None):
    (tmp_path / "spec.js").write_text(spec)
    (tmp_path / "app.json").write_text(json.dumps(app))
    out = subprocess.run([NODE, str(SIM), str(tmp_path / "spec.js"), str(tmp_path / "app.json")],
                         capture_output=True, text=True, timeout=60, env={**os.environ, **(env or {})})
    assert out.returncode == 0, out.stderr
    *calls, verdict = out.stdout.strip().splitlines()
    return [json.loads(c) for c in calls], verdict


@needs_node
def test_example_journey_runs_through_the_map(pack_dir, tmp_path):
    pack = Pack.load(pack_dir)
    spec, notes = export(pack, pack.journeys["open-display"])
    assert any("UiAutomator regexes" in n for n in notes)
    calls, verdict = run(tmp_path, spec)
    assert verdict == "PASS"
    assert calls[:2] == [["terminate", "com.android.settings"], ["activate", "com.android.settings"]]
    assert ["click", "Display"] in calls
    assert [c for c in calls if c[0] == "mobile: swipeGesture"] == [["mobile: swipeGesture", "up"]] * 3


@needs_node
def test_a_missing_or_lingering_element_fails_the_spec(pack_dir, tmp_path):
    pack, j = journey(pack_dir, "steps:\n  - see: Nope\n")
    assert run(tmp_path, export(pack, j)[0])[1] == "FAIL not on the screen: Nope"
    pack, j = journey(pack_dir, "steps:\n  - not_see: /Network.*/\n")
    assert run(tmp_path, export(pack, j)[0])[1] == "FAIL still on the screen: /Network.*/"
    pack, j = journey(pack_dir, "steps:\n  - do: [{scroll_to: Never}]\n")
    calls, verdict = run(tmp_path, export(pack, j)[0])
    assert verdict == "FAIL not found while scrolling: Never"
    assert len([c for c in calls if c[0] == "mobile: swipeGesture"]) > 10  # it kept looking for 20 s


@needs_node
def test_text_then_description_index_ids_and_escaping(pack_dir, tmp_path):
    pack, j = journey(pack_dir, """steps:
  - do: [{tap: Search settings}]
  - see: {id: search_src_text}
  - not_see: {id: "other.pkg:id/search_src_text"}
  - see: {text: Row, index: 1}
  - not_see: {text: Row, index: 2}
  - see: {text: Row, index: 0, id: second}
  - see: {id: second}
  - see: 'He said "hi" \\\\ ok'
""")
    calls, verdict = run(tmp_path, export(pack, j)[0])
    assert verdict == "PASS", calls
    assert ["click", "Search settings"] in calls  # found by its content description
    pack, j = journey(pack_dir, "steps:\n  - do: [{tap: Search settings}, {tap: {text: Row, index: 0}}]\n"
                                "  - see: Brightness level\n")
    assert run(tmp_path, export(pack, j)[0])[1] == "PASS"  # the top Row (by text, y 50) leads on, not the desc


@needs_node
def test_typing_reads_variables_from_the_environment(pack_dir, tmp_path):
    pack, j = journey(pack_dir, """steps:
  - do: [{tap: Search settings}, {tap: {id: search_src_text}}]
  - input: "find ${DEMO_SEARCH}!"
""")
    spec = export(pack, j)[0]
    assert 'env("DEMO_SEARCH")' in spec and "wifi" not in spec  # the value is never in the file
    calls, verdict = run(tmp_path, spec, env={"DEMO_SEARCH": "wifi"})
    assert verdict == "PASS" and ["type", "find wifi!"] in calls
    env = {k: v for k, v in os.environ.items() if k != "DEMO_SEARCH"}
    (tmp_path / "spec.js").write_text(spec)
    (tmp_path / "app.json").write_text(json.dumps(APP))
    out = subprocess.run([NODE, str(SIM), str(tmp_path / "spec.js"), str(tmp_path / "app.json")],
                         capture_output=True, text=True, timeout=60, env=env)
    assert out.stdout.strip().splitlines()[-1].startswith("FAIL set DEMO_SEARCH in the environment")


@needs_node
def test_every_command_has_a_webdriverio_form(pack_dir, tmp_path):
    pack, j = journey(pack_dir, """start: {clear_state: true}
steps:
  - do: [{tap: Search settings}, {tap: {id: search_src_text}}, {type: abc}, {erase: 2}, {press: enter},
         hide_keyboard, back, wait_idle, scroll, stop, launch]
""")
    calls, verdict = run(tmp_path, export(pack, j)[0])
    assert verdict == "PASS", calls
    assert calls[:3] == [["terminate", "com.android.settings"], ["mobile: clearApp", "com.android.settings"],
                         ["activate", "com.android.settings"]]
    rest = [c for c in calls[3:] if c[0] != "click"]
    assert rest == [["type", "abc"], ["key", 67], ["key", 67], ["key", 66], ["hideKeyboard"], ["back"],
                    ["mobile: swipeGesture", "up"], ["terminate", "com.android.settings"],
                    ["terminate", "com.android.settings"], ["activate", "com.android.settings"]]


def test_refusals_and_the_cli(pack_dir, tmp_path, capsys):
    (pack_dir / "journeys" / "v.yaml").write_text(
        "name: v\nsteps:\n  - do: [{tap: \"${DEMO_SEARCH}\"}]\n")
    assert main(["--pack", str(pack_dir), "export", "appium", "v"]) == 2
    assert "cannot put a variable in a selector" in capsys.readouterr().err
    (pack_dir / "journeys" / "v.yaml").write_text("name: v\nsteps:\n  - hook: {name: note}\n")
    assert main(["--pack", str(pack_dir), "export", "appium", "v"]) == 2
    assert "nothing to export" in capsys.readouterr().err
    out = tmp_path / "open-display.e2e.js"
    assert main(["--pack", str(pack_dir), "export", "appium", "open-display", "-o", str(out)]) == 0
    assert out.read_text().startswith("// Exported from Journeys journey open-display")
    assert main(["--pack", str(pack_dir), "export", "appium", "open-display", "-o", str(out)]) == 2  # never over it

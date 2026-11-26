"""Appium export: the WebdriverIO spec is run under node against a pretend WebdriverIO (tests/wdio_sim.js)."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from journeys.actions import ActionError, to_command
from journeys.cli import main
from journeys.drivers.appium import AppiumDriver
from journeys.drivers.base import find
from journeys.formats.appium import _text, export
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
                   {"desc": "Row", "id": "second"}, {"text": "Row", "go": "DISPLAY"}, {"text": 'He said "hi" \\\\ ok'}],
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


def spec_of(pack_dir, body):
    pack, j = journey(pack_dir, body)
    return export(pack, j)[0]


@needs_node
def test_example_journey_runs_through_the_map(pack_dir, tmp_path):
    pack = Pack.load(pack_dir)
    spec, notes = export(pack, pack.journeys["open-display"])
    assert any("JavaScript regexes" in n for n in notes)
    calls, verdict = run(tmp_path, spec)
    assert verdict == "PASS"
    assert calls[1:3] == [["terminate", "com.android.settings"], ["activate", "com.android.settings"]]
    assert ["tap", "Display", 540, 240] in calls  # the centre of its box, after one swipe
    assert [c for c in calls if c[0] == "swipe"] == [["swipe", "up", 600]] * 3  # a slow drag, no fling


@needs_node
def test_the_test_gets_a_timeout_that_fits_the_journey(pack_dir, tmp_path):
    calls, verdict = run(tmp_path, spec_of(pack_dir, "steps:\n  - see: Network & internet\n  - do: [{scroll_to: Display}]\n"))
    assert verdict == "PASS" and calls[0] == ["timeout", 60_000 + 10_000 * 3 + 20_000]  # mocha's 60 s is too short


@needs_node
def test_a_missing_or_lingering_element_fails_the_spec(pack_dir, tmp_path):
    assert run(tmp_path, spec_of(pack_dir, "steps:\n  - see: Nope\n"))[1] == "FAIL not on the screen: Nope"
    assert run(tmp_path, spec_of(pack_dir, "steps:\n  - not_see: /Network.*/\n"))[1] == \
        "FAIL still on the screen: /Network.*/"
    calls, verdict = run(tmp_path, spec_of(pack_dir, "steps:\n  - do: [{scroll_to: Never}]\n"))
    assert verdict == "FAIL not found while scrolling: Never"
    assert len([c for c in calls if c[0] == "swipe"]) > 10  # it kept looking for 20 s


@needs_node
def test_scroll_to_wants_all_of_the_element_and_can_go_up(pack_dir, tmp_path):
    app = {**APP, "screens": {**APP["screens"], "HOME": [{"text": "Half", "bounds": [0, 2350, 1080, 2450]}]}}
    calls, verdict = run(tmp_path, spec_of(pack_dir, "steps:\n  - see: Half\n  - do: [{scroll_to: Half}]\n"), app=app)
    assert verdict == "FAIL not found while scrolling: Half"  # seen (partly) but never all on the screen
    calls, verdict = run(tmp_path, spec_of(
        pack_dir, "steps:\n  - do: [{scroll_to: {element: Never, direction: up}}]\n"), app=app)
    assert {c[1] for c in calls if c[0] == "swipe"} == {"down"}  # the finger moves down to see what is above


@needs_node
def test_labels_ids_index_and_escaping_follow_the_driver(pack_dir, tmp_path):
    calls, verdict = run(tmp_path, spec_of(pack_dir, """steps:
  - do: [{tap: Search settings}]
  - see: {id: search_src_text}
  - see: {id: "other.pkg:id/search_src_text"}
  - see: {text: Row, index: 1}
  - not_see: {text: Row, index: 2}
  - see: {text: Row, index: 0, id: second}
  - see: 'He said "hi" \\\\ ok'
  - do: [{tap: {text: Row, index: 1}}]
  - see: Brightness level
"""))
    assert verdict == "PASS", calls
    assert ["tap", "Search settings", 540, 340] in calls  # found by its content description


@needs_node
def test_one_label_per_element_counted_in_page_order(pack_dir, tmp_path):
    app = {**APP, "screens": {**APP["screens"], "HOME": [
        {"text": "Open", "desc": "Menu"}, {"text": "Top", "y": 900, "go": "DISPLAY"}, {"text": "Top", "y": 300},
        {"hint": "Type here"}, {"text": "a\tb"}]}}
    calls, verdict = run(tmp_path, spec_of(pack_dir, """steps:
  - not_see: Menu
  - see: Type here
  - see: "a\\tb"
  - do: [{tap: Top}]
  - see: Brightness level
"""), app=app)
    assert verdict == "PASS", calls  # text wins over the description; the first Top in the page, not the top one


@needs_node
def test_the_spec_finds_what_the_journeys_driver_finds(pack_dir, tmp_path):
    xml = ('<?xml version="1.0" encoding="UTF-8"?><hierarchy rotation="0">'
           '<android.widget.FrameLayout package="com.android.settings" bounds="[0,0][1080,2400]">'
           '<android.widget.TextView package="com.android.settings" text="Open" content-desc="Menu" bounds="[0,100][500,200]"/>'
           '<android.widget.TextView text="" content-desc="Menu" bounds="[0,300][500,400]"/>'
           '<android.widget.EditText text="" content-desc="" hint="Search here" resource-id="com.x:id/q" bounds="[0,500][1080,600]"/>'
           '<android.widget.TextView text="Row" bounds="[0,2500][1080,2600]"/>'
           '<android.widget.TextView text="Row" bounds="[0,2300][1080,2500]"/>'
           '<android.widget.TextView text="Row" bounds="[-100,0][1080,80]"/>'
           '<android.widget.TextView text="a &amp; b &#233; &quot;q&quot; &lt;x&gt;" bounds="[0,700][1080,800]"/>'
           '<android.widget.Button text="" clickable="true" resource-id="other.pkg:id/btn" bounds="[0,900][200,1000]"/>'
           '<android.widget.TextView text="Line&#10;two" bounds="[0,1100][1080,1200]"/>'
           '<android.widget.TextView text="&#x1F600; ok" resource-id="com.x:id/caf\u00e9" bounds="[0,1500][1080,1600]"/>'
           '<android.view.View text="" bounds="[0,1300][1080,1400]"/>'
           '</android.widget.FrameLayout></hierarchy>')
    d = AppiumDriver.__new__(AppiumDriver)
    d._s = lambda *a, **k: xml
    d._window = lambda: (1080, 2400)
    screen, centres, _ = d._read()
    app = {"app": "com.android.settings", "start": "X", "screens": {"X": {"xml": xml}}}
    sels = ["Menu", "Open", "Search here", {"id": "q"}, "Row", {"text": "Row", "index": 1}, {"text": "Row", "index": 2},
            'a & b é "q" <x>', {"id": "com.android.settings:id/btn"}, {"id": "btn"}, "/R.w/", "/Line.*/", "/Line\\ntwo/",
            {"text": "/M.*/", "index": 0}, "@3", "@99", "\U0001F600 ok", {"id": "caf\u00e9"}]
    for sel in sels:
        i = find(screen.elements, to_command({"tap": sel})["tapOn"])
        calls, verdict = run(tmp_path, spec_of(pack_dir, f"steps:\n  - do: [{{tap: {json.dumps(sel)}}}]\n"), app=app)
        got = [c[2:] for c in calls if c[0] == "tap"]
        assert (got == [list(centres[i])] if i is not None else verdict.startswith("FAIL not on the screen")), sel


@needs_node
def test_a_pattern_javascript_cannot_read_stops_the_spec_at_load(pack_dir, tmp_path):
    for pattern in ("(?P<n>Net).*", "(?i)net.*"):
        calls, verdict = run(tmp_path, spec_of(pack_dir, f"steps:\n  - see: /{pattern}/\n"))
        assert verdict.startswith(f'FAIL the pattern "{pattern}" works in Journeys but not in JavaScript')
        assert calls == [["end", 0]]  # before it touched the device


@needs_node
def test_an_expired_session_fails_at_once_and_a_bad_read_is_read_again(pack_dir, tmp_path):
    spec = spec_of(pack_dir, "steps:\n  - see: Network & internet\n")
    for reads in (["dead"], ["ok", "dead"]):  # in launch, then in a check
        calls, verdict = run(tmp_path, spec, app={**APP, "reads": reads})
        assert verdict == "FAIL A session is either terminated or not started" and calls[-1] == ["end", 0], reads
    calls, verdict = run(tmp_path, spec, app={**APP, "reads": ["fail", "ok", "fail", "fail", "ok"]})
    assert verdict == "PASS" and calls[-1] == ["end", 1300]  # launch read again once, the check twice
    calls, verdict = run(tmp_path, spec, app={**APP, "reads": ["ok"] + ["fail"] * 50})
    assert verdict == "FAIL cannot read the screen"  # still unreadable at the end: its own error


@needs_node
def test_hide_keyboard_falls_back_to_the_mobile_command_and_no_keyboard_is_nothing_to_hide(pack_dir, tmp_path):
    spec = spec_of(pack_dir, "steps:\n  - do: [{tap: Search settings}, {tap: {id: search_src_text}}, hide_keyboard,"
                             " hide_keyboard]\n")
    for app in (APP, {**APP, "old_routes": False}):
        calls, verdict = run(tmp_path, spec, app=app)
        assert verdict == "PASS" and [c for c in calls if c[0] == "hideKeyboard"] == [["hideKeyboard"]], app


@needs_node
def test_launch_waits_for_the_app_and_wait_idle_for_a_still_screen(pack_dir, tmp_path):
    spec = spec_of(pack_dir, "steps:\n  - see: Network & internet\n")
    calls, verdict = run(tmp_path, spec, app={**APP, "launcher_reads": 3})
    assert verdict == "PASS" and calls[-1] == ["end", 900]  # three reads of the launcher, 300 ms apart
    calls, verdict = run(tmp_path, spec_of(pack_dir, "steps:\n  - do: [wait_idle]\n"), app={**APP, "changing": 3})
    assert verdict == "PASS" and calls[-1] == ["end", 900]  # launch took one changed read; idle three more


@needs_node
def test_typing_reads_variables_from_the_environment(pack_dir, tmp_path):
    spec = spec_of(pack_dir, """steps:
  - do: [{tap: Search settings}, {tap: {id: search_src_text}}, {type: "${DEMO_SEARCH}"}]
  - input: "find ${DEMO_SEARCH}!"
""")
    assert 'env("DEMO_SEARCH")' in spec and "wifi" not in spec  # the value is never in the file
    calls, verdict = run(tmp_path, spec, env={"DEMO_SEARCH": "wifi"})
    assert verdict == "PASS" and [c for c in calls if c[0] == "type"] == [["type", "wifi"], ["type", "find wifi!"]]
    env = {k: v for k, v in os.environ.items() if k != "DEMO_SEARCH"}
    out = subprocess.run([NODE, str(SIM), str(tmp_path / "spec.js"), str(tmp_path / "app.json")],
                         capture_output=True, text=True, timeout=60, env=env)
    assert out.stdout.strip().splitlines()[-1].startswith("FAIL set DEMO_SEARCH in the environment")


@needs_node
def test_typed_text_keeps_quotes_backslashes_newlines_and_other_letters(pack_dir, tmp_path):
    calls, verdict = run(tmp_path, spec_of(pack_dir, """steps:
  - do: [{tap: Search settings}, {tap: {id: search_src_text}}, {type: "a\\"b\\\\c\\nd é ✓"}]
"""))
    assert verdict == "PASS" and ["type", 'a"b\\c\nd é ✓'] in calls


@needs_node
def test_every_command_has_a_webdriverio_form(pack_dir, tmp_path):
    calls, verdict = run(tmp_path, spec_of(pack_dir, """start: {clear_state: true}
steps:
  - do: [{tap: Search settings}, {tap: {id: search_src_text}}, {type: abc}, {erase: 2}, {press: enter},
         hide_keyboard, hide_keyboard, back, wait_idle, scroll, stop, launch]
"""))
    assert verdict == "PASS", calls
    assert calls[1:4] == [["terminate", "com.android.settings"], ["mobile: clearApp", "com.android.settings"],
                          ["activate", "com.android.settings"]]
    rest = [c for c in calls[4:] if c[0] not in ("tap", "end")]
    assert rest == [["type", "abc"], ["key", 67], ["key", 67], ["key", 66], ["hideKeyboard"], ["back"],
                    ["swipe", "up", 600], ["terminate", "com.android.settings"],
                    ["terminate", "com.android.settings"], ["activate", "com.android.settings"]]  # one hide: then none up


@needs_node
def test_a_journey_without_launch_starts_on_the_screen_it_finds(pack_dir, tmp_path):
    spec = spec_of(pack_dir, "start: {launch: false}\nsteps:\n  - see: Display\n")
    assert "await launch(" not in spec
    assert run(tmp_path, spec)[1] == "FAIL not on the screen: Display"  # nothing started the app


def test_names_in_the_header_cannot_break_out_of_the_comment(pack_dir, tmp_path):
    y = pack_dir / "pack.yaml"
    y.write_text(y.read_text().replace("name: settings-demo", 'name: "a\\nconsole.log(1);//"'))
    pack = Pack.load(pack_dir)
    spec = export(pack, pack.journeys["open-display"])[0]
    assert spec.splitlines()[0] == '// Exported from Journeys: journey "open-display", pack "a\\nconsole.log(1);//".'


def test_a_goto_with_no_known_start_is_left_out_with_a_note(pack_dir):
    pack, j = journey(pack_dir, "steps:\n  - do: [back]\n  - goto: SETTINGS-DISPLAY\n")
    assert any("goto SETTINGS-DISPLAY: start screen unknown" in n for n in export(pack, j)[1])


def test_the_spec_file_holds_no_raw_line_separators(pack_dir):
    spec = spec_of(pack_dir, 'steps:\n  - see: "a\\u2028b\\u2029c"\n  - do: [{type: "x\\u2028y"}]\n')
    assert "\u2028" not in spec and "\u2029" not in spec and "\\u2028" in spec


def test_an_anchor_javascript_reads_as_a_letter_is_refused(pack_dir):
    for pattern in ("/Total\\Z/", "/\\ATotal/"):
        pack, j = journey(pack_dir, f"steps:\n  - see: '{pattern}'\n")
        with pytest.raises(ActionError, match="uses .A or .Z"):
            export(pack, j)
    pack, j = journey(pack_dir, "steps:\n  - see: '/a\\\\Z/'\n")  # an escaped backslash, then a plain Z
    assert export(pack, j)


def test_a_failed_write_leaves_no_file(pack_dir, tmp_path, capsys, monkeypatch):
    out = tmp_path / "x.e2e.js"
    real = open

    class Full:
        def __init__(self, f):
            self.f = f

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.f.close()

        def write(self, _):
            raise OSError(28, "No space left on device")

    monkeypatch.setattr("builtins.open", lambda p, m="r", *a, **k: Full(real(p, m)) if p == str(out) else real(p, m, *a, **k))
    assert main(["--pack", str(pack_dir), "export", "appium", "open-display", "-o", str(out)]) == 2
    assert "No space left on device" in capsys.readouterr().err and not out.exists()


def test_an_erase_gets_time_for_each_character(pack_dir):
    spec = spec_of(pack_dir, "steps:\n  - do: [{erase: 1000}]\n")
    assert "this.timeout(" + str(60_000 + 10_000 * 2 + 200 * 1000) + ");" in spec


def test_an_undeclared_variable_in_typed_text_is_refused(pack_dir):
    pack = Pack.load(pack_dir)
    with pytest.raises(ActionError, match=r"\$\{NOPE\} is not declared"):
        _text(pack, "a ${NOPE}")


def test_variables_in_any_selector_are_refused_and_accounts_map_to_their_env_var(pack_dir):
    for sel in ('{id: "${DEMO_SEARCH}"}', '"/x${DEMO_SEARCH}/"', '"${DEMO_SEARCH}"'):
        pack, j = journey(pack_dir, f"steps:\n  - see: {sel}\n")
        with pytest.raises(ActionError, match="cannot put a variable in a selector"):
            export(pack, j)
    y = pack_dir / "pack.yaml"
    y.write_text(y.read_text() + "accounts:\n  demo: {user: DEMO_USER}\n")
    pack, j = journey(pack_dir, 'steps:\n  - input: "${demo.user}/${DEMO_USER}"\n')
    spec = export(pack, j)[0]
    assert spec.count('env("DEMO_USER")') == 2 and "demo.user" not in spec


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
    assert out.read_text().startswith("// Exported from Journeys: journey \"open-display\"")
    capsys.readouterr()
    assert main(["--pack", str(pack_dir), "export", "appium", "open-display", "-o", str(out)]) == 2  # never over it
    assert "NOTE" not in capsys.readouterr().err  # nothing was written, so no notes about it

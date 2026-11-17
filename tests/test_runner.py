import os

import pytest

from journeys.drivers.fake import FakeDriver
from journeys.pack import Pack
from journeys.results import Store
from journeys.runner import Runner

import yaml


@pytest.fixture
def env(pack_dir, tmp_path):
    pack = Pack.load(pack_dir)
    app = yaml.safe_load((pack_dir / "fake" / "app.yaml").read_text())
    driver = FakeDriver(app)
    store = Store(tmp_path / "state")
    run = store.new_run(pack=pack.name)

    def go(name, variables=None):
        r = Runner(pack, driver, store, run, variables=variables or {}, sleep=lambda s: None)
        return r.run_all(pack.select(name))

    return pack, app, driver, store, run, go


def test_journeys_pass_and_run_is_saved(env):
    pack, app, driver, store, run, go = env
    outs = go("smoke")
    assert [o.status for o in outs] == ["passed", "passed"]
    saved = store.run(run.id)
    assert saved["status"] == "passed" and len(saved["journeys"]) == 2
    assert all(s["status"] == "passed" for s in saved["journeys"][0]["steps"])
    assert {"launchApp"} <= {c if isinstance(c, str) else next(iter(c)) for c in driver.log}


def test_wrong_screen_fails_with_failure_record(env):
    pack, app, driver, store, run, go = env
    # The Display tap now opens About. The runner detours (About -> Home), meets the same broken
    # edge again, and stops; no proposal is kept for a walk that never arrived.
    for e in app["screens"]["HOME"]["elements"]:
        if e["text"] == "Display":
            e["to"] = "ABOUT"
    r = Runner(pack, driver, store, run, variables={}, sleep=lambda s: None, heal=False)
    (out,) = r.run_all(pack.select("open-display"))
    assert out.status == "failed"
    assert "edge SETTINGS-HOME -> SETTINGS-DISPLAY landed on SETTINGS-ABOUT" in out.message
    f = store.failure(out.failure)
    assert f["journey"] == "open-display" and f["screen"] == "SETTINGS-ABOUT"
    assert "Android version" in f["listing"] and f["screenshot"].endswith(".txt")
    assert f["file"].endswith("display.yaml") and f["line"] == 5
    assert store.run(run.id)["status"] == "failed"
    (out,) = go("open-display")  # with healing
    assert "landed on SETTINGS-ABOUT (again, after a detour)" in out.message
    assert out.changes == [] and store.proposals() == []


def test_missing_element_and_crash(env):
    pack, app, driver, store, run, go = env
    app["screens"]["DISPLAY"]["elements"] = [
        e for e in app["screens"]["DISPLAY"]["elements"] if e["text"] != "Dark theme"
    ]
    (out,) = go("open-display")
    assert out.status == "failed" and "Element not found" in out.message
    # A tap that kills the app: the message says the app is gone.
    for e in app["screens"]["HOME"]["elements"]:
        if e["text"] == "Display":
            e["to"] = "CRASH"
    (out,) = go("open-display")
    assert "the app is not running" in out.message


def test_expect_retries_then_fails(env):
    pack, app, driver, store, run, go = env
    app["start"] = "ABOUT"
    sleeps = []
    r = Runner(pack, driver, store, run, variables={}, sleep=sleeps.append, settle_tries=3, heal=False)
    (out,) = r.run_all(pack.select("open-display"))
    assert out.status == "failed" and "expected screen SETTINGS-HOME, on SETTINGS-ABOUT" in out.message
    assert sleeps == [0.5, 0.5]


def test_variables_and_hook(env, pack_dir):
    pack, app, driver, store, run, go = env
    (out,) = go("search-settings", {"DEMO_SEARCH": "wifi"})
    assert out.status == "passed"
    assert {"inputText": "wifi"} in driver.log
    (pack_dir / "hooks" / "note.sh").write_text("#!/bin/sh\necho bad >&2\nexit 3\n")
    (out,) = go("android-version")
    assert out.status == "failed" and "hook note exited 3: bad" in out.message


def test_event_step(env, pack_dir):
    pack, app, driver, store, run, go = env
    script = pack_dir / "hooks" / "event.sh"
    script.write_text('#!/bin/sh\necho log\ncase "$1" in *opened*) echo \'{"fired": true}\';; *) echo \'{"fired": false}\';; esac\n')
    os.chmod(script, 0o755)
    (pack_dir / "pack.yaml").write_text((pack_dir / "pack.yaml").read_text() + "events:\n  check: [hooks/event.sh]\n")
    (pack_dir / "journeys" / "ev.yaml").write_text(
        "- name: ev-ok\n  steps:\n    - event: {name: opened}\n"
        "- name: ev-no\n  steps:\n    - event: {name: closed}\n"
    )
    pack2 = Pack.load(pack_dir)
    assert pack2.errors == []
    r = Runner(pack2, driver, store, run, variables={}, sleep=lambda s: None)
    ok, no = r.run_all([pack2.journeys["ev-ok"], pack2.journeys["ev-no"]])
    assert ok.status == "passed"
    assert no.status == "failed" and "did not fire" in no.message

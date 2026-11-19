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
        if e["text"].startswith("Display"):
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
        e for e in app["screens"]["DISPLAY"]["elements"] if not e["text"].startswith("Dark theme")
    ]
    (out,) = go("open-display")
    assert out.status == "failed" and "Element not found" in out.message
    # A tap that kills the app: the message says the app is gone.
    for e in app["screens"]["HOME"]["elements"]:
        if e["text"].startswith("Display"):
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


def test_a_stopped_run_leaves_no_journey_running_and_a_lost_device_is_not_a_crash(pack_dir, tmp_path):
    import pytest
    import yaml
    from journeys.drivers.base import DriverError
    from journeys.drivers.fake import FakeDriver
    from journeys.pack import Pack
    from journeys.results import Store
    from journeys.runner import Runner
    pack = Pack.load(pack_dir)
    app = yaml.safe_load((pack_dir / "fake" / "app.yaml").read_text())
    store = Store(tmp_path / "s")
    d = FakeDriver(app)
    d.do = lambda cmds: (_ for _ in ()).throw(KeyboardInterrupt())
    run = store.new_run()
    with pytest.raises(KeyboardInterrupt):
        Runner(pack, d, store, run, variables={}, sleep=lambda s: None).run_all(pack.select("smoke"))
    data = store.run(run.id)
    assert data["status"] == "failed" and all(j["status"] != "running" for j in data["journeys"])
    d = FakeDriver(app)
    d.do = lambda cmds: (_ for _ in ()).throw(DriverError("device x is not connected"))
    d.app_alive = lambda: (_ for _ in ()).throw(DriverError("device x is not connected"))
    (out,) = Runner(pack, d, store, store.new_run(), variables={}, sleep=lambda s: None,
                    heal=False).run_all(pack.select("open-display"))
    assert "device does not answer" in out.message and "crash?" not in out.message


def test_a_stopped_run_ends_the_live_view_too(pack_dir, tmp_path):
    import json
    import pytest
    import yaml
    from journeys.drivers.fake import FakeDriver
    from journeys.pack import Pack
    from journeys.results import Store
    from journeys.runner import Runner
    pack = Pack.load(pack_dir)
    d = FakeDriver(yaml.safe_load((pack_dir / "fake" / "app.yaml").read_text()))
    calls = {"n": 0}
    real = d.do

    def do(cmds):
        calls["n"] += 1
        if calls["n"] == 3:
            raise KeyboardInterrupt
        return real(cmds)
    d.do = do
    store = Store(tmp_path / "s")
    with pytest.raises(KeyboardInterrupt):
        Runner(pack, d, store, store.new_run(), variables={}, sleep=lambda s: None).run_all(pack.select("smoke"))
    assert json.loads((tmp_path / "s" / "live.json").read_text())["status"] == "failed"


def test_journey_names_are_not_masked(pack_dir, tmp_path):
    from journeys.results import mask, set_secrets
    set_secrets(["display"])
    try:
        assert mask({"name": "open-display", "message": "open-display"}) == {"name": "open-display",
                                                                            "message": "open-•••"}
    finally:
        set_secrets([])


def test_json_and_repr_escaped_secrets_are_masked():
    import json
    from journeys.results import mask, set_secrets
    set_secrets(['my"secret', "pässwörd1", "it's-s3cret"])
    try:
        for s in ('my"secret', "pässwörd1", "it's-s3cret"):
            for form in (s, json.dumps(s), json.dumps(s, ensure_ascii=False), repr(s)):
                assert s not in mask(form) and json.dumps(s)[1:-1] not in mask(form), form
    finally:
        set_secrets([])

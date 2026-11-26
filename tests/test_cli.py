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
    assert main(base + ["run", "smoke", "--driver", "maestro"]) == 2
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


def test_build_and_install_steps(pack_dir, tmp_path, capsys):
    (pack_dir / "pack.yaml").write_text(
        (pack_dir / "pack.yaml").read_text()
        + "build:\n  - [sh, -c, 'echo built > out.txt']\n"
        + "install:\n  - [sh, -c, 'echo {serial} $JOURNEYS_SERIAL >> out.txt']\n  - [sh, -c, 'exit 4']\n"
    )
    base = ["--pack", str(pack_dir), "--state", str(tmp_path / "s")]
    assert main(base + ["build"]) == 0
    assert main(base + ["install"]) == 2  # needs a serial
    assert main(base + ["install", "--serial", "emu-1"]) == 1  # second step fails
    assert (pack_dir / "out.txt").read_text() == "built\nemu-1 emu-1\n"


def test_a_killed_run_ends_failed_not_running(pack_dir, tmp_path):
    """SIGTERM (an MCP child stopped, a closed terminal) ends the run as failed with an end time."""
    import signal
    import subprocess
    import sys
    import time
    import yaml
    (pack_dir / "hooks" / "note.sh").write_text("#!/bin/sh\nsleep 30\n")
    state = tmp_path / "s"
    proc = subprocess.Popen([sys.executable, "-m", "journeys", "--pack", str(pack_dir), "--state", str(state),
                             "run", "android-version", "--driver", "fake"], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, env={**__import__("os").environ, "PYTHONDONTWRITEBYTECODE": "1"})
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


def test_look_and_errors_never_print_a_secret(pack_dir, capsys, monkeypatch):
    from journeys import cli
    from journeys.cli import main
    from journeys.drivers.base import Element, Screen
    from journeys.drivers.fake import FakeDriver
    monkeypatch.setattr(FakeDriver, "look", lambda self: Screen([Element("@1", "Text", "Search hunter2")]))
    monkeypatch.setenv("DEMO_SEARCH", "hunter2")
    args = ["--pack", str(pack_dir), "--state", str(pack_dir / "s")]
    assert main(args + ["look", "--driver", "fake"]) == 0
    out = capsys.readouterr().out
    assert "Search •••" in out and "hunter2" not in out
    monkeypatch.setattr(cli, "_open", lambda *a: (_ for _ in ()).throw(cli.DriverError("tap hunter2 failed")))
    assert main(args + ["look", "--driver", "fake"]) == 2
    assert "hunter2" not in capsys.readouterr().err


def test_a_reused_run_id_is_a_clear_error(pack_dir, capsys):
    from journeys.cli import main
    args = ["--pack", str(pack_dir), "--state", str(pack_dir / "s"), "run", "open-display", "--driver", "fake",
            "--run-id", "abc"]
    assert main(args) == 0
    assert main(args) == 2 and "already exists" in capsys.readouterr().err


def test_journeys_driver_env_is_an_answer_and_eof_repeats_the_question(pack_dir, capsys, monkeypatch):
    from journeys.cli import main
    f = pack_dir / "pack.yaml"
    f.write_text("\n".join(ln for ln in f.read_text().splitlines() if not ln.startswith("driver:")) + "\n")
    args = ["--pack", str(pack_dir), "--state", str(pack_dir / "s"), "run", "open-display"]
    monkeypatch.setenv("JOURNEYS_DRIVER", "fake")
    assert main(args) == 0
    monkeypatch.delenv("JOURNEYS_DRIVER")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: (_ for _ in ()).throw(EOFError()))
    assert main(args) == 2 and "--driver" in capsys.readouterr().err


def test_an_unwritable_state_folder_is_a_clear_error(pack_dir, tmp_path, capsys):
    blocker = tmp_path / "file"
    blocker.write_text("")
    assert main(["--pack", str(pack_dir), "--state", str(blocker / "s"), "run", "smoke", "--driver", "fake"]) == 2
    assert "error:" in capsys.readouterr().err


def test_hook_args_with_a_date_do_not_crash(pack_dir, tmp_path):
    j = pack_dir / "journeys" / "about.yaml"
    j.write_text(j.read_text().replace("args: {saw: android-version}", "args: {day: 2024-01-01}"))
    assert main(["--pack", str(pack_dir), "--state", str(tmp_path / "s"), "run", "android-version",
                 "--driver", "fake"]) == 0


def test_init_takes_journeys_driver_as_the_answer(tmp_path, monkeypatch):
    from journeys.pack import Pack
    monkeypatch.setenv("JOURNEYS_DRIVER", "speedrun")
    assert main(["--pack", str(tmp_path / "p"), "init", "--app", "com.x.y"]) == 0
    assert Pack.load(tmp_path / "p").driver == "speedrun"


def test_nothing_to_run_is_not_a_pass(pack_dir, tmp_path, capsys):
    for f in (pack_dir / "journeys").glob("*.yaml"):
        f.write_text("")
    (pack_dir / "sets.yaml").write_text("sets: {}\n")
    assert main(["--pack", str(pack_dir), "--state", str(tmp_path / "s"), "run", "all", "--driver", "fake"]) == 2
    assert "no journeys to run" in capsys.readouterr().err


def test_a_failing_dka_check_still_stops_the_driver(tappable_pack_dir, monkeypatch):
    from journeys.drivers.base import DriverError
    from journeys.drivers.fake import FakeDriver
    stopped = []
    monkeypatch.setattr(FakeDriver, "is_emulator", lambda self, *a: (_ for _ in ()).throw(DriverError("offline")))
    monkeypatch.setattr(FakeDriver, "stop", lambda self: stopped.append(1))
    assert main(["--pack", str(tappable_pack_dir), "--state", str(tappable_pack_dir / "s"), "tap-all",
                 "--driver", "fake", "--dka"]) == 2
    assert stopped


def test_ctrl_c_at_the_driver_question_is_a_clear_error(pack_dir, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: (_ for _ in ()).throw(KeyboardInterrupt()))
    assert main(["--pack", str(pack_dir), "--state", str(pack_dir / "s"), "run", "smoke"]) == 2
    assert "no driver given" in capsys.readouterr().err


def test_a_bad_fake_app_is_a_clear_error(pack_dir, tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    for text in ("- just a list\n", "app: com.android.settings\nscreens: {A: {elements: []}}\n"):
        bad.write_text(text)
        assert main(["--pack", str(pack_dir), "--state", str(tmp_path / "s"), "run", "smoke", "--driver", "fake",
                     "--fake-app", str(bad)]) == 2
        err = capsys.readouterr().err
        assert "a fake app is a map" in err or "start names a screen" in err


def test_a_stop_before_the_runner_starts_still_ends_failed(pack_dir, tmp_path, monkeypatch, capsys):
    import yaml
    from journeys import cli
    monkeypatch.setattr(cli, "Runner", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
    assert main(["--pack", str(pack_dir), "--state", str(tmp_path / "s"), "run", "smoke", "--driver", "fake"]) == 130
    (run,) = (tmp_path / "s").rglob("run.json")
    data = yaml.safe_load(run.read_text())
    assert data["status"] == "failed" and data.get("ended")


def test_init_refuses_an_existing_pack_before_asking_and_doctor_reads_journeys_driver(pack_dir, capsys, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: (_ for _ in ()).throw(AssertionError("asked")))
    assert main(["--pack", str(pack_dir), "init", "--app", "com.x.y"]) == 2
    assert "already exists" in capsys.readouterr().err
    monkeypatch.setenv("JOURNEYS_DRIVER", "speedrun")
    main(["--pack", str(pack_dir), "doctor"])
    assert "driver speedrun (chosen)" in capsys.readouterr().out


def test_tap_all_that_tapped_nothing_exits_1(tappable_pack_dir, capsys):
    import yaml
    f = tappable_pack_dir / "fake" / "app.yaml"
    app = yaml.safe_load(f.read_text())
    app["screens"][app["start"]]["elements"] = [{"kind": "Button", "text": "Delete everything"}]
    f.write_text(yaml.safe_dump(app))
    assert main(["--pack", str(tappable_pack_dir), "--state", str(tappable_pack_dir / "s"), "tap-all",
                 "--driver", "fake"]) == 1
    assert "nothing was tapped" in capsys.readouterr().out


def test_fake_app_inner_shapes_and_fake_screenshots_are_checked(pack_dir, tmp_path, capsys, monkeypatch):
    bad = tmp_path / "bad.yaml"
    for inner in ("HOME: null", "HOME: {elements: 5}", "HOME: {elements: [{text: 5}]}", "HOME: {elements: [{to: [1]}]}"):
        bad.write_text(f"app: com.android.settings\nstart: HOME\nscreens:\n  {inner}\n")
        assert main(["--pack", str(pack_dir), "--state", str(tmp_path / "s"), "run", "smoke", "--driver", "fake",
                     "--fake-app", str(bad)]) == 2, inner
        assert "error:" in capsys.readouterr().err
    from journeys.drivers.base import Element, Screen
    from journeys.drivers.fake import FakeDriver
    from journeys.results import set_secrets
    import yaml
    d = FakeDriver(yaml.safe_load((pack_dir / "fake" / "app.yaml").read_text()))
    monkeypatch.setattr(FakeDriver, "look", lambda self: Screen([Element("@1", "Text", "Hello hunter2")]))
    set_secrets(["hunter2"])
    try:
        shot = d.screenshot(tmp_path / "shot.png")
    finally:
        set_secrets([])
    assert "hunter2" not in shot.read_text()


def test_state_dir_never_leaves_its_folder_and_init_needs_an_app_first(tmp_path, monkeypatch, capsys):
    from journeys.results import default_state_dir
    monkeypatch.delenv("JOURNEYS_STATE", raising=False)
    assert default_state_dir("../../x").parent.name == "journeys"
    monkeypatch.setattr("builtins.input", lambda _: (_ for _ in ()).throw(AssertionError("asked")))
    assert main(["--pack", str(tmp_path / "p"), "init"]) == 2 and "no app id" in capsys.readouterr().err


def test_init_with_an_ambiguous_project_stops_before_asking(tmp_path, capsys, monkeypatch):
    proj = tmp_path / "proj"
    for m, pkg in (("a", "com.acme.one"), ("b", "com.acme.two")):
        (proj / m).mkdir(parents=True)
        (proj / m / "build.gradle.kts").write_text(f'applicationId = "{pkg}"\n')
    monkeypatch.setattr("builtins.input", lambda _: (_ for _ in ()).throw(AssertionError("asked")))
    assert main(["--pack", str(tmp_path / "p"), "init", "--project", str(proj)]) == 2
    assert "several app ids" in capsys.readouterr().err


def test_a_failing_stop_does_not_hide_the_results_and_doctor_flags_a_bad_env_driver(pack_dir, tmp_path, capsys,
                                                                                    monkeypatch):
    from journeys.drivers.base import DriverError
    from journeys.drivers.fake import FakeDriver
    monkeypatch.setattr(FakeDriver, "stop", lambda self: (_ for _ in ()).throw(DriverError("timeout")))
    assert main(["--pack", str(pack_dir), "--state", str(tmp_path / "s"), "run", "open-display",
                 "--driver", "fake"]) == 0
    out = capsys.readouterr()
    assert "1/1 passed" in out.out and "WARN driver stop" in out.err
    monkeypatch.setenv("JOURNEYS_DRIVER", "bogus")
    assert main(["--pack", str(pack_dir), "doctor"]) == 1
    assert "driver bogus is not one of" in capsys.readouterr().out


def test_a_failed_set_cleanup_fails_the_run_command(pack_dir, tmp_path, capsys):
    (pack_dir / "hooks" / "cleanup.sh").write_text("#!/bin/sh\necho still on >&2\nexit 5\n")
    (pack_dir / "hooks" / "cleanup.sh").chmod(0o755)
    f = pack_dir / "pack.yaml"
    f.write_text(f.read_text().replace("hooks:\n", "hooks:\n  cleanup: [hooks/cleanup.sh]\n"))
    (pack_dir / "sets.yaml").write_text("sets:\n  smoke: {tags: [smoke], before: [note], after: [cleanup]}\n")
    base = ["--pack", str(pack_dir), "--state", str(tmp_path / "s")]
    assert main(base + ["run", "smoke", "--driver", "fake"]) == 1  # both journeys pass; the cleanup did not
    out = capsys.readouterr().out
    assert "2/2 passed" in out and "passed  set before: hook note" in out
    assert "failed  set after: hook cleanup  hook cleanup exited 5: still on" in out
    (pack_dir / "sets.yaml").write_text("sets:\n  smoke: {tags: [smoke], before: [cleanup]}\n")
    assert main(base + ["run", "smoke", "--driver", "fake"]) == 1
    assert "0/2 passed (no journey ran: a before: hook failed)" in capsys.readouterr().out

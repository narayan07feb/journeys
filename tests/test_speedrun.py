from pathlib import Path

import pytest

from journeys.drivers import speedrun
from journeys.drivers.base import DriverError
from journeys.drivers.speedrun import SpeedrunDriver, parse_look

LOOK = '''android 1080x2400, 9 elements
@1 Text "Sign in"
@2 Text "Say \\"hi\\"\\nnow"
@5 Field #phone-input hint="(555) 010-1234"
@6 View "checkbox, unchecked" #remember-me
@7 Button "Next" #next disabled
@8 Button "Continue as guest" #guest
@9 Switch "Dark theme" checked
@10 Field "a@b.co" #email focused error="Bad \\"email\\""
@11 Text "A very long label…"
Keyboard
'''


def test_parse_look():
    s = parse_look(LOOK)
    assert s.header == "android 1080x2400, 9 elements"
    by = {e.ref: e for e in s.elements}
    assert len(s.elements) == 9
    assert by["@1"].kind == "Text" and by["@1"].text == "Sign in"
    assert by["@2"].text == 'Say "hi"\nnow'
    assert by["@5"].text == "" and by["@5"].id == "phone-input" and by["@5"].state == ()
    assert by["@7"].id == "next" and by["@7"].state == ("disabled",)
    assert by["@9"].state == ("checked",)
    assert by["@10"].text == "a@b.co" and by["@10"].state == ("focused",)
    assert by["@11"].text.endswith("…")


def test_find_speedrun_prefers_env_then_third_party(tmp_path, monkeypatch):
    monkeypatch.setenv("JOURNEYS_SPEEDRUN", "/x/speedrun")
    assert speedrun.find_speedrun(tmp_path) is None  # a path that is not there is not a speedrun
    mine = tmp_path / "my-speedrun"
    mine.write_text("")
    mine.chmod(0o755)
    monkeypatch.setenv("JOURNEYS_SPEEDRUN", str(mine))
    assert speedrun.find_speedrun(tmp_path) == str(mine)
    monkeypatch.delenv("JOURNEYS_SPEEDRUN")
    for v in ("v0.4.2", "v0.4.9"):
        (tmp_path / "third_party" / "speedrun" / v).mkdir(parents=True)
        (tmp_path / "third_party" / "speedrun" / v / "speedrun").write_text("")
    assert speedrun.find_speedrun(tmp_path).endswith("v0.4.9/speedrun")


def test_skips_speedrun_in_home_dot_speedrun(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".speedrun").mkdir(parents=True)
    fake = home / ".speedrun" / "speedrun"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", str(home / ".speedrun"))
    monkeypatch.delenv("JOURNEYS_SPEEDRUN", raising=False)
    assert speedrun.find_speedrun(tmp_path / "empty") is None


def test_driver_runs_speedrun_with_private_home(tmp_path):
    """A stand-in speedrun binary records its HOME and args, and answers like the real one."""
    log = tmp_path / "calls.log"
    stub = tmp_path / "speedrun"
    stub.write_text(f'''#!/bin/sh
echo "HOME=$HOME ARGS=$*" >> {log}
case "$1" in
  look) printf 'android 10x10, 1 elements\\n@1 Text "Hi" #hi\\n';;
  do) cat >> {log}; if grep -q Nope {log}; then echo 'FAILED Tap on "Nope"  1.0 s'; echo '  Element not found'; exit 1; fi;;
esac
''')
    stub.chmod(0o755)
    d = SpeedrunDriver(str(stub), tmp_path / "srhome")
    d.start("com.example", "emulator-5554")
    assert d.look().elements[0].id == "hi"
    assert d.do([{"tapOn": "Hi"}, "back"]).ok
    res = d.do([{"tapOn": "Nope"}])
    assert not res.ok and "Element not found" in res.message
    d.stop()
    text = log.read_text()
    assert f"HOME={tmp_path / 'srhome'}" in text and str(Path.home()) + " " not in text
    assert "session start --app com.example --platform android --animations normal --device emulator-5554" in text
    assert "- tapOn: Hi\n- back\n" in text and "session stop" in text


def test_foreground_app_from_dumpsys(tmp_path, monkeypatch):
    import subprocess as sp
    d = SpeedrunDriver("/bin/true", tmp_path / "h")
    out = b"  mCurrentFocus=Window{1a2b3c u0 com.android.settings/com.android.settings.Settings}\n"
    monkeypatch.setattr(d, "_adb", lambda *a: sp.CompletedProcess(a, 0, out, b""))
    assert d.foreground_app() == "com.android.settings"
    monkeypatch.setattr(d, "_adb", lambda *a: sp.CompletedProcess(a, 0, b"nothing", b""))
    assert d.foreground_app() is None


def test_shared_device_guard_refuses_what_speedrun_would_change(tmp_path, monkeypatch):
    """On a real phone the pack does not allow to change, start stops before `session start`."""
    import subprocess as sp
    phone = {"getprop ro.kernel.qemu": b"", "getprop ro.build.version.sdk": b"36",
             "ls /data/local/tmp/speedrun-agent-*.apk 2>/dev/null": b"/data/local/tmp/speedrun-agent-ab12.apk\n",
             "pidof dev.mobile.maestro dev.mobile.maestro.test": b"4242"}
    adb = lambda *a: sp.CompletedProcess(a, 0, phone.get(" ".join(a[1:]), b""), b"")
    started = []
    d = SpeedrunDriver("/bin/true", tmp_path / "h")
    monkeypatch.setattr(d, "_adb", adb)
    monkeypatch.setattr(d, "_sr", lambda *a, **k: started.append(a) or sp.CompletedProcess(a, 0, "", ""))
    with pytest.raises(DriverError) as e:
        d.start("com.example", "192.168.0.5:5555")
    assert "SDK 36" in str(e.value) and "speedrun-agent-ab12.apk" in str(e.value) and "Maestro" in str(e.value)
    assert started == []
    phone.update({"getprop ro.build.version.sdk": b"30", "ls /data/local/tmp/speedrun-agent-*.apk 2>/dev/null": b"",
                  "pidof dev.mobile.maestro dev.mobile.maestro.test": b""})
    d.start("com.example", "192.168.0.5:5555")  # Android 11, nothing to change: allowed
    assert started
    started.clear()
    phone["getprop ro.build.version.sdk"] = b"36"
    d.allow_changes = ["192.168.0.5:5555"]  # a listed serial: no guard
    d.start("com.example", "192.168.0.5:5555")
    assert started


def test_shared_device_guard_refuses_any_animation_scale_write(tmp_path, monkeypatch):
    """Speedrun writes the scales at session start: only a no-op (normal on a device at 1) may run."""
    import subprocess as sp
    phone = {"getprop ro.build.version.sdk": b"30", "settings get global window_animation_scale": b"1.0",
             "settings get global transition_animation_scale": b"1.0",
             "settings get global animator_duration_scale": b"0.5"}
    adb = lambda *a: sp.CompletedProcess(a, 0, phone.get(" ".join(a[1:]), b""), b"")
    started = []
    for animations, scale, ok in (("normal", b"0.5", False), ("off", b"1", False), ("normal", b"1", True)):
        phone["settings get global animator_duration_scale"] = scale
        d = SpeedrunDriver("/bin/true", tmp_path / "h", animations=animations)
        monkeypatch.setattr(d, "_adb", adb)
        monkeypatch.setattr(d, "_sr", lambda *a, **k: started.append(a) or sp.CompletedProcess(a, 0, "", ""))
        if ok:
            d.start("com.example", "192.168.0.5:5555")
        else:
            with pytest.raises(DriverError, match="animation"):
                d.start("com.example", "192.168.0.5:5555")
    assert len(started) == 1

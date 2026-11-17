"""Drives a device through upstream Speedrun's session commands (`session start`, `look`, `do`).

Speedrun keeps its state under $HOME/.speedrun. We run it with HOME set to a folder of our own,
so its sessions, logs and update checks stay out of the user's real home.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import yaml

from .base import CommandResult, Driver, DriverError, Element, Screen, changes_allowed

_LINE = re.compile(r'^(@\d+)\s+(\S+)(.*)$')
_TOKEN = re.compile(r'\s*(?:"((?:[^"\\]|\\.)*)"|#(\S+)|(\w+)="(?:[^"\\]|\\.)*"|(\S+))')
_ESC = re.compile(r'\\(.)')


def _unquote(s: str) -> str:
    return _ESC.sub(lambda m: "\n" if m.group(1) == "n" else m.group(1), s)


def parse_look(text: str) -> Screen:
    """Parse `speedrun look`: a header line, then `@ref Kind "text" #id key="v" state...` per element."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    header = lines[0] if lines and not lines[0].startswith("@") else ""
    elements = []
    for ln in lines:
        m = _LINE.match(ln)
        if not m:
            continue
        ref, kind, rest = m.groups()
        label, id_, state = "", "", []
        for q, hid, _attr, word in _TOKEN.findall(rest):
            if q and not label:
                label = _unquote(q)
            elif hid:
                id_ = hid
            elif word:
                state.append(word)
        elements.append(Element(ref, kind, label, id_, tuple(state)))
    return Screen(elements, header)


def find_speedrun(repo_root: Path | None = None) -> str | None:
    """$JOURNEYS_SPEEDRUN, then third_party/speedrun/<version>/speedrun (newest), then PATH.

    A PATH speedrun under ~/.speedrun is skipped: that folder may hold a patched build."""
    env = os.environ.get("JOURNEYS_SPEEDRUN")
    if env:
        return env
    root = repo_root or Path(__file__).resolve().parents[2]
    found = sorted((root / "third_party" / "speedrun").glob("*/speedrun"))
    if found:
        return str(found[-1])
    on_path = shutil.which("speedrun")
    if on_path and Path("~/.speedrun").expanduser() not in Path(on_path).resolve().parents:
        return on_path
    return None


class SpeedrunDriver(Driver):
    def __init__(self, binary: str | None, home: Path, timeout_s: int = 120, animations: str = "normal",
                 allow_changes="emulator"):
        if not binary:
            raise DriverError("speedrun not found: run `journeys setup`, or set JOURNEYS_SPEEDRUN")
        if animations not in ("off", "fast", "normal", "default"):
            raise DriverError(f"animations is off, fast, normal or default, not {animations}")
        self.bin, self.home, self.timeout, self.animations = binary, Path(home), timeout_s, animations
        self.allow_changes = allow_changes  # pack.yaml devices.allow_settings_changes
        self.home.mkdir(parents=True, exist_ok=True)
        self.serial: str | None = None
        self.app = ""
        self.started = False

    def _env(self) -> dict[str, str]:
        env = {**os.environ, "HOME": str(self.home), "SPEEDRUN_NO_UPDATE_CHECK": "1"}
        keys = Path("~/.android").expanduser()  # adb started from here still needs the user's keys
        if keys.is_dir():
            env.setdefault("ADB_VENDOR_KEYS", str(keys))
        return env

    def _sr(self, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
        try:
            return subprocess.run([self.bin, *args], input=stdin, capture_output=True, text=True,
                                  env=self._env(), timeout=self.timeout)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise DriverError(f"speedrun {args[0]}: {e}") from None

    def _adb(self, *args: str) -> subprocess.CompletedProcess:
        serial = ["-s", self.serial] if self.serial else []
        try:
            return subprocess.run(["adb", *serial, *args], capture_output=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise DriverError(f"adb {args[0]}: {e}") from None

    def _guard_shared_device(self) -> None:
        """Speedrun's session start changes a device in ways a shared phone must not see: it turns on
        its accessibility service on Android 13+, deletes other speedrun-agent builds from
        /data/local/tmp and force-stops a running Maestro driver. Refuse instead."""
        sh = lambda cmd: self._adb("shell", cmd).stdout.decode(errors="replace").strip()  # read-only
        why = []
        sdk = sh("getprop ro.build.version.sdk")
        if sdk.isdigit() and int(sdk) >= 33:
            why.append(f"Android SDK {sdk}: Speedrun turns on its accessibility service for the session")
        if apks := sh("ls /data/local/tmp/speedrun-agent-*.apk 2>/dev/null"):
            why.append(f"Speedrun deletes other agent builds: {' '.join(apks.split())}")
        if sh("pidof dev.mobile.maestro dev.mobile.maestro.test"):
            why.append("a Maestro driver is running; Speedrun would stop it")
        if why:
            raise DriverError("this device is not allowed to change (pack.yaml devices.allow_settings_changes): "
                              + "; ".join(why))

    def start(self, app_id: str, serial: str | None = None) -> None:
        self.app, self.serial = app_id, serial
        if not changes_allowed(self.allow_changes, self, serial):
            self._guard_shared_device()
        # "normal" by default: Speedrun writes the animation scales at 1, which a shared device keeps
        # anyway; "off" (Speedrun's own default) is faster but changes a device setting for the run.
        args = ["session", "start", "--app", app_id, "--platform", "android", "--animations", self.animations]
        if serial:
            args += ["--device", serial]
        p = self._sr(*args)
        if p.returncode != 0:
            raise DriverError(f"speedrun session start failed: {(p.stderr or p.stdout).strip()[-500:]}")
        self.started = True

    def look(self) -> Screen:
        p = self._sr("look")
        if p.returncode != 0:
            raise DriverError(f"speedrun look failed: {(p.stderr or p.stdout).strip()[-300:]}")
        return parse_look(p.stdout)

    def do(self, commands: list) -> CommandResult:
        flow = yaml.safe_dump(commands, sort_keys=False, allow_unicode=True)
        p = self._sr("do", "-", stdin=flow)
        out = (p.stdout + p.stderr).strip()
        if p.returncode == 0:
            return CommandResult(True, out)
        failed = [ln for ln in out.splitlines() if ln.startswith("FAILED") or ln.startswith("  ")]
        return CommandResult(False, "\n".join(failed[:4]) or out[-500:])

    def screenshot(self, path: Path) -> Path | None:
        p = self._adb("exec-out", "screencap", "-p")
        if p.returncode != 0 or not p.stdout.startswith(b"\x89PNG"):
            return None
        path.write_bytes(p.stdout)
        return path

    def app_alive(self) -> bool:
        return self._adb("shell", "pidof", self.app).returncode == 0

    def foreground_app(self) -> str | None:
        out = self._adb("shell", "dumpsys", "window").stdout.decode(errors="replace")  # read-only
        m = re.search(r"mCurrentFocus=Window\{\S+ \S+ ([\w.]+)/", out) or re.search(r"mFocusedApp=.*? ([\w.]+)/", out)
        return m.group(1) if m else None

    def crash_log(self) -> str:
        p = self._adb("logcat", "-d", "-b", "crash", "-t", "60")  # read-only: no -c, no -G
        return p.stdout.decode(errors="replace")[-4000:] if p.returncode == 0 else ""

    def is_emulator(self) -> bool:
        if (self.serial or "").startswith("emulator-"):
            return True
        return self._adb("shell", "getprop", "ro.kernel.qemu").stdout.strip() == b"1"

    def global_setting(self, name: str, value: str | None = None) -> str:
        old = self._adb("shell", "settings", "get", "global", name).stdout.decode().strip()
        if value is not None:
            if self._adb("shell", "settings", "put", "global", name, value).returncode != 0:
                raise DriverError(f"could not set {name}")
        return old

    def stop(self) -> None:
        if self.started:
            self._sr("session", "stop")
            self.started = False

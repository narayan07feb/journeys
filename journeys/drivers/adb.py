"""What every real Android driver reads through adb, read-only: screenshot, process, focus, crashes."""
from __future__ import annotations

import subprocess
from pathlib import Path

from .base import Driver, DriverError, focused_package


class AdbDriver(Driver):
    serial: str | None = None
    app: str = ""

    def _adb(self, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
        serial = ["-s", self.serial] if self.serial else []
        try:
            return subprocess.run(["adb", *serial, *args], capture_output=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise DriverError(f"adb {args[0]}: {e}") from None

    def screenshot(self, path: Path) -> Path | None:
        p = self._adb("exec-out", "screencap", "-p")
        if p.returncode != 0 or not p.stdout.startswith(b"\x89PNG"):
            return None
        path.write_bytes(p.stdout)
        return path

    def app_alive(self) -> bool:
        return self._adb("shell", "pidof", self.app).returncode == 0

    def foreground_app(self) -> str | None:
        return focused_package(self._adb("shell", "dumpsys", "window").stdout.decode(errors="replace"))

    def crash_log(self) -> str:
        p = self._adb("logcat", "-d", "-b", "crash", "-t", "60")  # read-only: no -c, no -G
        return p.stdout.decode(errors="replace")[-4000:] if p.returncode == 0 else ""

    def is_emulator(self) -> bool:
        if (self.serial or "").startswith("emulator-"):
            return True
        return self._adb("shell", "getprop", "ro.kernel.qemu").stdout.strip() == b"1"

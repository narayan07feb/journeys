"""Drivers: each one reads the screen and runs flow commands on a device. Pick one per pack."""
from __future__ import annotations

import shutil

DRIVERS = ("maestro", "speedrun", "fake")  # Journeys drives through the team's own tool


def availability() -> dict[str, str | None]:
    """Each driver: None when it can run here, else what is missing."""
    from .speedrun import find_speedrun
    adb = None if shutil.which("adb") else "adb is not on PATH"
    return {
        "maestro": adb or (None if shutil.which("maestro") else "maestro is not on PATH"),
        "speedrun": adb or (None if find_speedrun() else "speedrun not found: run `journeys setup`"),
        "fake": None,
    }

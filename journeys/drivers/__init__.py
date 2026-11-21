"""Drivers: each one reads the screen and runs flow commands on a device. Pick one per pack."""
from __future__ import annotations

import shutil

DRIVERS = ("maestro", "speedrun", "appium", "fake")  # Journeys drives through the team's own tool
TOOLS = ", ".join(d for d in DRIVERS if d != "fake")  # the team tools, for messages: "maestro, speedrun, appium"


def availability(only: str | None = None) -> dict[str, str | None]:
    """Each driver (or only one): None when it can run here, else what is missing. The appium
    check asks the server over the network, so a run checks only the driver it uses."""
    from .appium import reachable
    from .speedrun import find_speedrun
    adb = None if shutil.which("adb") else "adb is not on PATH"
    checks = {
        "maestro": lambda: adb or (None if shutil.which("maestro") else "maestro is not on PATH"),
        "speedrun": lambda: adb or (None if find_speedrun() else "speedrun not found: run `journeys setup`"),
        "appium": lambda: adb or reachable(),
        "fake": lambda: None,
    }
    return {k: check() for k, check in checks.items() if only in (None, k)}

"""Which UI test framework a project already uses, so a new pack picks the same one.

Read-only: it looks at file names and build files under the project, nothing else.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .drivers import DRIVERS

_SKIP = {".git", "build", ".gradle", "node_modules", ".idea", ".venv", "third_party", ".cxx"}
_ESPRESSO = re.compile(r"androidx\.test\.espresso|espresso-core|ui-test-junit4|androidx\.compose\.ui:ui-test")
_APPIUM = re.compile(r"(^|[/\\])(wdio\.conf\.[jt]s|appium\.config\.\w+|\.appiumrc(\.\w+)?)$")
_APP_ID = re.compile(r"applicationId\s*=?\s*[\"']([\w.]+)[\"']")


@dataclass
class Found:
    framework: str     # maestro, espresso, appium, ...
    reason: str        # the file that shows it


def _files(root: Path, depth: int = 6):
    root = root.resolve()
    for dirpath, dirs, files in os.walk(root):
        rel = Path(dirpath).relative_to(root)
        dirs[:] = [] if len(rel.parts) >= depth else sorted(d for d in dirs if d not in _SKIP)
        for f in sorted(files):
            yield Path(dirpath) / f


def scan(project: Path) -> tuple[list[Found], str | None]:
    """Frameworks found (strongest first) and the app id from the Gradle build, if any."""
    if not project.is_dir():
        raise FileNotFoundError(f"project folder {project} not found")
    found: dict[str, Found] = {}
    app_id = None
    for f in _files(project):
        rel = f.relative_to(project.resolve()).as_posix()
        if ".maestro" in f.parts and f.suffix in (".yaml", ".yml"):
            found.setdefault("maestro", Found("maestro", rel))
        elif _APPIUM.search(rel):
            found.setdefault("appium", Found("appium", rel))
        elif f.name in ("build.gradle", "build.gradle.kts", "libs.versions.toml"):
            try:
                text = f.read_text(errors="replace")
            except OSError:
                continue  # a broken link or an unreadable file: not evidence either way
            if _ESPRESSO.search(text):
                found.setdefault("espresso", Found("espresso", rel))
            m = _APP_ID.search(text)
            app_id = app_id or (m.group(1) if m else None)
        elif f.suffix in (".yaml", ".yml"):
            try:
                if f.stat().st_size > 200_000:
                    continue
                head = f.read_text(errors="replace")[:2000]
            except OSError:
                continue
            if re.search(r"^appId:", head, re.M) and "\n---" in head:
                found.setdefault("maestro", Found("maestro", rel))
    order = ["maestro", "appium", "espresso"]
    return [found[k] for k in order if k in found], app_id


def pick(found: list[Found]) -> tuple[str, str]:
    """The driver for a new pack, and why. A framework without a step-by-step driver yet gets
    uiautomator (no extra tools), and the reason says so."""
    for f in found:
        if f.framework in DRIVERS:
            return f.framework, f"{f.reason} shows the project uses {f.framework}"
    if found:
        names = ", ".join(f"{f.framework} ({f.reason})" for f in found)
        return "uiautomator", f"found {names}: no Journeys driver for it yet, so uiautomator (adb only)"
    return "uiautomator", "no UI test framework found: uiautomator (adb only)"


def init_pack(dest: Path, project: Path | None, name: str, app: str | None) -> list[str]:
    """Write a new, empty pack. Returns what it did, line by line."""
    if (dest / "pack.yaml").exists():
        raise FileExistsError(f"{dest}/pack.yaml already exists")
    found, app_id = scan(project) if project else ([], None)
    driver, why = pick(found)
    app = app or app_id
    if not app:
        raise ValueError("no app id: pass --app <package> (none found in the project's Gradle files)")
    if not re.fullmatch(r"[A-Za-z][\w]*(\.[A-Za-z_][\w]*)+", app):
        raise ValueError(f"{app!r} is not an Android package name (like com.example.app)")
    for sub in ("map", "journeys", "drafts"):
        (dest / sub).mkdir(parents=True, exist_ok=True)
    head = {"name": name, "app": app, "driver": driver}
    if project:
        head["project"] = os.path.relpath(project.resolve(), dest.resolve())
    (dest / "pack.yaml").write_text(f"# driver {driver}: {why}\n" + yaml.safe_dump(head, sort_keys=False)
                                    + "devices:\n  allow_settings_changes: emulator\n")
    (dest / "map" / "screens.yaml").write_text("screens:\n")
    (dest / "map" / "edges.yaml").write_text("edges:\n")
    (dest / "sets.yaml").write_text("sets:\n")
    return [f"pack {name} for {app} in {dest}", f"driver {driver}: {why}",
            "next: add screens to map/screens.yaml, then `journeys new <name>` for a first draft journey"]

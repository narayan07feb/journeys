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


def scan(project: Path, strict: bool = False) -> tuple[list[Found], str | None]:
    """Frameworks found (strongest first) and the app id from the Gradle build, if any."""
    if not project.is_dir():
        raise FileNotFoundError(f"project folder {project} not found")
    found: dict[str, Found] = {}
    ids: dict[str, str] = {}  # applicationId -> the build file that sets it
    for f in _files(project):
        rel = f.relative_to(project.resolve()).as_posix()
        if ".maestro" in Path(rel).parts and f.suffix in (".yaml", ".yml"):
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
            code = re.sub(r"/\*.*?\*/|//[^\n]*", "", text, flags=re.S)  # commented-out lines are not the app
            m = _APP_ID.search(code)
            if m and not re.search(r"com\.android\.(library|test)|androidx\.benchmark|android\.test\b", text):
                ids.setdefault(m.group(1), rel)  # an app module, not a test, library or benchmark one
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
    if len(ids) > 1 and not strict:
        return [found[k] for k in order if k in found], None
    if len(ids) > 1:
        raise ValueError("several app ids in the project (" + ", ".join(f"{i} in {f}" for i, f in ids.items())
                         + "): pass --app <package>")
    return [found[k] for k in order if k in found], next(iter(ids), None)


def pick(found: list[Found]) -> tuple[str | None, str]:
    """The driver for a new pack, and why; None when the project's framework has no Journeys
    driver yet (then the team names one with --driver)."""
    for f in found:
        if f.framework in DRIVERS:
            return f.framework, f"{f.reason} shows the project uses {f.framework}"
    if found:
        names = ", ".join(f"{f.framework} ({f.reason})" for f in found)
        return None, f"found {names}: Journeys has no driver for it yet"
    return None, "no UI test framework found in the project"


def init_pack(dest: Path, project: Path | None, name: str, app: str | None,
              driver: str | None = None) -> list[str]:
    """Write a new, empty pack. Returns what it did, line by line."""
    files = [dest / "pack.yaml", dest / "map" / "screens.yaml", dest / "map" / "edges.yaml", dest / "sets.yaml"]
    there = [str(f) for f in files if f.exists()]
    if there:
        raise FileExistsError(f"{', '.join(there)} already exist(s): init writes a new pack only")
    found, app_id = scan(project, strict=not app) if project else ([], None)
    detected, why = pick(found)
    if driver:
        why = f"chosen with --driver ({why})" if detected != driver else why
    driver = driver or detected
    if driver not in DRIVERS:
        raise ValueError(f"{why}: pass --driver {' | '.join(d for d in DRIVERS if d != 'fake')}"
                         if driver is None else f"driver {driver} is not one of {', '.join(DRIVERS)}")
    app = app or app_id
    if not app:
        raise ValueError("no app id: pass --app <package>" + (" (none found in the project's Gradle files)"
                                                              if project else ""))
    if not re.fullmatch(r"[A-Za-z][\w]*(\.[A-Za-z_][\w]*)+", app):
        raise ValueError(f"{app!r} is not an Android package name (like com.example.app)")
    for sub in ("map", "journeys", "drafts"):
        (dest / sub).mkdir(parents=True, exist_ok=True)
    head = {"name": name, "app": app, "driver": driver}
    if project:
        head["project"] = os.path.relpath(project.resolve(), dest.resolve())
    texts = [f"# driver {driver}: {why}\n" + yaml.safe_dump(head, sort_keys=False)
             + "devices:\n  allow_settings_changes: emulator\n", "screens:\n", "edges:\n", "sets:\n"]
    for f, text in zip(files, texts):
        with open(f, "x") as out:  # never over a file that appeared meanwhile
            out.write(text)
    return [f"pack {name} for {app} in {dest}", f"driver {driver}: {why}",
            "next: add screens to map/screens.yaml, then `journeys new <name>` for a first draft journey"]

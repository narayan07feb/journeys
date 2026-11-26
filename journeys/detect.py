"""Which UI test framework a project already uses: a hint when init asks the team for its driver.

Read-only: it looks at file names and build files under the project, nothing else.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

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
    """The driver the project's tests suggest, and why: a hint for the user's answer, never the
    choice (init and every device command ask the user)."""
    for f in found:
        if f.framework in DRIVERS:
            return f.framework, f"{f.reason} shows the project uses {f.framework}"
    if found:
        names = ", ".join(f"{f.framework} ({f.reason})" for f in found)
        return None, f"found {names}: Journeys has no driver for it yet"
    return None, "no UI test framework found in the project"


def _refuse_existing(files: list[Path]) -> None:
    there = [str(f) for f in files if f.exists()]
    if there:
        raise FileExistsError(f"{', '.join(there)} already exist(s): init writes a new pack only")


HOOK_NAME = re.compile(r"[a-z][a-z0-9_]*")

STUB = """#!/bin/sh
# {name}: sets something up on the team's server for a journey (written by `journeys init`).
# Journeys runs it in the pack folder, with the step's args as one JSON text in $1 and
# JOURNEYS_APP, JOURNEYS_SERIAL and JOURNEYS_PACK set. Read keys and tokens from env vars that
# pack.yaml declares under env: (never write them here). Exit 0 when done; otherwise print the
# reason on stderr and exit non-zero. The journeys agent skill can write this script with you.
echo "hooks/{name}.sh is not written yet" >&2
exit 1
"""


def init_pack(dest: Path, project: Path | None, name: str, app: str | None,
              driver: str | None = None, hooks: list[str] | Callable[[], list[str]] = ()) -> list[str]:
    """Write a new, empty pack. Returns what it did, line by line. `hooks`: names of server-setup
    scripts the team named (or a question that returns them, asked once every other check passed);
    each gets a hooks: entry and a stub script that fails until it is written."""
    files = [dest / "pack.yaml", dest / "map" / "screens.yaml", dest / "map" / "edges.yaml", dest / "sets.yaml"]
    _refuse_existing(files)
    found, app_id = scan(project, strict=not app) if project else ([], None)
    if driver not in DRIVERS:  # the team's answer only: the project's tests are a hint, never the choice
        raise ValueError(f"which tool does your team use to drive the device? ask the team, then pass --driver "
                         f"{' | '.join(d for d in DRIVERS if d != 'fake')} (hint: {pick(found)[1]})"
                         if not driver else f"driver {driver} is not one of {', '.join(DRIVERS)}")
    why = "the team's choice" + (f" (project hint: {pick(found)[1]})" if project else "")
    app = app or app_id
    if not app:
        raise ValueError("no app id: pass --app <package>" + (" (none found in the project's Gradle files)"
                                                              if project else ""))
    if not re.fullmatch(r"[A-Za-z][\w]*(\.[A-Za-z_][\w]*)+", app):
        raise ValueError(f"{app!r} is not an Android package name (like com.example.app)")
    hooks = list(dict.fromkeys(hooks() if callable(hooks) else hooks))
    bad = [h for h in hooks if not HOOK_NAME.fullmatch(h)]
    if bad:
        raise ValueError(f"hook names are lowercase words with underscores, like set_flag, not {', '.join(bad)}")
    files += [dest / "hooks" / f"{h}.sh" for h in hooks]
    _refuse_existing(files)
    for sub in ("map", "journeys", "drafts") + (("hooks",) if hooks else ()):
        (dest / sub).mkdir(parents=True, exist_ok=True)
    head = {"name": name, "app": app, "driver": driver}
    if project:
        head["project"] = os.path.relpath(project.resolve(), dest.resolve())
    texts = [f"# driver {driver}: {why}\n" + yaml.safe_dump(head, sort_keys=False)
             + "devices:\n  allow_settings_changes: emulator\n"
             + (yaml.safe_dump({"hooks": {h: [f"hooks/{h}.sh"] for h in hooks}}, sort_keys=False) if hooks else ""),
             "screens:\n", "edges:\n", "sets:\n"] + [STUB.format(name=h) for h in hooks]
    for f, text in zip(files, texts):
        with open(f, "x") as out:  # never over a file that appeared meanwhile
            out.write(text)
        if f.suffix == ".sh":
            f.chmod(0o755)
    return [f"pack {name} for {app} in {dest}", f"driver {driver}: {why}",
            *(f"hook {h}: hooks/{h}.sh is a stub that fails until it is written (the journeys skill can "
              f"write it with you)" for h in hooks),
            "next: add screens to map/screens.yaml, then `journeys new <name>` for a first draft journey"]

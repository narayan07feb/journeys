"""Writing journeys: a new one starts as a draft in <pack>/drafts/, and is promoted into journeys/."""
from __future__ import annotations

import re

import yaml

from .journey import Journey
from .pack import Pack, PackError

_NAME = re.compile(r"[a-z0-9][a-z0-9-]*")


def new_draft(pack: Pack, name: str, start: str | None) -> str:
    if not _NAME.fullmatch(name):
        raise PackError(f"journey names are lowercase words with dashes, not {name!r}")
    if name in pack.journeys or name in pack.drafts:
        raise PackError(f"journey {name} already exists")
    start = start or next(iter(pack.map.screens), None)
    if start not in pack.map.screens:
        raise PackError(f"start screen {start} is not in map/screens.yaml" if start else
                        "the map has no screens yet: add one to map/screens.yaml first")
    f = pack.root / "drafts" / f"{name}.yaml"
    f.parent.mkdir(exist_ok=True)
    try:
        with open(f, "x") as out:  # never over a file that is there (it may not load: still the user's)
            out.write(yaml.safe_dump({"name": name, "tags": [], "steps": [{"expect": start}]}, sort_keys=False))
    except FileExistsError:
        raise PackError(f"{f} already exists") from None
    return str(f)


def same_steps(a: Journey, b: Journey) -> bool:
    return (a.launch, a.clear_state, [(s.kind, s.arg) for s in a.steps]) == \
        (b.launch, b.clear_state, [(s.kind, s.arg) for s in b.steps])


def promote_journey(pack: Pack, name: str) -> str:
    """Move a valid draft into journeys/. Refuses errors, duplicates and shared draft files."""
    d = pack.drafts.get(name)
    if not d:
        broken = [e for e in pack.errors if e.startswith(str(pack.root / "drafts" / name) + ".")]
        raise PackError("\n".join(broken) if broken else
                        f"no draft named {name} (drafts: {', '.join(pack.drafts) or 'none'})")
    errs = [e for e in pack.errors if e.startswith(f"{d.file}:") or e.startswith(f"{d.file} ")]
    if errs:
        raise PackError(f"draft {name} has errors; fix them first:\n" + "\n".join(errs))
    if sum(j.file == d.file for j in pack.drafts.values()) > 1:
        raise PackError(f"{d.file} holds more than one draft: give {name} its own file first")
    twin = next((j.name for j in pack.journeys.values() if same_steps(j, d)), None)
    if twin:
        raise PackError(f"draft {name} has the same steps as journey {twin}: a duplicate, not promoted")
    dest = pack.root / "journeys" / d.file.relative_to(pack.root / "drafts")
    if dest.exists():
        raise PackError(f"{dest} already exists")
    dest.parent.mkdir(parents=True, exist_ok=True)
    d.file.rename(dest)
    return str(dest)

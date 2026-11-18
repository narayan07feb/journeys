"""Journeys: named, ordered steps through the app map, with checks."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import yamlio
from .actions import BARE, FOREIGN

KINDS = ("expect", "goto", "see", "not_see", "do", "input", "hook", "event")


@dataclass
class Step:
    kind: str
    arg: object
    line: int = 0

    def describe(self) -> str:
        return f"{self.kind}: {self.arg}"


@dataclass
class Journey:
    name: str
    file: Path
    steps: list[Step]
    tags: list[str] = field(default_factory=list)
    launch: bool = True
    clear_state: bool = False
    line: int = 1


def load_journeys(path: Path) -> tuple[list[Journey], list[str]]:
    """A file holds one journey (a map) or several (a list of maps)."""
    errors: list[str] = []
    try:
        raw = yamlio.load(path)
    except Exception as e:  # yaml.YAMLError and OSError both mean the file cannot be used
        return [], [f"{path}: {e}"]
    items = raw if isinstance(raw, list) else [raw]
    out = []
    for item in items:
        if not isinstance(item, dict) or not item.get("name"):
            errors.append(f"{path}: a journey is a map with a name")
            continue
        start = item.get("start") or {}
        if not isinstance(start, dict) or set(start) - {"launch", "clear_state", yamlio.LINE} or not all(
                isinstance(start.get(k, True), bool) for k in ("launch", "clear_state")):
            errors.append(f"{yamlio.where(path, item)}: start is a map of launch and clear_state (true or false), not {yamlio.strip_lines(start)!r}")
            start = {}
        steps, ok = [], True
        tags = item.get("tags") or []
        if not isinstance(tags, list):
            errors.append(f"{yamlio.where(path, item)}: tags is a list, like [smoke], not {tags!r}")
            tags = []
        if not isinstance(item.get("steps") or [], list):
            errors.append(f"{yamlio.where(path, item)}: steps is a list of steps")
            continue
        for n, s in enumerate(item.get("steps") or [], 1):
            body = {k: v for k, v in s.items() if k != yamlio.LINE} if isinstance(s, dict) else {}
            if len(body) != 1 or next(iter(body)) not in KINDS:
                word = next(iter(body), "") if len(body) == 1 else ""
                hint = ""
                if word in FOREIGN:
                    neutral = FOREIGN[word]
                    if neutral in ("see", "not_see"):
                        hint = f" ({word} is another tool's word: write `{neutral}: ...`)"
                    elif neutral in BARE:
                        hint = f" ({word} is another tool's word: write `do: [{neutral}]`)"
                    else:
                        hint = f" ({word} is another tool's word: write `do: [{{{neutral}: ...}}]`)"
                at = yamlio.where(path, s) if isinstance(s, dict) else f"{yamlio.where(path, item)} step {n}"
                errors.append(f"{at}: a step is one of {', '.join(KINDS)}{hint}")
                continue  # the journey still loads, so its other steps are checked too (its file has errors)
            kind, arg = next(iter(body.items()))
            steps.append(Step(kind, yamlio.strip_lines(arg), s.get(yamlio.LINE, 0)))
        if not item.get("steps"):
            errors.append(f"{yamlio.where(path, item)}: journey {item['name']} has no steps")
            ok = False
        if ok:
            out.append(Journey(
                str(item["name"]), path, steps, [str(t) for t in tags],
                bool(start.get("launch", True)), bool(start.get("clear_state", False)),
                item.get(yamlio.LINE, 1),
            ))
    return out, errors

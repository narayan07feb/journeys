"""Run history, failures and ids, kept as JSON files in a state folder outside the pack."""
from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path


def default_state_dir(pack_name: str) -> Path:
    base = os.environ.get("JOURNEYS_STATE")
    if base:
        return Path(base).expanduser()
    return Path("~/.local/state/journeys").expanduser() / pack_name


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str) + "\n")
    os.replace(tmp, path)


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


class Run:
    def __init__(self, store: "Store", run_id: str, data: dict):
        self.store, self.id, self.data = store, run_id, data
        self.dir = store.root / "runs" / run_id

    def save(self) -> None:
        write_json(self.dir / "run.json", self.data)


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def next_id(self, prefix: str) -> str:
        path = self.root / "counters.json"
        counters = read_json(path, {})
        counters[prefix] = counters.get(prefix, 0) + 1
        write_json(path, counters)
        return f"{prefix}-{counters[prefix]}"

    def new_run(self, **meta) -> Run:
        run_id = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
        run = Run(self, run_id, {"id": run_id, "started": time.time(), "status": "running", "journeys": [], **meta})
        run.dir.mkdir(parents=True)
        run.save()
        return run

    def runs(self) -> list[dict]:
        out = [read_json(p, None) for p in sorted((self.root / "runs").glob("*/run.json"))]
        return [r for r in out if r]

    def run(self, run_id: str) -> dict | None:
        return read_json(self.root / "runs" / run_id / "run.json", None)

    # failures (F-n) and proposals (P-n): lists of records in one JSON file each
    def _items(self, kind: str) -> list[dict]:
        return read_json(self.root / f"{kind}.json", [])

    def _add(self, kind: str, prefix: str, item: dict) -> str:
        new_id = self.next_id(prefix)
        items = self._items(kind)
        items.append({"id": new_id, "time": time.time(), **item})
        write_json(self.root / f"{kind}.json", items)
        return new_id

    def _get(self, kind: str, item_id: str) -> dict | None:
        return next((f for f in self._items(kind) if f["id"] == item_id), None)

    def _update(self, kind: str, item_id: str, **fields) -> dict:
        items = self._items(kind)
        for it in items:
            if it["id"] == item_id:
                it.update(fields)
                write_json(self.root / f"{kind}.json", items)
                return it
        raise KeyError(item_id)

    def add_failure(self, failure: dict) -> str:
        return self._add("failures", "F", failure)

    def failures(self) -> list[dict]:
        return self._items("failures")

    def failure(self, fid: str) -> dict | None:
        return self._get("failures", fid)

    def update_failure(self, fid: str, **fields) -> dict:
        return self._update("failures", fid, **fields)

    def add_proposal(self, proposal: dict) -> str:
        return self._add("proposals", "P", {"status": "proposed", **proposal})

    def proposals(self) -> list[dict]:
        return self._items("proposals")

    def proposal(self, pid: str) -> dict | None:
        return self._get("proposals", pid)

    def update_proposal(self, pid: str, **fields) -> dict:
        return self._update("proposals", pid, **fields)

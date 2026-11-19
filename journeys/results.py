"""Run history, failures and ids, kept as JSON files in a state folder outside the pack."""
from __future__ import annotations

import json
import os
import re
import secrets
import time
from pathlib import Path


def default_state_dir(pack_name: str) -> Path:
    base = os.environ.get("JOURNEYS_STATE")
    if base:
        return Path(base).expanduser()
    return Path("~/.local/state/journeys").expanduser() / pack_name


_SECRETS: list[str] = []


def set_secrets(values) -> None:
    """Env values (passwords, tokens) the run knows; every string written later is masked."""
    _SECRETS[:] = sorted({str(v) for v in values if len(str(v)) >= 3}, key=len, reverse=True)


# Fields Journeys itself fills with words it reads back (status, ids, names): a secret like "pass"
# must not turn "passed" into "•••ed". Typed values never land in them.
_PLAIN = {"status", "kind", "id", "failure", "failures", "changes", "proposal", "run", "target", "driver",
          "serial", "pack", "journey", "file", "line", "index", "started", "ended", "dka"}


def mask(obj):
    """obj with each secret, raw or regex-escaped, replaced by •••, in every string inside it, except
    keys and the _PLAIN fields."""
    if isinstance(obj, str):
        from .actions import escape
        for s in _SECRETS:
            obj = obj.replace(s, "•••").replace(escape(s), "•••")
        return obj
    if isinstance(obj, dict):
        return {k: v if k in _PLAIN and not isinstance(v, (dict, list)) else mask(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [mask(v) for v in obj]
    return obj


def write_json(path: Path, data) -> None:
    data = mask(data)  # nothing reaches results, proposals or the live page unmasked
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

    @staticmethod
    def make_run_id() -> str:
        return time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)

    def new_run(self, run_id: str | None = None, **meta) -> Run:
        run_id = run_id or self.make_run_id()
        if not re.fullmatch(r"[0-9A-Za-z-]{1,64}", run_id):
            raise ValueError(f"bad run id {run_id!r}")
        run = Run(self, run_id, {"id": run_id, "started": time.time(), "status": "running", "journeys": [], **meta})
        try:
            run.dir.mkdir(parents=True)
        except FileExistsError:
            raise ValueError(f"run {run_id} already exists") from None
        run.save()
        return run

    def runs(self) -> list[dict]:
        out = [read_json(p, None) for p in (self.root / "runs").glob("*/run.json")]
        return sorted((r for r in out if r), key=lambda r: r.get("started", 0))

    def history(self, last: int = 10) -> dict[str, dict]:
        """Per journey: its last results (oldest first) and whether it is flaky.

        Flaky: in its last `last` finished results it both passed and failed, and changed between
        them at least twice (pass, fail, pass), so one real break that stays broken is not flaky."""
        seq: dict[str, list[str]] = {}
        for r in self.runs():
            for j in r.get("journeys", []):
                if j.get("status") in ("passed", "failed"):
                    seq.setdefault(j["name"], []).append(j["status"])
        out = {}
        for name, results in seq.items():
            tail = results[-last:]
            flips = sum(a != b for a, b in zip(tail, tail[1:]))
            out[name] = {"results": tail, "passed": tail.count("passed"), "failed": tail.count("failed"),
                         "flaky": flips >= 2}
        return out

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

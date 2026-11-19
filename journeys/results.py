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
    return Path("~/.local/state/journeys").expanduser() / re.sub(r"[^\w.-]+|^\.+", "_", pack_name)  # never ../


_SECRETS: list[str] = []


RUN_ID = re.compile(r"[0-9A-Za-z-]{1,64}")


def current_actor() -> str:
    """Who is working: JOURNEYS_ACTOR (a name, or an MCP client's), else the agent whose shell runs
    us (AI_AGENT, CLAUDECODE), else a person. The tracker counts work per actor."""
    if os.environ.get("JOURNEYS_ACTOR"):
        return os.environ["JOURNEYS_ACTOR"][:64]
    if os.environ.get("AI_AGENT"):
        return os.environ["AI_AGENT"].split("_")[0][:64] or "agent"
    return "claude-code" if os.environ.get("CLAUDECODE") else "person"

def set_secrets(values) -> None:
    """Env values (passwords, tokens) the run knows; every string written later is masked."""
    raw = {str(v) for v in values if len(str(v)) >= 3}
    # the forms a value takes in hook arguments, tool output and Python messages: JSON-escaped
    # ('my\\"pw', 'p\\u00e4ss') and repr-escaped ("it\\'s") as well as raw
    forms = {f for s in raw for f in (s, json.dumps(s)[1:-1], json.dumps(s, ensure_ascii=False)[1:-1],
                                       repr(s)[1:-1])}
    _SECRETS[:] = sorted(forms, key=len, reverse=True)


# Fields Journeys itself fills with words it reads back (status, ids, names): a secret like "pass"
# must not turn "passed" into "•••ed". Typed values never land in them.
_PLAIN = {"status", "kind", "id", "name", "actor", "action", "failure", "failures", "changes", "proposal", "run", "target", "driver",
          "serial", "pack", "journey", "file", "line", "index", "started", "ended", "dka"}


def mask(obj, plain: bool = True):
    """obj with each secret, raw or regex-escaped, replaced by •••, in every string inside it, except
    keys and (with plain) the _PLAIN fields. plain=False for values a caller passed in (tool arguments)."""
    if isinstance(obj, str):
        from .actions import escape
        for s in _SECRETS:
            obj = obj.replace(s, "•••").replace(escape(s), "•••")
        return obj
    if isinstance(obj, dict):
        return {k: v if plain and k in _PLAIN and not isinstance(v, (dict, list)) else mask(v, plain)
                for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [mask(v, plain) for v in obj]
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
        if not RUN_ID.fullmatch(run_id):
            raise ValueError(f"bad run id {run_id!r}")
        run = Run(self, run_id, {"id": run_id, "started": time.time(), "status": "running", "journeys": [],
                                 "actor": current_actor(), **meta})
        try:
            run.dir.mkdir(parents=True)
        except FileExistsError:
            raise ValueError(f"run {run_id} already exists") from None
        run.save()
        return run

    def runs(self) -> list[dict]:
        out = [read_json(p, None) for p in (self.root / "runs").glob("*/run.json")]
        out = [r for r in out if isinstance(r, dict)]  # a run file of the wrong shape is skipped
        return sorted(out, key=lambda r: r.get("started") if isinstance(r.get("started"), (int, float)) else 0)

    def history(self, last: int = 10) -> dict[str, dict]:
        """Per journey: its last results (oldest first) and whether it is flaky.

        Flaky: in its last `last` finished results it both passed and failed, and changed between
        them at least twice (pass, fail, pass), so one real break that stays broken is not flaky."""
        seq: dict[str, list[str]] = {}
        for r in self.runs():
            for j in r.get("journeys") or []:
                if isinstance(j, dict) and j.get("status") in ("passed", "failed"):
                    seq.setdefault(j["name"], []).append(j["status"])
        out = {}
        for name, results in seq.items():
            tail = results[-last:]
            flips = sum(a != b for a, b in zip(tail, tail[1:]))
            out[name] = {"results": tail, "passed": tail.count("passed"), "failed": tail.count("failed"),
                         "flaky": flips >= 2}
        return out

    def run(self, run_id: str) -> dict | None:
        if not RUN_ID.fullmatch(str(run_id)):  # never a path out of the state folder
            raise ValueError(f"bad run id {run_id!r}")
        return read_json(self.root / "runs" / run_id / "run.json", None)

    # activity: who did what (drafts, promotes, decisions, agent tool calls), one JSON line each
    def log(self, action: str, **detail) -> None:
        """Never fails the command that did the work: a log that cannot be written is skipped."""
        if "args" in detail:  # a caller's values: no field is Journeys' own there
            detail["args"] = mask(detail["args"], plain=False)
        detail = {k: v[:500] if isinstance(v, str) else v for k, v in detail.items()}  # a long reason stays short
        line = mask({"time": time.time(), "actor": current_actor(), "action": action, **detail})
        try:
            with open(self.root / "activity.jsonl", "a") as f:
                f.write(json.dumps(line, default=str) + "\n")
        except OSError:
            pass

    def activity(self) -> list[dict]:
        try:
            lines = (self.root / "activity.jsonl").read_text().splitlines()
        except FileNotFoundError:
            return []
        out = []
        for ln in lines:
            try:
                item = json.loads(ln)
            except json.JSONDecodeError:
                continue  # a line cut short by a crash: skip it, keep the rest
            if isinstance(item, dict):
                out.append(item)
        return out

    # failures (F-n) and proposals (P-n): lists of records in one JSON file each
    def _items(self, kind: str) -> list[dict]:
        items = read_json(self.root / f"{kind}.json", [])
        return [i for i in items if isinstance(i, dict) and "id" in i] if isinstance(items, list) else []

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

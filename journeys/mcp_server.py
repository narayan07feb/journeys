"""`journeys mcp`: the Model Context Protocol over stdio (JSON-RPC 2.0, one message per line).

Runs started here run as a detached child process, so `start_run` returns at once; `wait_run`
polls until the run ends (or its time is up) and can be called again.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import __version__
from .heal import PromoteError, promote, reject
from .pack import Pack, PackError
from .results import Store, read_json, write_json

PROTOCOL = "2025-06-18"


def _schema(props: dict | None = None, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props or {}, "required": required or []}


_S, _I = {"type": "string"}, {"type": "integer"}
TOOLS = {
    "doctor": ("Check the pack (screens, edges, journeys) and list its errors.", _schema()),
    "list_journeys": ("List the pack's sets and journeys.", _schema()),
    "start_run": ("Start a run of a journey, a set, or `all` on one device; returns its run id at once. "
                  "driver: uiautomator, maestro, speedrun or fake (default: the pack's). A real driver needs serial.",
                  _schema({"target": _S, "serial": _S, "driver": _S}, ["target"])),
    "run_status": ("Status of a run: per journey passed/failed, failure ids and proposals.",
                   _schema({"run_id": _S}, ["run_id"])),
    "wait_run": ("Wait until a run ends (at most timeout_s, default 300), then return its status.",
                 _schema({"run_id": _S, "timeout_s": _I}, ["run_id"])),
    "failures": ("The most recent failures (id, journey, step, message).", _schema({"last": _I})),
    "failure": ("One failure in full: step, screen listing, screenshot path, explored options, proposal.",
                _schema({"id": _S}, ["id"])),
    "proposals": ("Open proposals (detours and fixes waiting for a human or agent to decide).", _schema()),
    "promote": ("Write a proposal into the pack's files. name: a new screen's name.",
                _schema({"id": _S, "name": _S}, ["id"])),
    "reject": ("Reject a proposal, with the reason.", _schema({"id": _S, "why": _S}, ["id", "why"])),
}


class Server:
    def __init__(self, pack_path: str, state: str | None = None):
        self.pack_path = pack_path
        self.state = state
        self._children: list[subprocess.Popen] = []

    def _pack(self) -> Pack:
        return Pack.load(self.pack_path)

    def _store(self, pack: Pack | None = None) -> Store:
        from .results import default_state_dir
        if self.state:
            return Store(Path(self.state).expanduser())
        return Store(default_state_dir((pack or self._pack()).name))

    # tools
    def doctor(self) -> dict:
        p = self._pack()
        return {"pack": p.name, "app": p.app, "screens": len(p.map.screens), "edges": len(p.map.edges),
                "journeys": len(p.journeys), "errors": p.errors}

    def list_journeys(self) -> dict:
        p = self._pack()
        return {"sets": {n: [j.name for j in p.select(n)] for n in p.sets},
                "journeys": [{"name": j.name, "tags": j.tags, "file": str(j.file)} for j in p.journeys.values()]}

    def start_run(self, target: str, serial: str | None = None, driver: str | None = None) -> dict:
        pack = self._pack()
        pack.select(target)  # raises for an unknown target before anything starts
        if (driver or pack.driver) != "fake" and not serial:
            raise ValueError("serial is required: Journeys never picks a device by itself")
        store = self._store(pack)
        run_id = store.make_run_id()
        argv = [sys.executable, "-m", "journeys", "--pack", str(pack.root), "--state", str(store.root),
                "run", target, "--run-id", run_id]
        argv += ["--serial", serial] if serial else []
        argv += ["--driver", driver] if driver else []
        log = store.root / "mcp-runs" / f"{run_id}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "w") as out:
            proc = subprocess.Popen(argv, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    start_new_session=True, cwd=pack.root)
        write_json(log.with_suffix(".json"), {"pid": proc.pid, "argv": argv})
        self._children.append(proc)
        return {"run_id": run_id, "log": str(log)}

    def _alive(self, store: Store, run_id: str) -> bool:
        meta = read_json(store.root / "mcp-runs" / f"{run_id}.json", {})
        for proc in self._children:
            if proc.pid == meta.get("pid"):
                return proc.poll() is None
        try:
            os.kill(meta["pid"], 0)
            return True
        except (KeyError, OSError):
            return False

    def run_status(self, run_id: str) -> dict:
        store = self._store()
        run = store.run(run_id)
        if run is None:
            log = store.root / "mcp-runs" / f"{run_id}.log"
            if not log.exists():
                raise ValueError(f"no run {run_id}")
            if self._alive(store, run_id):
                return {"run_id": run_id, "status": "starting"}
            return {"run_id": run_id, "status": "failed to start", "log": log.read_text()[-2000:]}
        status = run["status"]
        if status == "running" and not self._alive(store, run_id) and (store.root / "mcp-runs" / f"{run_id}.json").exists():
            status = "stopped (the run process ended without finishing)"
        return {"run_id": run_id, "status": status, "journeys": [
            {k: j.get(k) for k in ("name", "status", "failure", "message", "changes")} for j in run["journeys"]]}

    def wait_run(self, run_id: str, timeout_s: int = 300) -> dict:
        end = time.monotonic() + max(1, min(int(timeout_s), 3600))
        while True:
            st = self.run_status(run_id)
            if st["status"] not in ("running", "starting") or time.monotonic() >= end:
                if st["status"] in ("running", "starting"):
                    st["note"] = "still running: call wait_run again"
                return st
            time.sleep(0.5)

    def failures(self, last: int = 10) -> list:
        return [{k: f.get(k) for k in ("id", "journey", "step_text", "message", "proposal")}
                for f in self._store().failures()[-last:]]

    def failure(self, id: str) -> dict:
        f = self._store().failure(id)
        if not f:
            raise ValueError(f"no failure {id}")
        return f

    def proposals(self) -> list:
        return [p for p in self._store().proposals() if p["status"] == "proposed"]

    def promote(self, id: str, name: str | None = None) -> str:
        pack = self._pack()
        return promote(pack, self._store(pack), id, name)

    def reject(self, id: str, why: str) -> str:
        reject(self._store(), id, why)
        return f"{id} rejected"

    # protocol
    def handle(self, msg: dict) -> dict | None:
        mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
        if mid is None:  # a notification: no answer
            return None
        if method == "initialize":
            result = {"protocolVersion": params.get("protocolVersion") or PROTOCOL,
                      "capabilities": {"tools": {}},
                      "serverInfo": {"name": "journeys", "version": __version__}}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": [{"name": n, "description": d, "inputSchema": s} for n, (d, s) in TOOLS.items()]}
        elif method == "tools/call":
            result = self._call(params.get("name"), params.get("arguments") or {})
        else:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"no method {method}"}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def _call(self, name: str, args: dict) -> dict:
        if name not in TOOLS:
            return {"content": [{"type": "text", "text": f"no tool {name}"}], "isError": True}
        try:
            out = getattr(self, name)(**args)
            text = out if isinstance(out, str) else json.dumps(out, indent=1, default=str)
            return {"content": [{"type": "text", "text": text}]}
        except (PackError, PromoteError, ValueError, TypeError, OSError) as e:
            return {"content": [{"type": "text", "text": f"error: {e}"}], "isError": True}


def serve_stdio(server: Server, stdin=None, stdout=None) -> None:
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    for line in stdin:
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            reply = server.handle(msg)
        if reply is not None:
            stdout.write(json.dumps(reply) + "\n")
            stdout.flush()

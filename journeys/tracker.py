"""The tracker page's data: runs, the work queue, agent activity and agent usage, from the state folder.

Read-only: it only reads what runs, failures, proposals and the activity log already hold.
"""
from __future__ import annotations

import time

from .results import Store

# what counts as building a journey, and as solving an issue (a failure or a proposal)
BUILD = {"draft.new", "draft.import", "journey.promote"}
DECIDE = {"proposal.promote", "proposal.reject"}


def _day(t: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(t))


def runs(store: Store, last: int = 30) -> list[dict]:
    out = []
    for r in store.runs()[-last:]:
        js = r.get("journeys") or []
        start, end = r.get("started"), r.get("ended")
        out.append({"id": r.get("id"), "target": r.get("target"), "status": r.get("status"),
                    "actor": r.get("actor", "?"), "started": start,
                    "seconds": round(end - start) if start and end else None,
                    "passed": sum(j.get("status") == "passed" for j in js),
                    "failed": sum(j.get("status") == "failed" for j in js), "error": r.get("error")})
    return out


def _fixes(store: Store) -> dict[str, dict]:
    """Per failure id: when its journey next passed, and who fixed it. A failure is fixed by the
    last decision or journey edit between the failure and that pass, else by whoever ran the pass."""
    passes: dict[str, list[tuple[float, str]]] = {}
    for r in store.runs():
        for j in r.get("journeys") or []:
            if j.get("status") == "passed":
                passes.setdefault(j.get("name"), []).append((r.get("ended") or r.get("started") or 0,
                                                             r.get("actor", "?")))
    work = [a for a in store.activity() if a.get("action") in DECIDE | {"journey.promote"}]
    out = {}
    for f in store.failures():
        if f.get("journey") == "tap-all":
            continue  # a tap-all crash has no journey to pass again
        t = f.get("time") or 0
        later = sorted(p for p in passes.get(f.get("journey"), []) if p[0] > t)
        if not later:
            continue
        when, runner = later[0]
        by = [a for a in work if t < (a.get("time") or 0) <= when
              and (a.get("failure") == f["id"] or a.get("journey") == f.get("journey"))]
        out[f["id"]] = {"fixed": when, "minutes": round((when - t) / 60, 1),
                        "by": by[-1]["actor"] if by else runner}
    return out


def queue(store: Store, drafts: list[str]) -> dict:
    fixed = _fixes(store)
    decided = {p.get("failure") for p in store.proposals() if p.get("status") != "proposed"}
    return {
        "failures": [{k: f.get(k) for k in ("id", "journey", "message", "time")} for f in store.failures()
                     if f["id"] not in fixed and f["id"] not in decided][-50:],
        "proposals": [{k: p.get(k) for k in ("id", "summary", "time", "failure")} for p in store.proposals()
                      if p.get("status") == "proposed"],
        "drafts": sorted(drafts),
    }


def activity(store: Store, last: int = 200) -> list[dict]:
    """What people and agents did, newest first: the log plus run starts and ends."""
    items = list(store.activity())
    for r in store.runs():
        items.append({"time": r.get("started"), "actor": r.get("actor", "?"), "action": "run.start",
                      "target": r.get("target"), "run": r.get("id")})
        if r.get("ended"):
            items.append({"time": r["ended"], "actor": r.get("actor", "?"), "action": "run.end",
                          "target": r.get("target"), "run": r.get("id"), "status": r.get("status")})
    return sorted(items, key=lambda a: a.get("time") or 0, reverse=True)[:last]


def usage(store: Store) -> dict:
    """Per actor and per day: journeys built, issues solved, runs, agent tool calls, fix time."""
    def blank() -> dict:
        return {"drafts": 0, "journeys_built": 0, "decided": 0, "fixed": 0, "runs": 0, "tool_calls": 0,
                "fix_minutes": []}
    per: dict[str, dict] = {}
    days: dict[str, dict[str, dict]] = {}

    def add(actor: str, t: float, key: str, n=1) -> None:
        for row in (per.setdefault(actor, blank()), days.setdefault(_day(t), {}).setdefault(actor, blank())):
            if key == "fix_minutes":
                row[key].append(n)
            else:
                row[key] += n

    for a in store.activity():
        act, who, t = a.get("action"), a.get("actor", "?"), a.get("time") or 0
        if act in ("draft.new", "draft.import"):
            add(who, t, "drafts")
        elif act == "journey.promote":
            add(who, t, "journeys_built")
        elif act in DECIDE:
            add(who, t, "decided")
        elif act == "mcp.call":
            add(who, t, "tool_calls")
    for r in store.runs():
        add(r.get("actor", "?"), r.get("started") or 0, "runs")
    for fix in _fixes(store).values():
        add(fix["by"], fix["fixed"], "fixed")
        add(fix["by"], fix["fixed"], "fix_minutes", fix["minutes"])

    def done(row: dict) -> dict:
        m = row.pop("fix_minutes")
        row["solved"] = row["decided"] + row["fixed"]
        row["avg_fix_minutes"] = round(sum(m) / len(m), 1) if m else None
        return row
    return {"actors": {k: done(v) for k, v in sorted(per.items())},
            "days": {d: {k: done(v) for k, v in sorted(rows.items())} for d, rows in sorted(days.items())[-14:]}}


def summary(store: Store, drafts: list[str] | None = None) -> dict:
    return {"runs": runs(store), "queue": queue(store, drafts or []), "activity": activity(store),
            "usage": usage(store)}

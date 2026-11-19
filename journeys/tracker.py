"""The tracker page's data: runs, the work queue, agent activity and agent usage, from the state folder.

Read-only: it reads what runs, failures, proposals and the activity log already hold, once per call,
and skips any record of the wrong shape (a hand-edited or half-written file never breaks the page).
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass

from .results import Store

# what counts as building a journey, and as deciding a proposal
DECIDE = {"proposal.promote", "proposal.reject"}


def _t(v) -> float:
    """A time from a state file, or 0: never NaN, infinity or a date the clock cannot show."""
    ok = isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and 0 < v < 1e11
    return float(v) if ok else 0.0


def _s(v) -> str:
    return v if isinstance(v, str) else "?"


def _day(t: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(t))


@dataclass
class State:
    runs: list[dict]
    failures: list[dict]
    proposals: list[dict]
    activity: list[dict]

    @classmethod
    def read(cls, store: Store) -> "State":
        acts = [a for a in store.activity() if isinstance(a.get("action"), str)]
        for a in acts:  # ids other code looks up by: text only
            for k in ("failure", "journey", "id"):
                if k in a and not isinstance(a[k], str):
                    a[k] = None
        props = [p for p in store.proposals() if isinstance(p["id"], str)]
        for p in props:
            if not isinstance(p.get("failure"), str):
                p["failure"] = None
        return cls(store.runs(), [f for f in store.failures() if isinstance(f["id"], str)], props, acts)


def _journeys(run: dict) -> list[dict]:
    js = run.get("journeys")
    return [j for j in js if isinstance(j, dict)] if isinstance(js, list) else []


def runs(st: State, last: int = 30) -> list[dict]:
    out = []
    for r in st.runs[-last:]:
        js = _journeys(r)
        start, end = _t(r.get("started")), _t(r.get("ended"))
        out.append({"id": r.get("id"), "target": r.get("target"), "status": r.get("status"),
                    "actor": _s(r.get("actor")), "started": start or None,
                    "seconds": round(end - start) if start and end else None,
                    "passed": sum(j.get("status") == "passed" for j in js),
                    "failed": sum(j.get("status") == "failed" for j in js), "error": r.get("error")})
    return out


def fixes(st: State) -> dict[str, dict]:
    """Per failure id: when its journey next passed, and who fixed it. Best effort: the fix goes to
    whoever last promoted this failure's proposal or that journey between the failure and the pass,
    else to whoever ran the passing run. Only a run that started after the failure counts. A tap-all
    crash is fixed by a later clean tap-all that walked everything, with the same --dka."""
    passes: dict[str, list[tuple[float, float, str]]] = {}  # journey -> (started, ended, actor)
    dka = {r.get("id"): bool(r.get("dka")) for r in st.runs}
    for r in st.runs:
        start = _t(r.get("started"))
        p = (start, _t(r.get("ended")) or start, _s(r.get("actor")))
        if r.get("target") == "tap-all" and r.get("status") == "passed" and not r.get("not_visited"):
            passes.setdefault(_walk(bool(r.get("dka"))), []).append(p)  # a full clean walk closes its crashes
        for j in _journeys(r):
            if j.get("status") == "passed" and isinstance(j.get("name"), str):
                passes.setdefault(j["name"], []).append(p)
    work: dict[str, list[dict]] = {}  # failure id or journey name -> promotes, by time (a reject fixes nothing)
    for a in st.activity:
        if a["action"] in ("proposal.promote", "journey.promote"):
            for key in (a.get("failure"), a.get("journey")):
                if isinstance(key, str):
                    work.setdefault(key, []).append(a)
    out = {}
    for f in st.failures:
        if not isinstance(f.get("journey"), str):
            continue
        t = _t(f.get("time"))
        key = _walk(dka.get(f.get("run"), False)) if f["journey"] == "tap-all" else f["journey"]
        later = sorted(p for p in passes.get(key, []) if p[0] > t)
        if not later:
            continue
        _, when, runner = later[0]
        by = sorted((a for a in work.get(f["id"], []) + work.get(f["journey"], []) if t < _t(a.get("time")) <= when),
                    key=lambda a: _t(a.get("time")))
        out[f["id"]] = {"journey": f["journey"], "fixed": when, "minutes": round((when - t) / 60, 1),
                        "by": _s(by[-1].get("actor")) if by else runner}
    return out


def _walk(dka: bool) -> str:
    return "tap-all --dka" if dka else "tap-all"


def queue(st: State, drafts: list[str], fixed: dict) -> dict:
    """Open work. A failure stays open until its journey passes again or its proposal is promoted:
    a rejected proposal means "not this heal", so the failure still needs one by hand."""
    healed = {p.get("failure") for p in st.proposals if p.get("status") == "promoted"}
    open_ = [{k: f.get(k) for k in ("id", "journey", "message", "time")} for f in st.failures
             if f["id"] not in fixed and f["id"] not in healed]
    return {
        "failures": open_[-50:], "failures_open": len(open_),  # the list is the newest 50; the count is all
        "proposals": [{k: p.get(k) for k in ("id", "summary", "time", "failure")} for p in st.proposals
                      if p.get("status") == "proposed"],
        "drafts": sorted(drafts),
    }


def activity(st: State, last: int = 200) -> list[dict]:
    """What people and agents did, newest first: the log plus run starts and ends."""
    items = list(st.activity)
    for r in st.runs:
        base = {"actor": _s(r.get("actor")), "target": r.get("target"), "run": r.get("id")}
        if _t(r.get("started")):
            items.append({"time": _t(r["started"]), "action": "run.start", **base})
        if _t(r.get("ended")):
            items.append({"time": _t(r["ended"]), "action": "run.end", "status": r.get("status"), **base})
    return sorted(items, key=lambda a: _t(a.get("time")), reverse=True)[:last]


def usage(st: State, fixed: dict) -> dict:
    """Per actor and per day: journeys built and issues solved, runs, agent tool calls, fix time.

    solved counts each issue once: a journey passing again after failing (however many failure
    records it left), plus failures healed by a promoted proposal and not counted that way.
    "decided" counts promotes and rejects; a reject solves nothing; a detour promote is a change."""
    def blank() -> dict:
        return {"drafts": 0, "journeys_built": 0, "decided": 0, "fixed": 0, "healed": 0, "changes": 0, "runs": 0,
                "tool_calls": 0, "fix_minutes": []}
    per: dict[str, dict] = {}
    days: dict[str, dict[str, dict]] = {}
    healed_once: set[str] = set()

    def add(actor: str, t: float, key: str, n=1) -> None:
        rows = [per.setdefault(actor, blank())]
        if t:  # an entry without a time still counts for its actor, not for a day
            rows.append(days.setdefault(_day(t), {}).setdefault(actor, blank()))
        for row in rows:
            if key == "fix_minutes":
                row[key].append(n)
            else:
                row[key] += n

    for a in st.activity:
        act, who, t = a["action"], _s(a.get("actor")), _t(a.get("time"))
        if act in ("draft.new", "draft.import"):
            add(who, t, "drafts")
        elif act == "journey.promote":
            add(who, t, "journeys_built")
        elif act in DECIDE:
            add(who, t, "decided")
            f = a.get("failure")
            if act == "proposal.promote" and isinstance(f, str) and f not in fixed and f not in healed_once:
                add(who, t, "healed")  # solved by the promote itself: no later pass counts it again
                healed_once.add(f)  # a second proposal for the same failure is not a second issue
            elif act == "proposal.promote" and f is None:
                add(who, t, "changes")  # a detour accepted: the app changed, nothing was broken
        elif act == "mcp.call":
            add(who, t, "tool_calls")
    for r in st.runs:
        add(_s(r.get("actor")), _t(r.get("started")), "runs")
    issues: dict[tuple, float] = {}  # one pass that fixes several failures of a journey is one issue
    for fix in fixed.values():
        k = (fix["journey"], fix["fixed"], fix["by"])
        issues[k] = max(issues.get(k, 0), fix["minutes"])  # its fix time runs from the first failure
    for (_, when, by), minutes in issues.items():
        add(by, when, "fixed")
        add(by, when, "fix_minutes", minutes)

    def done(row: dict) -> dict:
        m = row.pop("fix_minutes")
        row["solved"] = row["fixed"] + row.pop("healed")
        row["avg_fix_minutes"] = round(sum(m) / len(m), 1) if m else None
        return row
    return {"actors": {k: done(v) for k, v in sorted(per.items())},
            "days": {d: {k: done(v) for k, v in sorted(rows.items())} for d, rows in sorted(days.items())[-14:]}}


def summary(store: Store, drafts: list[str] | None = None) -> dict:
    st = State.read(store)
    fixed = fixes(st)
    return {"runs": runs(st), "queue": queue(st, drafts or [], fixed), "activity": activity(st),
            "usage": usage(st, fixed)}

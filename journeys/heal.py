"""Self-healing: explore a screen's options, and promote or reject proposals into the pack's files.

Proposal kinds (field `change`):
  expect-to-goto  {file, line, screen}            a journey's `expect: S` becomes `goto: S`
  edge-to         {src, old, new}                 edge src->old now lands on new: set `to: new`
  new-edge        {src, dst, do}                  add an edge
  new-screen      {name, match, edges: [...]}     add a screen (and edges from it)
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from . import yamlio
from .appmap import AppMap
from .drivers.base import Driver, DriverError, Screen
from .pack import Pack
from .results import Store

TAPPABLE = {"Button", "Field", "Text", "Switch", "CheckBox", "Image", "View", "Tab", "Link", "Radio"}


def fingerprint(screen: Screen) -> frozenset:
    return frozenset((e.kind, e.text, e.id) for e in screen.elements)


def same_screen(a: Screen, b: Screen) -> bool:
    fa, fb = fingerprint(a), fingerprint(b)
    if not fa and not fb:
        return True
    return len(fa & fb) / max(len(fa | fb), 1) >= 0.8


def selector(el) -> dict | str:
    return {"id": el.id} if el.id else el.text


def heading(screen: Screen) -> str:
    return next((e.text for e in screen.elements if e.kind == "Text" and e.text), "")


def screen_name(text: str) -> str:
    words = re.sub(r"[^A-Za-z0-9]+", " ", text).upper().split()
    return "-".join(words[:4]) or "UNNAMED"


def tap_targets(screen: Screen, deny: list, limit: int) -> list:
    """The elements tap-all and explore may tap: tappable kinds, with a text or id, not denied."""
    return [e for e in screen.elements
            if e.kind in TAPPABLE and (e.text or e.id) and not any(r.matches(e) for r in deny)][:limit]


def left_app(driver: Driver, app: str) -> str | None:
    """The other package on screen after a tap (a system screen, another app), else None."""
    fg = driver.foreground_app()
    return fg if fg and fg != app else None


def explore(driver: Driver, amap: AppMap, app: str, deny: list, limit: int = 12) -> list[dict]:
    """Tap each allowed option on the current screen once, see where it leads, go back.

    Stops early when the app dies, leaves for another app, or `back` does not return."""
    try:
        base = driver.look()
    except DriverError:
        return []
    options = []
    for el in tap_targets(base, deny, limit):
        tap = {"tapOn": selector(el)}
        res = driver.do([tap])
        if not res.ok:
            options.append({"tap": tap, "leads_to": None, "note": res.message[:200]})
            continue
        if not driver.app_alive():
            options.append({"tap": tap, "leads_to": "CRASH"})
            break
        other = left_app(driver, app)
        if other:
            options.append({"tap": tap, "leads_to": None, "note": f"left the app for {other}; explore stopped"})
            driver.do(["back"])
            break
        after = driver.look()
        if same_screen(base, after):
            options.append({"tap": tap, "leads_to": None, "note": "no change"})
            continue
        known = amap.identify(after).name
        options.append({"tap": tap, "leads_to": known, "heading": None if known else heading(after)})
        driver.do(["back"])
        if not same_screen(base, driver.look()):
            options[-1]["note"] = "back did not return; explore stopped"
            break
    return options


# --- promote / reject -------------------------------------------------------------------------

class PromoteError(Exception):
    pass


def _edit_line(path: Path, line: int, pattern: str, repl: str) -> None:
    lines = path.read_text().splitlines(keepends=True)
    if not 0 < line <= len(lines) or not re.search(pattern, lines[line - 1]):
        raise PromoteError(f"{path}:{line} no longer matches {pattern!r}: edit it by hand")
    lines[line - 1] = re.sub(pattern, repl, lines[line - 1], count=1)
    path.write_text("".join(lines))


def _append(path: Path, top_key: str, items) -> None:
    """Append entries under the file's top key (the key must be the file's last block)."""
    if not path.exists() or not path.read_text().strip():
        path.write_text(f"{top_key}:\n")
    text = path.read_text()
    body = yaml.safe_dump(items, sort_keys=False, allow_unicode=True, default_flow_style=False)
    body = "".join(f"  {ln}\n" for ln in body.splitlines())
    path.write_text(text.rstrip("\n") + "\n" + body)


def _edge_line(pack: Pack, src: str, dst: str) -> int:
    for raw in (yamlio.load(pack.root / "map" / "edges.yaml") or {}).get("edges") or []:
        if raw.get("from") == src and raw.get("to") == dst:
            return raw[yamlio.LINE]
    raise PromoteError(f"no edge {src} -> {dst} in edges.yaml any more")


def apply(pack: Pack, change: dict) -> str:
    kind = change["kind"]
    edges_file, screens_file = pack.root / "map" / "edges.yaml", pack.root / "map" / "screens.yaml"
    if kind == "expect-to-goto":
        s = re.escape(change["screen"])
        _edit_line(Path(change["file"]), change["line"], rf"expect:\s*{s}\b", f"goto: {change['screen']}")
        return f"{change['file']}:{change['line']}: expect -> goto {change['screen']}"
    if kind == "edge-to":
        start = _edge_line(pack, change["src"], change["old"])
        lines = edges_file.read_text().splitlines()
        pat = rf"\bto:\s*{re.escape(change['old'])}\b"
        for n in range(start, min(start + 10, len(lines) + 1)):
            if re.search(pat, lines[n - 1]):
                _edit_line(edges_file, n, pat, f"to: {change['new']}")
                return f"edge {change['src']} -> {change['old']} now goes to {change['new']}"
        raise PromoteError(f"could not find `to: {change['old']}` near {edges_file}:{start}")
    if kind == "new-edge":
        _append(edges_file, "edges", [{"from": change["src"], "to": change["dst"], "do": change["do"]}])
        return f"edge {change['src']} -> {change['dst']} added"
    if kind == "new-screen":
        if change["name"] in pack.map.screens:
            raise PromoteError(f"screen {change['name']} already exists: rename it in the proposal")
        _append(screens_file, "screens", {change["name"]: {"match": change["match"]}})
        if change.get("edges"):
            _append(edges_file, "edges", change["edges"])
        return f"screen {change['name']} added with {len(change.get('edges') or [])} edges"
    raise PromoteError(f"unknown proposal kind {kind}")


def promote(pack: Pack, store: Store, pid: str, name: str | None = None) -> str:
    p = store.proposal(pid)
    if not p:
        raise PromoteError(f"no proposal {pid}")
    if p["status"] != "proposed":
        raise PromoteError(f"{pid} is already {p['status']}")
    change = dict(p["change"])
    if name and change["kind"] == "new-screen":
        old = change["name"]
        change["name"] = name
        change["edges"] = [{**e, "from": name if e["from"] == old else e["from"],
                            "to": name if e["to"] == old else e["to"]} for e in change.get("edges") or []]
    done = apply(pack, change)
    check = Pack.load(pack.root)
    store.update_proposal(pid, status="promoted", result=done, pack_errors=check.errors)
    return done + (f" (pack now has {len(check.errors)} errors: run doctor)" if check.errors else "")


def reject(store: Store, pid: str, why: str) -> None:
    p = store.proposal(pid)
    if not p:
        raise PromoteError(f"no proposal {pid}")
    if p["status"] != "proposed":
        raise PromoteError(f"{pid} is already {p['status']}")
    store.update_proposal(pid, status="rejected", why=why)

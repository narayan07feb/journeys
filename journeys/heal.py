"""Self-healing: explore a screen's options, and promote or reject proposals into the pack's files.

Proposal kinds (field `change`):
  expect-to-goto  {file, line, screen}            a journey's `expect: S` becomes `goto: S`
  edge-to         {src, old, new}                 edge src->old now lands on new: set `to: new`
  new-edge        {src, dst, do}                  add an edge
  new-screen      {name, match, edges: [...]}     add a screen (and edges from it)
"""
from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path


from . import yamlio
from .actions import ActionError, dump, escape, from_command, from_commands, to_commands
from .appmap import KINDS, AppMap
from .drivers.base import Driver, DriverError, Screen, find, id_matches
from .pack import Pack
from .results import Store

TAPPABLE = set(KINDS)  # every kind a driver reports can be tapped


def fingerprint(screen: Screen) -> frozenset:
    return frozenset((e.kind, e.text, e.id) for e in screen.elements)


def same_screen(a: Screen, b: Screen) -> bool:
    fa, fb = fingerprint(a), fingerprint(b)
    if not fa and not fb:
        return True
    return len(fa & fb) / max(len(fa | fb), 1) >= 0.8


def selector(el, screen: Screen | None = None) -> dict | str | None:
    """A selector that finds THIS element again on the screen, or None when none does (then the
    element is not tapped: a selector that hits another element could hit a denied one).

    Flow selectors read text as a regex for the whole text, so the text is escaped. A row whose
    label joins its children's texts ("Wi-Fi, Connected") or a label cut short ("Long text…") is
    not one node's text on every driver: its first part is tried first. A repeated id (list rows)
    names only the first match: then the text, or id + index."""
    els = screen.elements if screen else [el]
    me = next(i for i, e in enumerate(els) if e is el) if screen else 0
    text = el.text
    lit = (lambda t: "\\" + escape(t) if re.fullmatch(r"@\d+", t) else escape(t))  # "@2" is text, not a ref
    tries: list = []
    if el.id:
        tries.append({"id": el.id})
    prefix = text[:-1].split(", ")[0] if text.endswith("…") else text.split(", ")[0] if ", " in text else ""
    if prefix.strip():  # never ".*" alone: it would match whatever is first
        tries.append(lit(prefix) + ".*")
    if text:
        tries.append(lit(text))
        same = [i for i, e in enumerate(els) if e.text == text]
        tries.append({"text": lit(text), "index": same.index(me)})
    if el.id:
        same = [i for i, e in enumerate(els) if e.id and id_matches(el.id, e.id)]
        tries.append({"id": el.id, "index": same.index(me)})
    return next((t for t in tries if find(els, t) == me and _only(els, t)), None)


def _only(els: list, sel) -> bool:
    """No other element matches the selector without its index: a driver may pick another match
    than the first one. With an index, the index names the one element."""
    return isinstance(sel, dict) and "index" in sel or \
        sum(find([e], sel) == 0 for e in els) == 1


def heading(screen: Screen) -> str:
    return next((e.text for e in screen.elements if e.kind == "Text" and e.text), "")


def screen_name(text: str) -> str:
    words = re.sub(r"[^A-Za-z0-9]+", " ", text).upper().split()
    return "-".join(words[:4]) or "UNNAMED"


def denied(el, deny: list) -> bool:
    """A deny rule matches the label, or a text rule matches the id's name ("OK" with id
    delete_account): a label can be vague or cut short, the id often says what the button does."""
    name = el.id.rsplit("/", 1)[-1] if el.id else ""
    name = re.sub(r"[_.]+|(?<=[a-z0-9])(?=[A-Z])", " ", name)  # delete_account, btnDeleteAccount
    el = replace(el, text=_ZW.sub("", el.text))  # "Log\u200bout" reads as "Logout"
    return any(r.matches(el) or (name and r.text and r.id is None and r.matches(replace(el, text=name)))
               for r in deny)


_ZW = re.compile("[\u200b-\u200d\u2060\ufeff\u00ad]")  # zero-width characters and soft hyphens
# Warning words with no denied word in them ("Are you sure?" [Proceed]): their confirm buttons are skipped too
_WARN = re.compile(r"(?i)are you sure|permanent|can(not|'t|’t) be undone|irreversib|closure|lose (all|your)")


def _warns(screen: Screen) -> bool:
    return any(_WARN.search(_ZW.sub("", e.text)) for e in screen.elements)


_CONFIRM = re.compile(r"(?i)\b(yes|ok(ay)?|confirm|continue|accept|agree|allow|proceed|go_ahead|i_understand|sure|"
                      r"do_it|apply|enable|turn_on|update|got_it)\b".replace("_", r"[\s\-\u2010-\u2015]"))


def tap_targets(screen: Screen, deny: list, limit: int) -> list:
    """The elements tap-all and explore may tap: tappable kinds with a label, not denied. A label-less
    element (often a container) is never tapped: its centre may sit over a denied button, and the
    deny list reads labels."""
    no = [e for e in screen.elements if denied(e, deny)]
    if no or _warns(screen):  # "Delete all your data?" [Yes]: a confirm button does what the words say
        no += [e for e in screen.elements if _CONFIRM.search(_ZW.sub("", e.text)) or _WARN.search(e.text)]
    texts, ids = {e.text for e in no}, {e.id for e in no if e.id}
    # an element that shares its text or id with a denied one is skipped too: its selector needs an
    # index, and a driver may count matches in another order than this listing
    return [e for e in screen.elements if e.kind in TAPPABLE and e.text.strip() and e not in no
            and e.text not in texts and e.id not in ids][:limit]


def new_denied(base: Screen, after: Screen, deny: list) -> bool:
    """Denied words on `after` that `base` did not show. Used when a tap looks like "no change" but
    put up a dialog ("Delete all?" over a long list): the old option list is stale then."""
    seen = {(e.text, e.id) for e in base.elements}
    new = [e for e in after.elements if (e.text, e.id) not in seen]
    risky = _warns(after) or any(denied(e, deny) for e in after.elements)  # a dialog over a denied screen too
    return any(denied(e, deny) for e in new) or (risky and any(_CONFIRM.search(_ZW.sub("", e.text)) for e in new))


def left_app(driver: Driver, app) -> str | None:
    """The other package on screen after a tap (a system screen, another app), else None.
    `app` is the pack's package, or a test of a package (Pack.owns: the app and its app_packages)."""
    fg = driver.foreground_app()
    owns = app if callable(app) else (lambda pkg: pkg == app)
    return fg if fg and not owns(fg) else None


def explore(driver: Driver, amap: AppMap, app, deny: list, limit: int = 12) -> list[dict]:
    """Tap each allowed option on the current screen once, see where it leads, go back.

    Stops early when the app dies, leaves for another app, or `back` does not return."""
    try:
        base = driver.look()
    except DriverError:
        return []
    options = []
    for el in tap_targets(base, deny, limit):
        sel = selector(el, base)
        if sel is None:  # no selector finds only this element: tapping could hit another one
            options.append({"tap": None, "leads_to": None, "note": f"skipped: no unique selector for {el.line()}"})
            continue
        tap = {"tapOn": sel}
        res = driver.do([tap])
        alive = driver.app_alive()
        if not res.ok and alive:  # a failed tap that killed the app is a crash, below
            options.append({"tap": from_command(tap), "leads_to": None, "note": res.message[:200]})
            continue
        if not alive:
            options.append({"tap": from_command(tap), "leads_to": "CRASH"})
            break
        other = left_app(driver, app)
        if other:
            options.append({"tap": from_command(tap), "leads_to": None, "note": f"left the app for {other}; explore stopped"})
            driver.do(["back"])
            break
        after = driver.look()
        if same_screen(base, after) and new_denied(base, after, deny):  # a dialog over it: the list is stale
            options.append({"tap": from_command(tap), "leads_to": None,
                            "note": "it showed denied words; explore stopped"})
            driver.do(["back"])
            break
        if same_screen(base, after):
            options.append({"tap": from_command(tap), "leads_to": None, "note": "no change"})
            continue
        known = amap.identify(after).name
        options.append({"tap": from_command(tap), "leads_to": known, "heading": None if known else heading(after)})
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
    # an empty inline value ("edges: []", "screens: {}") cannot take block items after it
    text = re.sub(rf"(?m)^{top_key}:\s*(\[\s*\]|\{{\s*\}})\s*$", f"{top_key}:", path.read_text())
    body = dump(items, default_flow_style=False)
    body = "".join(f"  {ln}\n" for ln in body.rstrip("\n").split("\n"))  # only \n: U+2028 stays in its text
    path.write_text(text.rstrip("\n") + "\n" + body)


def _edge_line(pack: Pack, src: str, dst: str) -> int:
    for raw in (yamlio.load(pack.root / "map" / "edges.yaml") or {}).get("edges") or []:
        if raw.get("from") == src and raw.get("to") == dst:
            return raw[yamlio.LINE]
    raise PromoteError(f"no edge {src} -> {dst} in edges.yaml any more")


def _neutral(do: list) -> list:
    """Proposals saved by older versions hold framework commands ({tapOn: ...}): write neutral words."""
    try:
        to_commands(do)
        return do
    except ActionError:
        return from_commands(do)


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
        _append(edges_file, "edges", [{"from": change["src"], "to": change["dst"], "do": _neutral(change["do"])}])
        return f"edge {change['src']} -> {change['dst']} added"
    if kind == "new-screen":
        if change["name"] in pack.map.screens:
            raise PromoteError(f"screen {change['name']} already exists: rename it in the proposal")
        _append(screens_file, "screens", {change["name"]: {"match": change["match"]}})
        if change.get("edges"):
            _append(edges_file, "edges", [{**e, "do": _neutral(e["do"])} for e in change["edges"]])
        return f"screen {change['name']} added with {len(change.get('edges') or [])} edges"
    raise PromoteError(f"unknown proposal kind {kind}")


def promote(pack: Pack, store: Store, pid: str, name: str | None = None) -> str:
    p = store.proposal(pid)
    if not p:
        raise PromoteError(f"no proposal {pid}")
    if p["status"] != "proposed":
        raise PromoteError(f"{pid} is already {p['status']}")
    change = dict(p["change"])
    if "•••" in json.dumps(change, ensure_ascii=False):  # a masked secret would become a rule that never matches
        raise PromoteError(f"{pid} holds a masked value (•••): write that screen or edge by hand, without the secret")
    if name and change["kind"] == "new-screen":
        old = change["name"]
        change["name"] = name
        change["edges"] = [{**e, "from": name if e["from"] == old else e["from"],
                            "to": name if e["to"] == old else e["to"]} for e in change.get("edges") or []]
    done = apply(pack, change)
    check = Pack.load(pack.root)
    errors = check.run_errors()  # unfinished drafts' errors are not this promote's doing
    store.update_proposal(pid, status="promoted", result=done, pack_errors=errors)
    store.log("proposal.promote", id=pid, failure=p.get("failure"), journey=p.get("journey"), result=done)
    return done + (f" (pack now has {len(errors)} errors: run validate)" if errors else "")


def reject(store: Store, pid: str, why: str) -> None:
    p = store.proposal(pid)
    if not p:
        raise PromoteError(f"no proposal {pid}")
    if p["status"] != "proposed":
        raise PromoteError(f"{pid} is already {p['status']}")
    store.update_proposal(pid, status="rejected", why=why)
    store.log("proposal.reject", id=pid, failure=p.get("failure"), journey=p.get("journey"), why=why)

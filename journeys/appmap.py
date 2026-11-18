"""The app map: screens known by what is on them, and edges (which actions lead where)."""
from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from . import yamlio
from .actions import ActionError, to_commands
from .drivers.base import Element, Screen

_RULE_KEYS = {"text", "id", "kind"}
KINDS = ("Text", "Button", "Field", "Switch", "CheckBox", "Image", "View")  # as drivers report them


@dataclass(frozen=True)
class Rule:
    text: str | None = None
    id: str | None = None
    kind: str | None = None

    @classmethod
    def parse(cls, raw) -> "Rule":
        if isinstance(raw, str):
            return cls(text=raw)
        if not isinstance(raw, dict):
            raise ValueError(f"a rule is a text or a map of {sorted(_RULE_KEYS)}, not {raw!r}")
        raw = yamlio.strip_lines(raw)
        extra = set(raw) - _RULE_KEYS
        if extra or not raw:
            raise ValueError(f"a rule takes {sorted(_RULE_KEYS)}, got {sorted(raw)}")
        rule = cls(**{k: str(v) for k, v in raw.items()})
        if rule.kind is not None and rule.kind not in KINDS:
            raise ValueError(f"kind is one of {', '.join(KINDS)}, not {rule.kind}")
        if rule.text and _is_regex(rule.text):
            re.compile(rule.text[1:-1])  # raises re.error early, at load time
        return rule

    def matches(self, el: Element) -> bool:
        if self.id is not None and el.id != self.id and not el.id.endswith(f":id/{self.id}"):
            return False  # Android ids read as "<package>:id/<name>"; the short name is enough
        if self.kind is not None and el.kind != self.kind:
            return False
        if self.text is not None:
            if _is_regex(self.text):
                return re.search(self.text[1:-1], el.text) is not None
            return el.text == self.text
        return True

    def matches_any(self, screen: Screen) -> bool:
        return any(self.matches(e) for e in screen.elements)


def _is_regex(text: str) -> bool:
    return len(text) >= 2 and text.startswith("/") and text.endswith("/")


@dataclass
class ScreenDef:
    name: str
    title: str = ""
    all: list[Rule] = field(default_factory=list)
    any: list[Rule] = field(default_factory=list)
    none: list[Rule] = field(default_factory=list)

    def score(self, screen: Screen) -> int | None:
        """None when the screen is not this one; else how many rules matched (more is surer)."""
        if not all(r.matches_any(screen) for r in self.all):
            return None
        if any(r.matches_any(screen) for r in self.none):
            return None
        hits = sum(r.matches_any(screen) for r in self.any)
        if self.any and not hits:
            return None
        return len(self.all) + hits


@dataclass
class Edge:
    src: str
    dst: str
    do: list


@dataclass
class Identity:
    name: str | None
    ambiguous: list[str] = field(default_factory=list)


@dataclass
class AppMap:
    screens: dict[str, ScreenDef] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, map_dir: Path) -> "AppMap":
        m = cls()
        sf, ef = map_dir / "screens.yaml", map_dir / "edges.yaml"
        if sf.exists():
            for name, raw in ((yamlio.load(sf) or {}).get("screens") or {}).items():
                if name == yamlio.LINE:
                    continue
                try:
                    raw = raw or {}
                    if not isinstance(raw, dict) or not isinstance(raw.get("match") or {}, dict):
                        raise ValueError("a screen is a map with title and match: {all, any, none}")
                    match = raw.get("match") or {}
                    m.screens[name] = ScreenDef(
                        name,
                        raw.get("title", ""),
                        *([Rule.parse(r) for r in match.get(k) or []] for k in ("all", "any", "none")),
                    )
                    if not (match.get("all") or match.get("any")):
                        m.errors.append(f"{yamlio.where(sf, raw)}: screen {name} has no all/any rule")
                except (ValueError, AttributeError, TypeError, re.error) as e:
                    m.errors.append(f"{yamlio.where(sf, raw)}: screen {name}: {e}")
        if ef.exists():
            for raw in (yamlio.load(ef) or {}).get("edges") or []:
                try:
                    do = yamlio.strip_lines(raw["do"])
                    if not (isinstance(raw["from"], str) and isinstance(raw["to"], str)):
                        raise TypeError("from and to are screen names")
                    m.edges.append(Edge(raw["from"], raw["to"], do if isinstance(do, list) else [do]))
                    to_commands(m.edges[-1].do)
                except (KeyError, TypeError) as e:
                    m.errors.append(f"{yamlio.where(ef, raw)}: an edge needs from, to and do ({e})")
                except ActionError as e:
                    m.errors.append(f"{yamlio.where(ef, raw)}: edge {raw['from']} -> {raw['to']}: {e}")
        for e in m.edges:
            for end in (e.src, e.dst):
                if end not in m.screens:
                    m.errors.append(f"{ef}: edge {e.src} -> {e.dst} names unknown screen {end}")
        return m

    def identify(self, screen: Screen) -> Identity:
        scored = [(s, d.name) for d in self.screens.values() if (s := d.score(screen)) is not None]
        if not scored:
            return Identity(None)
        best = max(s for s, _ in scored)
        names = sorted(n for s, n in scored if s == best)
        return Identity(names[0], []) if len(names) == 1 else Identity(None, names)

    def path(self, src: str, dst: str) -> list[Edge] | None:
        """Shortest list of edges from src to dst; [] when src is dst; None when there is no way."""
        if src == dst:
            return []
        prev: dict[str, Edge] = {}
        seen, todo = {src}, deque([src])
        while todo:
            cur = todo.popleft()
            for e in self.edges:
                if e.src == cur and e.dst not in seen:
                    seen.add(e.dst)
                    prev[e.dst] = e
                    if e.dst == dst:
                        out = []
                        while dst != src:
                            out.append(prev[dst])
                            dst = prev[dst].src
                        return out[::-1]
                    todo.append(e.dst)
        return None

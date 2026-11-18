"""A pack: one app's map, journeys, sets, hooks and settings, in a folder the core loads by path."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import yamlio
from .actions import ActionError, to_commands, to_selector
from .appmap import AppMap, Rule
from .drivers import DRIVERS
from .journey import Journey, load_journeys

# Never tapped by tap-all or explore, in any pack: words for actions that are hard to undo or
# that can cut the device off (Wi-Fi off strands a Wi-Fi adb link).
DEFAULT_DENY = [Rule(text=t) for t in (
    r"/(?i)\b(reset|erase|factory|wipe|delete|remove|uninstall|clear (data|storage|cache))\b/",
    r"/(?i)\b(wi-?fi|airplane|aeroplane|flight mode|bluetooth|mobile data|hotspot|usb debugging|developer options)\b/",
    r"/(?i)\b(sign ?out|log ?out|pay|buy|purchase|subscribe|send|call|share|rate us|report)\b/",
)]

_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_.]*)\}")


class PackError(Exception):
    pass


@dataclass
class Pack:
    root: Path
    name: str
    app: str
    driver: str = "uiautomator"
    build: list[list[str]] = field(default_factory=list)
    install: list[list[str]] = field(default_factory=list)
    env_names: list[str] = field(default_factory=list)
    accounts: dict[str, dict[str, str]] = field(default_factory=dict)
    hooks: dict[str, list[str]] = field(default_factory=dict)
    events: list[str] | None = None
    devices: dict = field(default_factory=dict)
    tap_allowed: bool = True
    deny: list[Rule] = field(default_factory=lambda: list(DEFAULT_DENY))
    map: AppMap = field(default_factory=AppMap)
    journeys: dict[str, Journey] = field(default_factory=dict)
    sets: dict[str, dict] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, root: Path | str) -> "Pack":
        root = Path(root).expanduser().resolve()
        pf = root / "pack.yaml"
        if not pf.exists():
            raise PackError(f"{pf} not found: a pack folder needs pack.yaml")
        raw = yamlio.strip_lines(yamlio.load(pf) or {})
        if not raw.get("name") or not raw.get("app"):
            raise PackError(f"{pf}: name and app are required")
        p = cls(
            root, str(raw["name"]), str(raw["app"]), str(raw.get("driver", "uiautomator")),
            list(raw.get("build") or []), list(raw.get("install") or []),
            [str(n) for n in raw.get("env") or []], dict(raw.get("accounts") or {}),
            dict(raw.get("hooks") or {}), (raw.get("events") or {}).get("check"),
            dict(raw.get("devices") or {}),
        )
        taps = raw.get("tap_all") or {}
        p.tap_allowed = bool(taps.get("allowed", True))
        for r in taps.get("deny") or []:
            try:
                p.deny.append(Rule.parse(r))
            except (ValueError, re.error) as e:
                p.errors.append(f"{pf}: tap_all.deny: {e}")
        if p.driver not in DRIVERS:
            p.errors.append(f"{pf}: driver is one of {', '.join(DRIVERS)}, not {p.driver}")
        p.map = AppMap.load(root / "map")
        p.errors += p.map.errors
        for f in sorted((root / "journeys").glob("**/*.y*ml")):
            js, errs = load_journeys(f)
            p.errors += errs
            for j in js:
                if j.name in p.journeys:
                    p.errors.append(f"{f}:{j.line}: journey {j.name} is also in {p.journeys[j.name].file}")
                else:
                    p.journeys[j.name] = j
        sf = root / "sets.yaml"
        if sf.exists():
            p.sets = yamlio.strip_lines((yamlio.load(sf) or {}).get("sets") or {})
        p._check()
        return p

    def _check(self) -> None:
        for j in self.journeys.values():
            for s in j.steps:
                at = f"{j.file}:{s.line}"
                if s.kind in ("expect", "goto") and s.arg not in self.map.screens:
                    self.errors.append(f"{at}: {s.kind} names unknown screen {s.arg}")
                try:
                    if s.kind == "do":
                        to_commands(s.arg)
                    elif s.kind in ("see", "not_see"):
                        to_selector(s.arg)
                except ActionError as e:
                    self.errors.append(f"{at}: {e}")
                if s.kind == "hook":
                    name = s.arg if isinstance(s.arg, str) else (s.arg or {}).get("name")
                    if name not in self.hooks:
                        self.errors.append(f"{at}: hook {name} is not in pack.yaml hooks")
                if s.kind == "event" and not self.events:
                    self.errors.append(f"{at}: event step needs events.check in pack.yaml")
        for name, argv in self.hooks.items():
            if not isinstance(argv, list) or not argv:
                self.errors.append(f"pack.yaml: hook {name} must be a command list")
        for name, spec in self.sets.items():
            if name in self.journeys:
                self.errors.append(f"sets.yaml: set {name} has the same name as a journey")
            if not isinstance(spec, dict):
                self.errors.append(f"sets.yaml: set {name} is a map with journeys and/or tags")
                continue
            for jn in spec.get("journeys") or []:
                if jn not in self.journeys:
                    self.errors.append(f"sets.yaml: set {name} names unknown journey {jn}")
            if not self.select(name):
                self.errors.append(f"sets.yaml: set {name} selects no journey")

    def select(self, target: str) -> list[Journey]:
        """A journey name, a set name, or `all`."""
        if target == "all":
            return list(self.journeys.values())
        if target in self.journeys:
            return [self.journeys[target]]
        spec = self.sets.get(target)
        if not isinstance(spec, dict):
            raise PackError(f"no journey or set named {target}")
        names = [n for n in spec.get("journeys") or [] if n in self.journeys]
        tags = set(spec.get("tags") or [])
        names += [j.name for j in self.journeys.values() if tags & set(j.tags) and j.name not in names]
        return [self.journeys[n] for n in names]

    def declared(self) -> set[str]:
        out = set(self.env_names)
        for acc, fields in self.accounts.items():
            for fld, var in (fields or {}).items():
                out |= {var, f"{acc}.{fld}"}
        return out

    def variables(self, environ: dict[str, str] | None = None) -> dict[str, str]:
        """Only declared env vars and account fields; the pack itself never holds secrets."""
        env = os.environ if environ is None else environ
        out = {n: env[n] for n in self.env_names if n in env}
        for acc, fields in self.accounts.items():
            for fld, var in (fields or {}).items():
                if var in env:
                    out[var] = env[var]
                    out[f"{acc}.{fld}"] = env[var]
        return out


def substitute(obj, variables: dict[str, str], declared: set[str]):
    """Replace ${NAME} for the pack's declared names; raise KeyError for a declared one that is not set.

    Undeclared ${...} stay as written, for the driver's own flow variables."""
    if isinstance(obj, str):
        def rep(m):
            name = m.group(1)
            if name in variables:
                return variables[name]
            if name in declared:
                raise KeyError(name)
            return m.group(0)
        return _VAR.sub(rep, obj)
    if isinstance(obj, list):
        return [substitute(v, variables, declared) for v in obj]
    if isinstance(obj, dict):
        return {k: substitute(v, variables, declared) for k, v in obj.items()}
    return obj

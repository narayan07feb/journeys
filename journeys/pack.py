"""A pack: one app's map, journeys, sets, hooks and settings, in a folder the core loads by path."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import yamlio
from .actions import QUOTE, ActionError, to_commands, to_selector
from .appmap import KINDS, AppMap, Rule
from .drivers import DRIVERS
from .journey import Journey, load_journeys

# Never tapped by tap-all or explore, in any pack: words for actions that are hard to undo or
# that can cut the device off (Wi-Fi off strands a Wi-Fi adb link).
_S = r"[\s\-\u2010-\u2015]"  # a space or dash of any kind: NBSP, U+2011 ("Log\u2011out") as on real devices
DEFAULT_DENY = [Rule(text=t.replace("_", _S)) for t in (
    # word stems (no end boundary): "Payments", "Deleted", "Subscriptions" match too; "_" is any space or dash
    r"/(?i)\b(reset|erase|factory|wipe|delet|remov|uninstall|clear|force_stop|"
    r"deactivat|disabl|terminat|close_account|un_?link|revoke|private_dns|forget|reboot|restart|power_off|shut_?down|turn_off|"
    r"block|discard|empty|format|install|opt_?out|unfollow|unfriend|unpair|leave|exit|quit|switch_off|"
    r"cancel_(plan|subscription|membership|order|booking|payment))/",
    r"/(?i)\b(wi_?fi|airplane|aeroplane|flight_mode|bluetooth|mobile_data|hotspot|usb_debugging"
    r"|developer_options|disconnect|wlan|mobile_network|network_reset|wireless_debug|adb|cellular|roaming|vpn"
    r"|tether|internet)/",
    r"/(?i)\b(sign_?(out|off)|log_?(out|off)|pay|buy|purchase|(un)?subscri|send|call|share|rate_us|report|"
    r"order|checkout|transfer|withdraw|donat|top_?up|add_money|submit|publish|post$|hang_?up|end_call|"
    r"stop|unregist|deauthori|dial|sos|refund|abort|kill|delink)/",
)]

_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_.]*)\}")
_JOURNEY_NAME = re.compile(r"[a-z0-9][a-z0-9-]*")
_PACKAGE = re.compile(r"[A-Za-z][\w]*(\.[A-Za-z_][\w]*)+")


def _argvs(steps: list, key: str, pf, errors: list[str]) -> list:
    """build/install entries are argv lists ([./gradlew, assembleDebug]), not one shell string."""
    good = [a for a in steps if isinstance(a, list) and a and all(isinstance(x, str) for x in a)]
    if len(good) != len(steps):
        errors.append(f"{pf}: {key} entries are command lists like [./gradlew, assembleDebug]")
    return good


def _names(obj) -> list[str]:
    """Every ${NAME} in a step's or edge's values."""
    if isinstance(obj, str):
        return _VAR.findall(obj)
    if isinstance(obj, dict):
        return [n for v in obj.values() for n in _names(v)]
    if isinstance(obj, list):
        return [n for v in obj for n in _names(v)]
    return []


class PackError(Exception):
    pass


@dataclass
class Pack:
    root: Path
    name: str
    app: str
    driver: str = ""
    build: list[list[str]] = field(default_factory=list)
    install: list[list[str]] = field(default_factory=list)
    env_names: list[str] = field(default_factory=list)
    accounts: dict[str, dict[str, str]] = field(default_factory=dict)
    hooks: dict[str, list[str]] = field(default_factory=dict)
    events: list[str] | None = None
    devices: dict = field(default_factory=dict)
    project: Path | None = None   # the app's project folder, for framework detection
    app_packages: list[str] = field(default_factory=list)  # other packages the app's screens run in
    tap_allowed: bool = True
    deny: list[Rule] = field(default_factory=lambda: list(DEFAULT_DENY))
    map: AppMap = field(default_factory=AppMap)
    journeys: dict[str, Journey] = field(default_factory=dict)
    drafts: dict[str, Journey] = field(default_factory=dict)   # new journeys, not yet in sets or `all`
    sets: dict[str, dict] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, root: Path | str) -> "Pack":
        root = Path(root).expanduser().resolve()
        pf = root / "pack.yaml"
        if not pf.exists():
            raise PackError(f"{pf} not found: a pack folder needs pack.yaml")
        load_errors: list[str] = []
        raw = yamlio.strip_lines(yamlio.load_map(pf, load_errors))
        if load_errors:
            raise PackError(load_errors[0])
        if not raw.get("name") or not raw.get("app"):
            raise PackError(f"{pf}: name and app are required")
        if not isinstance(raw["app"], str) or not _PACKAGE.fullmatch(raw["app"]):
            raise PackError(f"{pf}: app is an Android package name like com.example.app, not {raw['app']!r}")
        errors: list[str] = []

        def shaped(key, typ, what):
            """A pack.yaml value of the right shape, or an error line and an empty one."""
            value = raw.get(key)
            value = typ() if value is None else value  # `tap_all: false` is an error, not an empty map
            if not isinstance(value, typ):
                errors.append(f"{pf}: {key} is {what}, not {value!r}")
                return typ()
            return value
        accounts = shaped("accounts", dict, "a map of account: {field: ENV_VAR}")
        for acc, fields in list(accounts.items()):
            if not isinstance(fields, dict) or not all(isinstance(v, str) for v in fields.values()):
                errors.append(f"{pf}: accounts.{acc} is a map of field: ENV_VAR, not {fields!r}")
                del accounts[acc]
        events = shaped("events", dict, "a map with check: [command]")
        check = events.get("check")
        if check is not None and not (isinstance(check, list) and check and all(isinstance(a, str) for a in check)):
            errors.append(f"{pf}: events.check is a command list like [hooks/check.sh], not {check!r}")
            events = {}
        for name, argv in (raw.get("hooks") or {}).items() if isinstance(raw.get("hooks"), dict) else []:
            if not (isinstance(argv, list) and argv and all(isinstance(a, str) for a in argv)):
                errors.append(f"{pf}: hook {name} is a command list like [hooks/{name}.sh], not {argv!r}")
        p = cls(
            root, str(raw["name"]), str(raw["app"]), str(raw.get("driver") or ""),
            _argvs(shaped("build", list, "a list of commands"), "build", pf, errors),
            _argvs(shaped("install", list, "a list of commands"), "install", pf, errors),
            [str(n) for n in shaped("env", list, "a list of names")], accounts,
            shaped("hooks", dict, "a map of name: [command]"), events.get("check"),
            shaped("devices", dict, "a map"),
        )
        rule = p.devices.get("allow_settings_changes", "emulator")
        if not (rule in ("emulator", "any") or isinstance(rule, list) and all(isinstance(x, str) for x in rule)):
            errors.append(f"{pf}: devices.allow_settings_changes is emulator, any or a list of serials, not {rule!r}")
        if p.devices.get("animations", "normal") not in ("off", "fast", "normal", "default"):
            errors.append(f"{pf}: devices.animations is off, fast, normal or default, not {p.devices['animations']!r}")
        p.app_packages = shaped("app_packages", list, "a list of package names")
        if not all(isinstance(x, str) and _PACKAGE.fullmatch(x) for x in p.app_packages):
            errors.append(f"{pf}: app_packages is a list of package names like com.example.app.web, "
                          f"not {p.app_packages!r}")
            p.app_packages = []
        if raw.get("project"):
            p.project = (root / str(raw["project"])).resolve()
        taps = shaped("tap_all", dict, "a map with allowed and deny")
        p.errors += errors
        p.tap_allowed = taps.get("allowed", True) if isinstance(raw.get("tap_all", {}), (dict, type(None))) else False
        if not isinstance(p.tap_allowed, bool):
            p.errors.append(f"{pf}: tap_all.allowed is true or false, not {p.tap_allowed!r}")
            p.tap_allowed = False  # unclear: never tap everything
        deny = taps.get("deny") or []
        if not isinstance(deny, list):
            p.errors.append(f"{pf}: tap_all.deny is a list of rules, like [{{text: \"/(?i)delete/\"}}], not {deny!r}")
            deny = []
        for r in deny:
            try:
                p.deny.append(Rule.parse(r))
            except (ValueError, re.error) as e:
                p.errors.append(f"{pf}: tap_all.deny: {e}")
        if p.driver and p.driver not in DRIVERS:  # a missing one is asked for, never guessed (cli.ask_driver)
            p.errors.append(f"{pf}: driver is the team's tool, one of {', '.join(DRIVERS)}, not {p.driver}")
        p.map = AppMap.load(root / "map")
        p.errors += p.map.errors
        for folder, into in (("journeys", p.journeys), ("drafts", p.drafts)):
            for f in sorted((root / folder).glob("**/*.y*ml")):
                js, errs = load_journeys(f)
                p.errors += errs
                for j in js:
                    other = p.journeys.get(j.name) or p.drafts.get(j.name)
                    if other:
                        p.errors.append(f"{f}:{j.line}: journey {j.name} is also in {other.file}")
                    else:
                        into[j.name] = j
        sf = root / "sets.yaml"
        if sf.exists():
            p.sets = yamlio.strip_lines(yamlio.load_map(sf, p.errors).get("sets") or {})
            if not isinstance(p.sets, dict):
                p.errors.append(f"{sf}: sets is a map of set names, not {type(p.sets).__name__}")
                p.sets = {}
        p._check_fake_app()
        p._check()
        return p

    def _check_fake_app(self) -> None:
        """fake/app.yaml, when there is one, must load: `run --driver fake` reads it."""
        f = self.root / "fake" / "app.yaml"
        if not f.exists():
            return
        app = yamlio.strip_lines(yamlio.load_map(f, self.errors))
        if not app:
            return
        screens = app.get("screens")
        if not isinstance(screens, dict) or not screens:
            self.errors.append(f"{f}: screens is a map of NAME: {{elements: [...]}}")
            return
        if not isinstance(app.get("start"), str) or app.get("start") not in screens:
            self.errors.append(f"{f}: start names a screen in it, not {app.get('start')!r}")
        for name, sd in screens.items():
            els = (sd or {}).get("elements") or [] if isinstance(sd, dict) else None
            if not isinstance(els, list) or not all(isinstance(e, dict) for e in els):
                self.errors.append(f"{f}: screen {name}: elements is a list of maps")
                continue
            for e in els:
                bad = [k for k in ("kind", "text", "id") if k in e and not isinstance(e[k], str)]
                if bad or e.get("kind", "Text") not in KINDS or not isinstance(e.get("state", []), list):
                    self.errors.append(f"{f}: screen {name}: element {e} needs text and id as quoted text, kind one "
                                       f"of {', '.join(KINDS)} and state as a list")
            targets = [e.get("to") for e in els if e.get("to") not in (None, "CRASH")] + [sd.get("back")]
            for t in targets:
                if t is not None and (not isinstance(t, str) or t not in screens):
                    self.errors.append(f"{f}: screen {name} points to unknown screen {t}")

    def _check(self) -> None:
        declared = self.declared()
        for e in self.map.edges:
            for name in sorted(set(_names(e.do)) - declared):
                self.errors.append(f"map/edges.yaml: edge {e.src} -> {e.dst} uses ${{{name}}}, which pack.yaml does "
                                   "not declare (env: or accounts:)")
        for j in [*self.journeys.values(), *self.drafts.values()]:
            if j.name == "all" or j.name in self.sets or not _JOURNEY_NAME.fullmatch(j.name):
                self.errors.append(f"{j.file}:{j.line}: journey name {j.name!r}: lowercase words with dashes, "
                                   "not `all` or a set's name")
            here = None  # the screen the journey is on, as far as the file says
            for s in j.steps:
                at = f"{j.file}:{s.line}"
                for name in sorted(set(_names(s.arg)) - declared):
                    self.errors.append(f"{at}: ${{{name}}} is not declared in pack.yaml (env: or accounts:)")
                if (s.kind in ("expect", "goto") and not isinstance(s.arg, str)) or (
                        s.kind == "input" and (not isinstance(s.arg, str) or s.arg == "")):
                    self.errors.append(f"{at}: {s.kind} takes one text, not {s.arg!r}"
                                       + ("" if s.arg in ("", None) or s.kind != "input" else f"; {QUOTE}"))
                    here = None
                    continue
                if s.kind in ("hook", "event") and not isinstance(s.arg, (str, dict)):
                    self.errors.append(f"{at}: {s.kind} takes a name or a map, not {s.arg!r}")
                    continue
                if s.kind in ("expect", "goto") and s.arg not in self.map.screens:
                    self.errors.append(f"{at}: {s.kind} names unknown screen {s.arg}")
                    here = None
                elif s.kind == "goto" and here and self.map.path(here, s.arg) is None:
                    self.errors.append(f"{at}: goto {s.arg}: the map has no path from {here}")
                if s.kind in ("expect", "goto"):
                    here = s.arg if s.arg in self.map.screens else None
                elif s.kind in ("do", "input", "hook"):
                    here = None  # an action may move to another screen
                try:
                    if s.kind == "do":
                        to_commands(s.arg)
                    elif s.kind in ("see", "not_see"):
                        to_selector(s.arg)
                except ActionError as e:
                    self.errors.append(f"{at}: {e}")
                if s.kind == "hook":
                    name = s.arg if isinstance(s.arg, str) else (s.arg or {}).get("name")
                    if not isinstance(name, str) or name not in self.hooks:
                        self.errors.append(f"{at}: hook {name} is not in pack.yaml hooks")
                if s.kind == "event" and not self.events:
                    self.errors.append(f"{at}: event step needs events.check in pack.yaml")
        for name, spec in self.sets.items():
            if name == "all":
                self.errors.append("sets.yaml: `all` is every journey already; name the set something else")
            if name in self.journeys:
                self.errors.append(f"sets.yaml: set {name} has the same name as a journey")
            if not isinstance(spec, dict) or any(
                    not isinstance(spec.get(k) or [], list) or not all(isinstance(x, str) for x in spec.get(k) or [])
                    for k in ("journeys", "tags")):
                self.errors.append(f"sets.yaml: set {name} is a map with journeys: [names] and/or tags: [tags]")
                self.sets[name] = {}
                continue
            for jn in spec.get("journeys") or []:
                if jn not in self.journeys:
                    self.errors.append(f"sets.yaml: set {name} names unknown journey {jn}")
            if not self.select(name):
                self.errors.append(f"sets.yaml: set {name} selects no journey")

    def owns(self, package: str) -> bool:
        """The pack's app on screen: its package, its app_packages, or Android's permission dialog."""
        return package == self.app or package in self.app_packages or package.endswith(".permissioncontroller")

    def run_errors(self, draft: str | None = None) -> list[str]:
        """Errors that stop a run: all but other drafts' (an unfinished draft never stops a set)."""
        drafts = str(self.root / "drafts") + "/"
        mine = str(self.drafts[draft].file) if draft in self.drafts else None
        return [e for e in self.errors if not e.startswith(drafts) or (mine and e.startswith(mine))]

    def select(self, target: str, draft: bool = False) -> list[Journey]:
        """A journey name, a set name, or `all`. Drafts run only when named with draft=True."""
        if draft:
            if target not in self.drafts:
                raise PackError(f"no draft named {target} (drafts: {', '.join(self.drafts) or 'none'})")
            return [self.drafts[target]]
        if target == "all":
            return list(self.journeys.values())
        if target in self.journeys:
            return [self.journeys[target]]
        spec = self.sets.get(target)
        if not isinstance(spec, dict):
            hint = f" ({target} is a draft: add --draft)" if target in self.drafts else ""
            raise PackError(f"no journey or set named {target}{hint}")
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

    Undeclared ${...} stay as written; `validate` refuses them in a pack."""
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

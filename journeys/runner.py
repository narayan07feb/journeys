"""Runs journeys on a driver, step by step, and records what happened."""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from .drivers.base import Driver, DriverError
from .journey import Journey, Step
from .pack import Pack, substitute
from .results import Run, Store


class StepFailed(Exception):
    pass


@dataclass
class StepOutcome:
    index: int
    text: str
    status: str
    ms: int
    message: str = ""
    screen: str | None = None


@dataclass
class JourneyOutcome:
    name: str
    status: str = "running"
    steps: list[StepOutcome] = field(default_factory=list)
    failure: str | None = None
    message: str = ""


class Runner:
    def __init__(
        self, pack: Pack, driver: Driver, store: Store, run: Run, *,
        serial: str | None = None, variables: dict[str, str] | None = None,
        settle_tries: int = 3, sleep: Callable[[float], None] = time.sleep,
        on_update: Callable[[JourneyOutcome], None] | None = None,
    ):
        self.pack, self.driver, self.store, self.run = pack, driver, store, run
        self.serial = serial
        self.vars = pack.variables() if variables is None else variables
        self.settle_tries, self.sleep, self.on_update = settle_tries, sleep, on_update

    def run_all(self, journeys: list[Journey]) -> list[JourneyOutcome]:
        outs = [self.journey(j) for j in journeys]
        failed = any(o.status == "failed" for o in outs)
        self.run.data.update(status="failed" if failed else "passed", ended=time.time())
        self.run.save()
        return outs

    def journey(self, j: Journey) -> JourneyOutcome:
        out = JourneyOutcome(j.name)
        self.run.data["journeys"].append(asdict(out))
        steps = j.steps
        if j.launch:
            steps = [Step("do", [{"launchApp": {"clearState": True}} if j.clear_state else "launchApp"], j.line)] + steps
        for i, step in enumerate(steps):
            t0 = time.monotonic()
            try:
                screen = self._step(step)
                out.steps.append(StepOutcome(i, step.describe(), "passed", _ms(t0), screen=screen))
            except (StepFailed, DriverError) as e:
                msg = str(e)
                if not self._alive():
                    msg += " (the app is not running: crash?)"
                out.steps.append(StepOutcome(i, step.describe(), "failed", _ms(t0), msg))
                out.status, out.message = "failed", msg
                out.failure = self._record_failure(j, i, step, msg)
                break
            self._publish(out)
        else:
            out.status = "passed"
        self._publish(out)
        return out

    # steps
    def _step(self, step: Step) -> str | None:
        arg = self._sub(step.arg)
        if step.kind == "expect":
            return self._expect(arg)
        if step.kind == "goto":
            return self._goto(arg)
        if step.kind == "see":
            self._do([{"assertVisible": arg}])
        elif step.kind == "not_see":
            self._do([{"assertNotVisible": arg}])
        elif step.kind == "do":
            self._do(arg if isinstance(arg, list) else [arg])
        elif step.kind == "input":
            self._do([{"inputText": str(arg)}])
        elif step.kind == "hook":
            self._hook(arg)
        elif step.kind == "event":
            self._event(arg)
        return None

    def _sub(self, arg):
        try:
            return substitute(arg, self.vars, self.pack.declared())
        except KeyError as e:
            raise StepFailed(f"env var {e.args[0]} is not set") from None

    def _do(self, commands: list) -> None:
        res = self.driver.do(commands)
        if not res.ok:
            raise StepFailed(res.message or "command failed")

    def current(self) -> str | None:
        return self.pack.map.identify(self.driver.look()).name

    def _settled_on(self, want: str) -> str | None:
        """Look a few times (the app may still be moving); return the screen seen last."""
        name = None
        for attempt in range(self.settle_tries):
            name = self.current()
            if name == want:
                return name
            if attempt + 1 < self.settle_tries:
                self.sleep(0.5)
        return name

    def _expect(self, want: str) -> str:
        got = self._settled_on(want)
        if got != want:
            raise StepFailed(f"expected screen {want}, on {got or 'an unknown screen'}")
        return got

    def _goto(self, want: str) -> str:
        here = self.current()
        if here is None:
            raise StepFailed(f"goto {want}: on an unknown screen")
        path = self.pack.map.path(here, want)
        if path is None:
            raise StepFailed(f"goto {want}: the map has no path from {here}")
        for edge in path:
            self._do(edge.do)
            got = self._settled_on(edge.dst)
            if got != edge.dst:
                raise StepFailed(f"edge {edge.src} -> {edge.dst} landed on {got or 'an unknown screen'}")
        return want

    def _env(self) -> dict[str, str]:
        return {**os.environ, "JOURNEYS_APP": self.pack.app, "JOURNEYS_SERIAL": self.serial or "",
                "JOURNEYS_PACK": str(self.pack.root)}

    def _script(self, argv: list[str], payload) -> subprocess.CompletedProcess:
        argv = [str(self.pack.root / a) if i == 0 and (self.pack.root / a).is_file() else str(a)
                for i, a in enumerate(argv)]
        try:
            return subprocess.run(argv + [json.dumps(payload)], cwd=self.pack.root, env=self._env(),
                                  capture_output=True, text=True, timeout=300)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise StepFailed(f"{argv[0]}: {e}") from None

    def _hook(self, arg) -> None:
        name, args = (arg, {}) if isinstance(arg, str) else (arg.get("name"), arg.get("args") or {})
        p = self._script(self.pack.hooks[name], args)
        if p.returncode != 0:
            raise StepFailed(f"hook {name} exited {p.returncode}: {(p.stderr or p.stdout).strip()[-300:]}")

    def _event(self, arg) -> None:
        p = self._script(self.pack.events, arg)
        lines = p.stdout.strip().splitlines()
        try:
            fired = p.returncode == 0 and bool(json.loads(lines[-1]).get("fired"))
        except (IndexError, ValueError, AttributeError):
            fired = False
        if not fired:
            raise StepFailed(f"event {arg} did not fire")

    # bookkeeping
    def _alive(self) -> bool:
        try:
            return self.driver.app_alive()
        except DriverError:
            return False

    def _record_failure(self, j: Journey, index: int, step: Step, message: str) -> str:
        try:
            screen = self.driver.look()
            listing, name = screen.listing(), self.pack.map.identify(screen).name
        except DriverError as e:
            listing, name = f"(no screen: {e})", None
        try:
            shot = self.driver.screenshot(self.run.dir / f"{j.name}-step{index}.png")
        except DriverError:
            shot = None
        return self.store.add_failure({
            "run": self.run.id, "journey": j.name, "step": index, "step_text": step.describe(),
            "file": str(j.file), "line": step.line or j.line, "message": message,
            "screen": name, "listing": listing, "screenshot": str(shot) if shot else None,
        })

    def _publish(self, out: JourneyOutcome) -> None:
        self.run.data["journeys"][-1] = asdict(out)
        self.run.save()
        if self.on_update:
            self.on_update(out)


def _ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)

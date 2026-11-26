"""Runs journeys on a driver, step by step, and records what happened."""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import asdict, dataclass, field
from typing import Callable

from .actions import ActionError, to_commands
from .drivers.base import Driver, DriverError
from .heal import explore, heading, screen_name
from .journey import Journey, Step
from .pack import Pack, substitute
from .results import Run, Store, mask, set_secrets, write_json


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
    changes: list[str] = field(default_factory=list)  # proposals made by detours


class Runner:
    def __init__(
        self, pack: Pack, driver: Driver, store: Store, run: Run, *,
        serial: str | None = None, variables: dict[str, str] | None = None,
        settle_tries: int = 3, sleep: Callable[[float], None] = time.sleep,
        on_update: Callable[[JourneyOutcome], None] | None = None, heal: bool = True,
    ):
        self.pack, self.driver, self.store, self.run = pack, driver, store, run
        self.serial = serial
        self.vars = pack.variables() if variables is None else variables
        set_secrets(self.vars.values())
        self.settle_tries, self.sleep, self.on_update = settle_tries, sleep, on_update
        self.heal = heal
        self._journey: Journey | None = None
        self._step_now: Step | None = None
        self._out: JourneyOutcome | None = None

    def run_all(self, journeys: list[Journey], target: str | None = None) -> list[JourneyOutcome]:
        """`target`: the set being run; its before: hooks run once first, its after: hooks always last."""
        hooks = self.pack.set_hooks(target)
        outs: list[JourneyOutcome] = []
        try:
            try:
                if self._set_hooks("before", hooks.get("before", []), stop_at_failure=True):
                    outs = [self.journey(j) for j in journeys]
            finally:  # also after a failed before:, a failed journey, Ctrl-C or a lost link
                self._set_hooks("after", hooks.get("after", []))
        except BaseException as e:  # a lost link or Ctrl-C: the run ends as failed, never stays "running"
            if self._out is not None and self._out.status == "running":  # live.json and Droid Lite too
                stop = self._mask(str(e)) or type(e).__name__  # keep a cleanup error noted before the stop
                self._out.status, self._out.message = "failed", f"{stop} ({self._out.message})" if self._out.message else stop
                try:
                    self._publish(self._out)
                except Exception:  # the stop itself must still be recorded below
                    pass
            for j in self.run.data.get("journeys", []):
                if j.get("status") == "running":
                    j["status"] = "failed"
            self.run.data.update(status="failed", ended=time.time(), error=self._mask(str(e)) or type(e).__name__)
            self.run.save()
            raise
        bad = [h for h in self.run.data.get("hooks", []) if h["status"] == "failed"]
        failed = bool(bad) or any(o.status == "failed" for o in outs)
        if bad:
            self.run.data["error"] = "; ".join(f"set {h['phase']}: {h['message']}" for h in bad)
        self.run.data.update(status="failed" if failed else "passed", ended=time.time())
        self.run.save()
        return outs

    def journey(self, j: Journey) -> JourneyOutcome:
        out = JourneyOutcome(j.name)
        self._journey, self._out = j, out
        self.run.data["journeys"].append(asdict(out))
        steps = j.steps
        if j.launch:
            steps = [Step("do", [{"launch": {"clear_state": True}} if j.clear_state else "launch"], j.line)] + steps
        self._total = len(steps) + len(j.after)
        try:
            self._steps(j, out, steps)
        finally:  # cleanup runs whatever happened, so a server flag never stays set
            self._after(j, out)
        self._publish(out)
        return out

    def _steps(self, j: Journey, out: JourneyOutcome, steps: list[Step]) -> None:
        for i, step in enumerate(steps):
            t0 = time.monotonic()
            self._step_now = step
            try:
                screen = self._step(step)
                out.steps.append(StepOutcome(i, step.describe(), "passed", _ms(t0), screen=screen))
            except (StepFailed, DriverError, ActionError) as e:
                msg = self._mask(str(e))
                try:
                    if not self.driver.app_alive():
                        msg += " (the app is not running: crash?)"
                except DriverError as d:  # the device itself is gone: not a crash
                    msg += f" (the device does not answer: {self._mask(str(d))})"
                out.steps.append(StepOutcome(i, step.describe(), "failed", _ms(t0), msg))
                out.status, out.message = "failed", msg
                out.failure = self._record_failure(j, i, step, msg)
                if self.heal:
                    try:
                        self._explore_failure(out.failure, step)
                    except DriverError as ex:  # the failure stands; explore just could not finish
                        self.store.update_failure(out.failure, explore_error=str(ex))
                break
            self._publish(out)
        else:
            out.status = "passed"

    def _after(self, j: Journey, out: JourneyOutcome) -> None:
        """Run every after: hook, even when one fails; a failed one fails a journey that passed."""
        errors = []
        for ref in j.after:
            t0 = time.monotonic()
            text = f"after: hook {ref}"
            try:
                self._hook(self._sub(ref))
                out.steps.append(StepOutcome(len(out.steps), text, "passed", _ms(t0)))
            except Exception as e:  # noqa: BLE001  every cleanup gets its turn
                msg = self._mask(str(e))
                errors.append(msg)
                out.steps.append(StepOutcome(len(out.steps), text, "failed", _ms(t0), msg))
            self._publish(out)  # a slow cleanup shows on the live page too
        if not errors:
            return
        note = "after: " + "; ".join(errors)
        if out.status == "passed":
            out.status, out.message = "failed", note
            out.failure = self.store.add_failure({
                "run": self.run.id, "journey": j.name, "step": len(out.steps) - 1, "step_text": "after",
                "file": str(j.file), "line": j.line, "message": note, "screen": None, "listing": "",
                "screenshot": None,
            })
        else:  # the first failure stays the journey's failure; the cleanup error is added to it
            out.message = f"{out.message} ({note})" if out.message else note

    def _set_hooks(self, phase: str, refs: list, stop_at_failure: bool = False) -> bool:
        """A set's before: or after: hooks, recorded in the run. False when one failed."""
        ok = True
        for ref in refs:
            entry = {"phase": phase, "hook": ref, "status": "passed", "message": ""}
            try:
                self._hook(self._sub(ref))
            except Exception as e:  # noqa: BLE001  every after: hook gets its turn
                entry.update(status="failed", message=self._mask(str(e)))
                ok = False
            self.run.data.setdefault("hooks", []).append(entry)
            self.run.save()
            if not ok and stop_at_failure:
                break
        return ok

    # steps
    def _step(self, step: Step) -> str | None:
        arg = self._sub(step.arg)
        if step.kind == "expect":
            return self._expect(arg)
        if step.kind == "goto":
            return self._goto(arg)
        if step.kind == "see":
            self._do(to_commands({"see": arg}))
        elif step.kind == "not_see":
            self._do(to_commands({"not_see": arg}))
            try:
                fg = self.driver.foreground_app()
            except DriverError:
                fg = None
            if fg is not None and not self.pack.owns(fg):
                raise StepFailed(f"not_see {arg}: {self.pack.app} is not on screen ({fg} is), so it proves nothing")
        elif step.kind == "do":
            self._do(to_commands(arg))
        elif step.kind == "input":
            self._do([{"inputText": str(arg)}])
        elif step.kind == "hook":
            self._hook(arg)
        elif step.kind == "event":
            self._event(arg)
        return None

    def _mask(self, text: str) -> str:
        return mask(text)

    def _sub(self, arg):
        try:
            return substitute(arg, self.vars, self.pack.declared())
        except KeyError as e:
            raise StepFailed(f"env var {e.args[0]} is not set") from None

    def _do(self, commands: list) -> None:
        """Commands in the drivers' form (see actions.py); pack actions go through to_commands first."""
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
        if got == want:
            return got
        if self.heal and got and self.pack.map.path(got, want) is not None:
            self._walk(got, want)
            j, step = self._journey, self._step_now
            self._propose(f"{j.name}: on {got} where {want} was expected; reached it by the map",
                          {"kind": "expect-to-goto", "file": str(j.file), "line": step.line, "screen": want})
            return want
        raise StepFailed(f"expected screen {want}, on {got or 'an unknown screen'}")

    def _goto(self, want: str) -> str:
        here = self.current()
        if here is None:
            raise StepFailed(f"goto {want}: on an unknown screen")
        if self.pack.map.path(here, want) is None:
            raise StepFailed(f"goto {want}: the map has no path from {here}")
        self._walk(here, want)
        return want

    def _walk(self, here: str, want: str) -> None:
        """Follow map edges to `want`. An edge that lands on another known screen with a way on is
        a detour: carry on from there. Its proposal is saved only once `want` is reached."""
        pending, broken = [], {}
        while here != want:
            for edge in self.pack.map.path(here, want) or []:
                if (edge.src, edge.dst) in broken:
                    raise StepFailed(f"edge {edge.src} -> {edge.dst} landed on "
                                     f"{broken[edge.src, edge.dst]} (again, after a detour)")
                self._do(to_commands(self._sub(edge.do)))  # ${NAME} in edges gets its value too
                got = self._settled_on(edge.dst)
                if got == edge.dst:
                    here = got
                    continue
                if self.heal and got and self.pack.map.path(got, want) is not None:
                    broken[edge.src, edge.dst] = got
                    pending.append((f"edge {edge.src} -> {edge.dst} now lands on {got}",
                                    {"kind": "edge-to", "src": edge.src, "old": edge.dst, "new": got}))
                    here = got
                    break
                raise StepFailed(f"edge {edge.src} -> {edge.dst} landed on {got or 'an unknown screen'}")
        for summary, change in pending:
            self._propose(summary, change)

    def _propose(self, summary: str, change: dict, **extra) -> str:
        for p in self.store.proposals():  # the same change from an earlier run: reuse it
            if p["status"] == "proposed" and p["change"] == change:
                pid = p["id"]
                break
        else:
            pid = self.store.add_proposal({"summary": summary, "change": change, "run": self.run.id,
                                           "journey": self._journey.name if self._journey else None, **extra})
        if self._out is not None and pid not in self._out.changes:
            self._out.changes.append(pid)
        return pid

    def _env(self) -> dict[str, str]:
        return {**os.environ, "JOURNEYS_APP": self.pack.app, "JOURNEYS_SERIAL": self.serial or "",
                "JOURNEYS_PACK": str(self.pack.root)}

    def _script(self, argv: list[str], payload) -> subprocess.CompletedProcess:
        argv = [str(self.pack.root / a) if i == 0 and (self.pack.root / a).is_file() else str(a)
                for i, a in enumerate(argv)]
        try:
            return subprocess.run(argv + [json.dumps(payload, default=str)], cwd=self.pack.root, env=self._env(),
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
            listing, name = self._mask(screen.listing()), self.pack.map.identify(screen).name
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

    def _explore_failure(self, fid: str, step: Step) -> None:
        if not self._alive():
            return
        try:
            head = mask(heading(self.driver.look()))  # before explore moves away; never an upper-cased secret
        except DriverError:
            return
        if not self.pack.tap_allowed:
            return
        options = explore(self.driver, self.pack.map, self.pack.owns, self.pack.deny)
        failure = self.store.update_failure(fid, options=options)  # the store masks what it writes
        target = step.arg if step.kind in ("expect", "goto") else None
        here = failure.get("screen")
        if here is None:
            if not head:
                return
            name = screen_name(head)
            edges = [{"from": name, "to": o["leads_to"], "do": [o["tap"]]}
                     for o in options if o.get("leads_to") and o["leads_to"] != "CRASH"]
            pid = self._propose(f"new screen {name} (heading {head!r}) at {self._journey.name}",
                                {"kind": "new-screen", "name": name, "match": {"all": [{"text": head}]},
                                 "edges": edges}, failure=fid)
        else:
            hit = next((o for o in options if target and o.get("leads_to") == target), None)
            if not hit:
                return
            pid = self._propose(f"{here}: {hit['tap']} reaches {target}",
                                {"kind": "new-edge", "src": here, "dst": target, "do": [hit["tap"]]}, failure=fid)
        self.store.update_failure(fid, proposal=pid)

    def _publish(self, out: JourneyOutcome) -> None:
        self.run.data["journeys"][-1] = asdict(out)
        self.run.save()
        last = out.steps[-1] if out.steps else None
        write_json(self.store.root / "live.json", {
            "run": self.run.id, "journey": out.name, "status": out.status,
            "step": f"{len(out.steps)} of {self._total}", "text": last.text if last else "",
            "screen": next((s.screen for s in reversed(out.steps) if s.screen), None),
            "message": out.message, "updated": time.time(),
        })
        if self.on_update:
            self.on_update(out)


def _ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)

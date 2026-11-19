"""Adapter for the Droid Lite Tests tab (docs: Droid Lite PLUGINS.md). Prints the JSON it reads.

Settings come from the environment:
  JOURNEYS_PACK    the pack (default: <project>/journeys-pack if present, else the example pack)
  JOURNEYS_SERIAL  the device; without it only JOURNEYS_DRIVER=fake can run
  JOURNEYS_DRIVER  maestro | speedrun | fake (default: the pack's driver; with neither, ask the user)
  JOURNEYS_STATE   the state folder (default ~/.local/state/journeys/<pack>)
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from .cli import _stop, make_driver, on_kill
from .drivers import DRIVERS
from .drivers.base import DriverError
from .live import panel, watch
from .pack import Pack, PackError
from .results import Store, default_state_dir, mask, set_secrets
from .runner import JourneyOutcome, Runner

REPO = Path(__file__).resolve().parents[1]


def emit(**line) -> None:
    print(json.dumps(line), flush=True)


def pack_path() -> Path:
    if os.environ.get("JOURNEYS_PACK"):
        return Path(os.environ["JOURNEYS_PACK"]).expanduser()
    project = os.environ.get("DROID_LITE_PROJECT")
    if project and (Path(project) / "journeys-pack" / "pack.yaml").exists():
        return Path(project) / "journeys-pack"
    return REPO / "examples" / "settings-pack"


def driver_kind(pack: Pack) -> str | None:
    """JOURNEYS_DRIVER or the pack's driver: the user's answer (the example pack names none)."""
    return os.environ.get("JOURNEYS_DRIVER") or pack.driver or None


ASK = ("no driver: ask the user which tool their team uses (maestro or speedrun), then set "
       "JOURNEYS_DRIVER or driver: in pack.yaml; Journeys never picks one")


def state_store(pack: Pack) -> Store:
    env = os.environ.get("JOURNEYS_STATE")
    return Store(Path(env).expanduser() if env else default_state_dir(pack.name))


def cmd_list(pack: Pack) -> None:
    def case(j):
        return {"id": j.name, "label": j.name, "detail": ", ".join(j.tags), "file": str(j.file), "line": j.line}
    print(json.dumps([
        {"label": f"Journeys ({pack.name})", "id": "all", "children": [case(j) for j in pack.journeys.values()]},
        {"label": "Sets", "children": [
            {"id": f"set:{n}", "label": n, "detail": ", ".join(j.name for j in pack.select(n))} for n in pack.sets]},
    ]))


class _Args:  # what make_driver reads
    fake_app = None


def cmd_run(pack: Pack, case_id: str) -> int:
    on_kill()  # a Tests-tab stop (SIGTERM) still ends the run as failed
    target = case_id.removeprefix("set:")
    if pack.run_errors():
        for e in pack.run_errors():
            emit(label="pack", status="failed", message=e)
        return 1
    journeys = pack.select(target)
    if not journeys:  # nothing to test is not a pass
        raise PackError(f"{target}: no journeys to run")
    serial = os.environ.get("JOURNEYS_SERIAL") or None
    kind = driver_kind(pack)
    if not kind:
        emit(case=case_id, status="failed", message=ASK)
        return 1
    if kind not in DRIVERS:
        emit(case=case_id, status="failed", message=f"driver {kind} is not one of {', '.join(DRIVERS)}")
        return 1
    if kind != "fake" and not serial:
        emit(case=case_id, status="failed", message="set JOURNEYS_SERIAL (or JOURNEYS_DRIVER=fake)")
        return 1
    store = state_store(pack)
    set_secrets(pack.variables().values())  # before any driver message can carry one
    driver = make_driver(kind, pack, _Args(), store)
    driver.start(pack.app, serial)  # a refused start leaves no empty run behind
    files = {j.name: j for j in pack.journeys.values()}

    def update(o: JourneyOutcome) -> None:
        if o.status == "running":
            if len(o.steps) <= 1:
                emit(case=o.name, status="running")
            return
        j, line = files[o.name], {}
        if o.failure:
            f = store.failure(o.failure) or {}
            line = {"file": f.get("file"), "line": f.get("line")}
        msg = o.message or (f"changes: {', '.join(o.changes)}" if o.changes else "")
        emit(case=o.name, label=o.name, status=o.status, message=msg,
             file=line.get("file") or str(j.file), line=line.get("line") or j.line)

    run = None
    try:
        run = store.new_run(pack=pack.name, target=target, driver=kind, serial=serial)
        emit(log=f"run {run.id} on {serial or 'fake'}")
        outs = Runner(pack, driver, store, run, serial=serial, on_update=update).run_all(journeys)
    except BaseException as e:  # a stop before the runner took over
        if run is not None and run.data.get("status") == "running":
            run.data.update(status="failed", ended=time.time(), error=str(e) or type(e).__name__)
            run.save()
        raise
    finally:
        _stop(driver)
    ok = all(o.status == "passed" for o in outs)
    if case_id.startswith("set:") or case_id == "all":  # the group's own icon
        emit(case=case_id, status="passed" if ok else "failed")
    return 0 if ok else 1


def cmd_view(store: Store, which: str) -> None:
    if which == "failures":
        items = [{"label": f"{f['id']} {f['journey']}", "status": "failed", "message": f["message"],
                  "file": f.get("file"), "line": f.get("line")} for f in store.failures()[-50:][::-1]]
    else:
        items = [{"label": p["id"], "status": "skipped", "message": p["summary"]}
                 for p in store.proposals() if p["status"] == "proposed"]
    print(json.dumps([{k: v for k, v in i.items() if v is not None} for i in items]))


def cmd_doctor(pack: Pack) -> int:
    from .drivers import availability
    errors = pack.run_errors()  # an unfinished draft is not a broken pack
    emit(label=f"pack {pack.name}", status="failed" if errors else "passed",
         message="; ".join(errors) or f"{len(pack.journeys)} journeys, {len(pack.drafts)} drafts")
    kind = driver_kind(pack)
    missing = availability().get(kind, f"unknown driver {kind}") if kind else ASK
    emit(label=f"driver {kind or '(not chosen)'}", status="failed" if missing else "passed", message=missing or "can run")
    serial = os.environ.get("JOURNEYS_SERIAL")
    emit(label="device", status="passed" if serial else "skipped",
         message=serial or "JOURNEYS_SERIAL not set (fake driver only)")
    return 1 if errors or missing else 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        pack = Pack.load(pack_path())
        store = state_store(pack)
        cmd = argv[0] if argv else ""
        if cmd == "list":
            cmd_list(pack)
        elif cmd == "run" and len(argv) == 2:
            return cmd_run(pack, argv[1])
        elif cmd == "watch":
            for live in watch(store):
                print(json.dumps(panel(live)), flush=True)
        elif cmd in ("failures", "proposals"):
            cmd_view(store, cmd)
        elif cmd == "doctor":
            return cmd_doctor(pack)
        else:
            print("usage: droidlite list | run <id> | watch | failures | proposals | doctor", file=sys.stderr)
            return 2
    except (PackError, DriverError, OSError) as e:
        emit(label="journeys", status="failed", message=mask(str(e)))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

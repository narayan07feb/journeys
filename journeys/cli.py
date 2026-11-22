"""journeys <command>: the command line."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml

from .drivers import DRIVERS, TOOLS, availability
from .drivers.base import Driver, DriverError
from .heal import PromoteError
from .pack import Pack, PackError
from .results import RUN_ID, Store, default_state_dir, mask, set_secrets
from .runner import Runner


def make_driver(kind: str, pack: Pack, args, store: Store) -> Driver:
    """The driver the pack names, or the --driver override. No silent fallback to another one."""
    if kind not in DRIVERS:
        raise DriverError(f"driver {kind} is not one of {', '.join(DRIVERS)}")
    missing = availability(kind)[kind]
    if missing:
        others = [k for k, why in availability().items() if not why and k not in (kind, "fake")]
        raise DriverError(f"driver {kind} cannot run: {missing}"
                          + (f" (these can: {', '.join(others)}; pass --driver)" if others else ""))
    if kind == "speedrun":
        from .drivers.speedrun import SpeedrunDriver, find_speedrun
        return SpeedrunDriver(find_speedrun(), store.root / "speedrun-home",
                              animations=str(pack.devices.get("animations", "normal")),
                              allow_changes=pack.devices.get("allow_settings_changes", "emulator"))
    if kind == "maestro":
        from .drivers.maestro import MaestroDriver
        return MaestroDriver()
    if kind == "appium":
        from .drivers.appium import AppiumDriver
        return AppiumDriver(allow_changes=pack.devices.get("allow_settings_changes", "emulator"))
    app = Path(args.fake_app) if args.fake_app else pack.root / "fake" / "app.yaml"
    if not app.exists():
        raise DriverError(f"{app} not found: the fake driver needs a fake app file")
    from .drivers.fake import FakeDriver
    try:
        return FakeDriver.from_file(app)
    except (yaml.YAMLError, AttributeError, KeyError, TypeError) as e:
        raise DriverError(f"{app}: not a fake app file ({e}); `journeys validate` checks the pack's own") from None


def _load(args) -> Pack:
    if not args.pack:
        raise PackError("no pack: pass --pack <folder> or set JOURNEYS_PACK")
    return Pack.load(args.pack)


def _log(args, pack: Pack, action: str, **detail) -> None:
    """The tracker's activity line for work already done: never a reason for that command to fail."""
    try:
        _store(args, pack).log(action, **detail)
    except Exception:
        pass


def _store(args, pack: Pack) -> Store:
    set_secrets(pack.variables().values())  # every store write, the activity log too, masks env values
    return Store(Path(args.state).expanduser() if args.state else default_state_dir(pack.name))


def cmd_doctor(args) -> int:
    rows: list[tuple[str, str]] = []
    try:
        pack = _load(args)
        rows.append(("OK", f"pack {pack.name} ({pack.app}): {len(pack.map.screens)} screens, "
                           f"{len(pack.map.edges)} edges, {len(pack.journeys)} journeys"))
        blocking = set(pack.run_errors())
        rows += [("FAIL" if e in blocking else "WARN", e) for e in pack.errors]  # an unfinished draft only warns
    except PackError as e:
        rows.append(("FAIL", str(e)))
    rows.append(("OK" if sys.version_info >= (3, 11) else "FAIL", f"python {sys.version.split()[0]}"))
    wanted = os.environ.get("JOURNEYS_DRIVER") or pack.driver if "pack" in locals() else None
    if wanted and wanted not in DRIVERS:
        rows.append(("FAIL", f"driver {wanted} is not one of {', '.join(DRIVERS)}"))
    if "pack" in locals() and not wanted:
        rows.append(("WARN", f"pack {pack.name} has no driver: ask the team which tool drives their device "
                             f"({TOOLS}) and set driver: in pack.yaml; runs ask until then"))
    if wanted and pack.project:
        from .detect import pick, scan
        try:
            detected, why = pick(scan(pack.project)[0])
            rows.append(("OK" if detected == wanted else "WARN",
                         f"project {pack.project}: {why}" + ("" if detected == wanted else f"; the pack says {wanted}")))
        except FileNotFoundError as e:
            rows.append(("FAIL", str(e)))
    for kind, missing in availability().items():
        if kind != "fake":
            level = "OK" if not missing else "FAIL" if kind == wanted else "WARN"
            tag = " (chosen)" if kind == wanted else ""
            rows.append((level, f"driver {kind}{tag}: {missing or 'can run'}"))
    adb = shutil.which("adb")
    rows.append(("OK", f"adb {adb}") if adb else ("WARN", "adb not on PATH"))
    if adb:
        try:
            out = subprocess.run([adb, "devices"], capture_output=True, text=True, timeout=30).stdout
        except (OSError, subprocess.TimeoutExpired) as e:
            out = ""
            rows.append(("WARN", f"adb devices: {e}"))
        devices = [ln.split()[0] for ln in out.splitlines()[1:] if ln.strip().endswith("device")]
        rows.append(("OK", f"devices: {', '.join(devices)}") if devices else ("WARN", "no device on adb"))
    for level, text in rows:
        print(f"{level:4} {text}")
    return 1 if any(level == "FAIL" for level, _ in rows) else 0


def cmd_list(args) -> int:
    pack = _load(args)
    if args.json:
        print(json.dumps({
            "sets": {n: [j.name for j in pack.select(n)] for n in pack.sets},
            "journeys": [{"name": j.name, "tags": j.tags, "file": str(j.file), "line": j.line}
                         for j in pack.journeys.values()],
            "drafts": [{"name": j.name, "file": str(j.file), "line": j.line} for j in pack.drafts.values()],
        }, indent=2))
        return 0
    for n in pack.sets:
        print(f"set {n}: {', '.join(j.name for j in pack.select(n))}")
    for j in pack.journeys.values():
        print(f"{j.name}  [{', '.join(j.tags)}]  {j.file.relative_to(pack.root)}:{j.line}")
    for j in pack.drafts.values():
        print(f"draft {j.name}  {j.file.relative_to(pack.root)}:{j.line}  (run with --draft)")
    return 0


def _at_least_1(text: str) -> int:
    n = int(text)
    if n < 1:
        raise argparse.ArgumentTypeError(f"{n}: at least 1 (0 would test nothing and still pass)")
    return n


def _run_id(text: str) -> str:
    if not RUN_ID.fullmatch(text):
        raise argparse.ArgumentTypeError(f"bad run id {text!r}: letters, digits and dashes")
    return text


def _refuse_errors(pack: Pack) -> None:
    errors = pack.run_errors()
    if errors:
        raise PackError("\n".join(errors) + f"\n{len(errors)} pack errors: fix them first (journeys validate)")


def _stop(driver: Driver) -> None:
    """Stop the driver; a failed stop is a warning, never a reason to hide the results already saved."""
    try:
        driver.stop()
    except DriverError as e:
        print(f"WARN driver stop: {mask(str(e))}", file=sys.stderr)


def ask_driver(why: str) -> str:
    """The user's own answer: Journeys never assumes a driver (no default, not even from the project)."""
    tools = " or ".join(d for d in DRIVERS if d != "fake")
    question = f"Which tool does your team use to drive the device ({tools})? Journeys never picks one."
    if not sys.stdin.isatty():
        raise DriverError(f"{question} {why}: ask the team, then pass --driver <{'|'.join(DRIVERS)}>")
    print(f"{question}\n{why}", file=sys.stderr)
    while True:
        try:
            answer = input("driver: ").strip()
        except (EOFError, KeyboardInterrupt):
            raise DriverError(f"no driver given. {question} Pass --driver <{'|'.join(DRIVERS)}>") from None
        if answer in DRIVERS:
            return answer
        print(f"{answer!r} is not one of {', '.join(DRIVERS)}", file=sys.stderr)


def _open(args, pack: Pack) -> tuple[Store, Driver]:
    args.driver = kind = args.driver or os.environ.get("JOURNEYS_DRIVER") or pack.driver or ask_driver(
        f"pack {pack.name} has no driver: in pack.yaml (add it there to stop this question)")
    if kind != "fake" and not args.serial:
        raise DriverError("pass --serial <adb serial>: Journeys never picks a device by itself")
    store = _store(args, pack)
    set_secrets(pack.variables().values())  # every command that writes to the store masks env values
    return store, make_driver(kind, pack, args, store)


def cmd_tap_all(args) -> int:
    from .explore import TapAllReport, dka_allowed, tap_all
    pack = _load(args)
    _refuse_errors(pack)  # a broken deny rule must not leave its buttons tappable
    if not pack.tap_allowed:
        raise PackError(f"pack {pack.name} sets tap_all.allowed: false (tapping everything is not safe in this app)")
    store, driver = _open(args, pack)
    driver.start(pack.app, args.serial)  # a refused start leaves no empty run behind
    try:
        if args.dka and not dka_allowed(pack, driver, args.serial):
            raise DriverError("--dka changes a device setting; pack.yaml devices.allow_settings_changes "
                              "does not allow it on this device")
        run = store.new_run(pack=pack.name, target="tap-all", driver=args.driver,
                            serial=args.serial, dka=args.dka)
        rep = tap_all(pack, driver, store, run, serial=args.serial, dka=args.dka,
                      max_screens=args.max_screens, max_taps=args.max_taps)
    finally:
        _stop(driver)
    rep = TapAllReport(**mask(rep.__dict__))  # the console never shows what the store masks
    print(f"run {run.id}: {len(rep.screens)} screens walked, {len(rep.crashes)} crashes, "
          f"{len(rep.dead_ends)} dead ends, {len(rep.new_screens)} new screens")
    for c in rep.crashes:
        print(f"crash   {c['failure']}  {c['screen']}  {c['tap']}")
    for d in rep.dead_ends:
        print(f"dead end  {d['from']} -> {d['screen']}  {d['tap']}")
    for n in rep.new_screens:
        print(f"new     {n}")
    for x in rep.left_app:
        print(f"left app  {x['screen']}  {x['tap']} -> {x['package']}")
    for u in rep.unreachable:
        print(f"unreachable  {u}")
    for s in rep.screens:
        for o in s["options"]:
            if o["result"] == "failed":
                print(f"tap failed  {s['screen']}  {o['tap']}: {o.get('note', '')}")
    status = store.run(run.id)
    if status.get("dka_restore_error"):  # the device still has "don't keep activities" on
        print(f"dka not restored: {status['dka_restore_error']}")
        return 1
    for n in status.get("not_visited", []):
        print(f"not visited (--max-screens)  {n}")
    if status.get("error"):
        print(f"failed: {status['error']}")
    return 0 if status["status"] == "passed" else 1


def cmd_run(args) -> int:
    pack = _load(args)
    journeys = pack.select(args.target, draft=args.draft)
    if not journeys:  # a CI job that finds nothing to test must not go green
        raise PackError(f"{args.target}: no journeys to run")
    errors = pack.run_errors(args.target if args.draft else None)
    if errors:
        for e in errors:
            print(f"FAIL {e}", file=sys.stderr)
        return 2
    store, driver = _open(args, pack)
    driver.start(pack.app, args.serial)  # a refused start leaves no empty run behind
    run = None
    try:
        run = store.new_run(args.run_id, pack=pack.name, target=args.target, driver=args.driver,
                            serial=args.serial)
        print(f"run {run.id}: {len(journeys)} journeys, results in {run.dir}")
        outs = Runner(pack, driver, store, run, serial=args.serial, heal=not args.no_heal).run_all(journeys)
    except BaseException as e:  # a stop before the runner took over (its own stops end the run itself)
        if run is not None and run.data.get("status") == "running":
            run.data.update(status="failed", ended=time.time(), error=str(e) or type(e).__name__)
            run.save()
        raise
    finally:
        _stop(driver)
    for o in outs:
        line = f"{o.status:7} {o.name}"
        if o.changes:
            line += f"  (changes: {', '.join(o.changes)})"
        if o.failure:
            line += f"  {o.failure}: {o.message}"
        print(line)
    passed = sum(o.status == "passed" for o in outs)
    print(f"{passed}/{len(outs)} passed")
    return 0 if passed == len(outs) else 1


def cmd_failures(args) -> int:
    store = _store(args, _load(args))
    if args.id:
        f = store.failure(args.id)
        if not f:
            print(f"no failure {args.id}", file=sys.stderr)
            return 1
        print(json.dumps(f, indent=2))
        return 0
    for f in store.failures()[-args.last:]:
        print(f"{f['id']}  {f['journey']} step {f['step']} ({f['step_text']}): {f['message']}")
    return 0


def cmd_proposals(args) -> int:
    store = _store(args, _load(args))
    if args.id:
        p = store.proposal(args.id)
        if not p:
            print(f"no proposal {args.id}", file=sys.stderr)
            return 1
        print(json.dumps(p, indent=2))
        return 0
    for p in store.proposals():
        if args.all or p["status"] == "proposed":
            print(f"{p['id']}  {p['status']:9} {p['summary']}")
    return 0


def cmd_promote(args) -> int:
    from .heal import promote
    pack = _load(args)
    print(promote(pack, _store(args, pack), args.id, args.name))
    return 0


def cmd_reject(args) -> int:
    from .heal import reject
    reject(_store(args, _load(args)), args.id, args.why)
    print(f"{args.id} rejected")
    return 0


def cmd_history(args) -> int:
    hist = _store(args, _load(args)).history(args.last)
    if args.json:
        print(json.dumps(hist, indent=2))
        return 0
    for name, h in sorted(hist.items()):
        marks = "".join("." if r == "passed" else "F" for r in h["results"])
        print(f"{name:30} {marks:12} {h['passed']}/{len(h['results'])} passed{'  FLAKY' if h['flaky'] else ''}")
    return 0


def cmd_tracker(args) -> int:
    from .tracker import _t, summary
    pack = _load(args)
    t = summary(_store(args, pack), list(pack.drafts))
    if args.json:
        print(json.dumps(t, indent=1, default=str))
        return 0
    print(f"runs: {len(t['runs'])} recent, {sum(r['status'] == 'passed' for r in t['runs'])} passed")
    q = t["queue"]
    print(f"work queue: {q['failures_open']} open failures, {len(q['proposals'])} open proposals, "
          f"{len(q['drafts'])} drafts")
    print("agent usage (drafts / journeys built / proposals decided / issues solved / runs / tool calls):")
    for who, u in t["usage"]["actors"].items():
        fix = f", avg fix {u['avg_fix_minutes']} min" if u["avg_fix_minutes"] is not None else ""
        print(f"  {who}: {u['drafts']} / {u['journeys_built']} / {u['decided']} / {u['solved']} / {u['runs']} / "
              f"{u['tool_calls']}{fix}")
    for a in t["activity"][:10]:
        print(f"  {time.strftime('%m-%d %H:%M', time.localtime(_t(a.get('time'))))}  {a.get('actor')}  "
              f"{a.get('action')}  {a.get('target') or a.get('journey') or a.get('id') or a.get('tool') or ''}")
    return 0


def cmd_serve(args) -> int:
    from .live import serve
    pack = _load(args)

    def drafts() -> list[str]:
        try:
            return list(Pack.load(pack.root).drafts)
        except PackError:
            return []
    srv = serve(_store(args, pack), args.port, drafts)
    print(f"live page: http://127.0.0.1:{srv.server_address[1]}/  (Ctrl-C stops it)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


def cmd_watch(args) -> int:
    from .live import panel, watch
    for live in watch(_store(args, _load(args))):
        print(json.dumps(panel(live)), flush=True)
    return 0


def cmd_steps(args) -> int:
    """Run the pack's build or install commands in the pack folder; {serial} becomes --serial."""
    pack = _load(args)
    serial = getattr(args, "serial", None)  # build has no --serial
    steps = pack.build if args.cmd == "build" else pack.install
    if not steps:
        print(f"pack.yaml has no {args.cmd} steps")
        return 0
    if args.cmd == "install" and not serial:
        raise DriverError("pass --serial <adb serial>: Journeys never picks a device by itself")
    env = {**os.environ, "JOURNEYS_SERIAL": serial or "", "JOURNEYS_APP": pack.app}
    for argv in steps:
        argv = [str(a).replace("{serial}", serial or "") for a in argv]
        print("$ " + " ".join(argv), flush=True)
        try:
            code = subprocess.run(argv, cwd=pack.root, env=env).returncode
        except OSError as e:
            print(f"{args.cmd} step cannot start: {e}", file=sys.stderr)
            return 1
        if code != 0:
            print(f"{args.cmd} step failed with exit {code}", file=sys.stderr)
            return 1
    return 0


def cmd_mcp(args) -> int:
    from .mcp_server import Server, serve_stdio
    if not args.pack:
        raise PackError("no pack: pass --pack <folder> or set JOURNEYS_PACK")
    serve_stdio(Server(args.pack, args.state))
    return 0


def cmd_init(args) -> int:
    from .detect import init_pack
    if not args.pack:
        raise PackError("pass --pack <new folder> for the new pack")
    dest = Path(args.pack).expanduser()
    project = Path(args.project).expanduser() if args.project else None
    if (dest / "pack.yaml").exists():  # before asking anything
        raise PackError(f"{dest / 'pack.yaml'} already exists: init writes a new pack only")
    if not args.app and not project:
        raise PackError("no app id: pass --app <package>")
    try:
        args.driver = args.driver or os.environ.get("JOURNEYS_DRIVER")
        if not args.driver:
            from .detect import pick, scan
            hint = pick(scan(project, strict=not args.app)[0])[1] if project else "no --project given"
            args.driver = ask_driver(f"a hint only, never a choice: {hint}")
        lines = init_pack(dest, project, args.name or dest.name, args.app, args.driver)
    except (FileExistsError, FileNotFoundError, ValueError) as e:
        raise PackError(str(e)) from None
    print("\n".join(lines))
    return 0


def cmd_look(args) -> int:
    """The screen on the device now, as Journeys reads it, and which map screen it is. Read-only."""
    pack = _load(args)  # pack errors do not stop it: look taps nothing, and it helps fix a screen rule
    for e in pack.run_errors():
        print(f"WARN {e}", file=sys.stderr)
    _, driver = _open(args, pack)
    driver.start(pack.app, args.serial)
    try:
        screen = driver.look()
    finally:
        _stop(driver)
    ident = pack.map.identify(screen)
    print(mask(screen.listing()))  # the console never shows what the store masks
    print(f"-- map screen: {ident.name or 'unknown'}")
    return 0


def cmd_validate(args) -> int:
    try:
        pack = _load(args)
    except PackError as e:  # pack.yaml itself is broken: still a FAIL line, like every other file
        print(f"FAIL {e}")
        return 1
    if not pack.driver:  # not an error: device commands ask for it (or take --driver)
        print(f"WARN {pack.root / 'pack.yaml'}: no driver: ask the team which tool drives their device "
              f"({TOOLS}), then set driver: (Journeys never picks one; runs ask until then)")
    for e in pack.errors:
        print(f"FAIL {e}")
    print(f"{'OK' if not pack.errors else 'FAIL'} pack {pack.name}: {len(pack.journeys)} journeys, "
          f"{len(pack.drafts)} drafts, {len(pack.map.screens)} screens, {len(pack.map.edges)} edges, "
          f"{len(pack.errors)} errors")
    return 1 if pack.errors else 0


def cmd_new(args) -> int:
    from .authoring import new_draft
    pack = _load(args)
    print(f"draft written: {new_draft(pack, args.name, args.start)}")
    _log(args, pack, "draft.new", journey=args.name)
    return 0


def cmd_promote_journey(args) -> int:
    from .authoring import promote_journey
    pack = _load(args)
    print(f"promoted: {promote_journey(pack, args.name)}")
    _log(args, pack, "journey.promote", journey=args.name)
    return 0


def cmd_export(args) -> int:
    from .actions import ActionError
    from .formats import appium, maestro
    export = {"maestro": maestro.export, "appium": appium.export}[args.format]
    pack = _load(args)
    j = pack.journeys.get(args.journey) or pack.drafts.get(args.journey)
    if not j:
        raise PackError(f"no journey or draft named {args.journey}")
    errs = [e for e in pack.errors if e.startswith(f"{j.file}:") or e.startswith(f"{j.file} ")]
    if errs:
        raise PackError(f"{j.name} has errors; `journeys validate` lists them:\n" + "\n".join(errs))
    try:
        flow, notes = export(pack, j)
    except ActionError as e:
        raise PackError(f"{j.name}: {e}") from None
    for n in notes:
        print(f"NOTE {n}", file=sys.stderr)
    if args.out:
        try:
            with open(args.out, "x") as out:  # never over the team's file
                out.write(flow)
        except FileExistsError:
            raise PackError(f"{args.out} exists: remove it first, or print the flow and diff") from None
        except OSError as e:
            raise PackError(f"cannot write {args.out}: {e.strerror}") from None
        print(f"written: {args.out}", file=sys.stderr)
    else:
        print(flow, end="")
    return 0


def cmd_check_sync(args) -> int:
    from .sync import check_sync, flow_dir, source
    pack = _load(args)
    drift = check_sync(pack)
    for x in drift:
        print(f"{x.kind:15} {x.file}" + (f"  (journey {x.journey})" if x.journey else "") + f"  {x.detail}")
    print(f"{'OK' if not drift else 'DRIFT'} {len(drift)} differences between the journeys and "
          f"{flow_dir(pack)} (the truth: {source(pack)})")
    return 1 if drift else 0


def cmd_sync(args) -> int:
    from .sync import sync
    pack = _load(args)
    _refuse_errors(pack)  # a broken journey would be written out as a broken flow
    done, left = sync(pack, dry_run=args.dry_run, prune=args.prune, accept=args.accept, decline=args.decline)
    for line in done:
        print(("would " if args.dry_run else "") + line)
    for x in left:
        print(f"left   {x.kind:15} {x.file}" + (f"  (journey {x.journey})" if x.journey else "") + f"  {x.detail}")
    if done and not args.dry_run:
        _log(args, pack, "sync", changes=len(done), left=len(left))
    changes = sum(not line.startswith("  note") for line in done)
    print(f"{changes} changes{' (dry run)' if args.dry_run else ''}, {len(left)} left for a person")
    return 1 if left else 0


def cmd_import(args) -> int:
    import yaml

    from .actions import ActionError
    from .formats.maestro import import_flow
    pack = _load(args)
    files = [f for p in map(Path, args.paths) for f in (sorted(p.rglob("*.y*ml")) if p.is_dir() else [p])]
    for f in files:
        if f.name in ("config.yaml", "config.yml"):
            continue  # Maestro's workspace config, not a flow
        try:
            draft, notes = import_flow(f, pack.app)
        except (ActionError, yaml.YAMLError, OSError, UnicodeDecodeError) as e:
            print(f"SKIP {f}: {e}")
            continue
        dest = pack.root / "drafts" / f"{draft['name']}.yaml"
        if draft["name"] == "all" or draft["name"] in pack.sets:
            draft["name"] += "-flow"  # `all` and set names cannot name a journey
            dest = pack.root / "drafts" / f"{draft['name']}.yaml"
        if draft["name"] in pack.journeys or draft["name"] in pack.drafts or dest.exists():
            print(f"SKIP {f}: a journey or draft named {draft['name']} exists")
            continue
        dest.parent.mkdir(exist_ok=True)
        dest.write_text(yaml.safe_dump(draft, sort_keys=False, allow_unicode=True))
        print(f"draft {dest}")
        _log(args, pack, "draft.import", journey=draft["name"], source=f.name)
        for n in notes:
            print(f"  NOTE {n}")
    print("next: `journeys validate`, then put expect/goto steps on the map's screens, then promote-journey")
    return 0


def cmd_setup(args) -> int:
    from .setup import install
    dest = Path(__file__).resolve().parents[1] / "third_party" / "speedrun"
    print(f"speedrun installed: {install(dest, args.version)}")
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="journeys", description="End-to-end journeys for Android apps.")
    p.add_argument("--pack", default=os.environ.get("JOURNEYS_PACK"), help="pack folder (or JOURNEYS_PACK)")
    p.add_argument("--state", help="state folder (default ~/.local/state/journeys/<pack>, or JOURNEYS_STATE)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor", help="check the pack and the tools").set_defaults(fn=cmd_doctor)
    sub.add_parser("validate", help="check the pack, every journey and every draft").set_defaults(fn=cmd_validate)
    s = sub.add_parser("look", help="print the screen on the device now and which map screen it is (read-only)")
    s.add_argument("--driver", choices=DRIVERS)
    s.add_argument("--serial", help="adb serial of the device")
    s.add_argument("--fake-app", help=argparse.SUPPRESS)
    s.set_defaults(fn=cmd_look)
    s = sub.add_parser("new", help="write a new draft journey into drafts/")
    s.add_argument("name")
    s.add_argument("--start", help="the screen it starts on (default: the map's first)")
    s.set_defaults(fn=cmd_new)
    s = sub.add_parser("promote-journey", help="move a valid draft into journeys/")
    s.add_argument("name")
    s.set_defaults(fn=cmd_promote_journey)
    s = sub.add_parser("export", help="write a journey as another tool's test (maestro flow, appium WebdriverIO spec)")
    s.add_argument("format", choices=["maestro", "appium"])
    s.add_argument("journey")
    s.add_argument("-o", "--out", help="file to write (default: print)")
    s.set_defaults(fn=cmd_export)
    s = sub.add_parser("sync", help="bring the journeys and the team's flow files (pack.yaml sync:) together")
    s.add_argument("--dry-run", action="store_true", help="say what it would do, change nothing")
    s.add_argument("--prune", action="store_true", help="also delete generated flows whose journey is gone")
    s.add_argument("--accept", action="append", default=[], metavar="FILE",
                   help="a developer flow whose left-out commands you carried into its journey: let sync replace it")
    s.add_argument("--decline", action="append", default=[], metavar="FILE",
                   help="a developer flow whose draft you deleted on purpose: keep it out of the journeys")
    s.set_defaults(fn=cmd_sync)
    sub.add_parser("check-sync", help="list where the journeys and the flow files disagree (exit 1 if any)"
                   ).set_defaults(fn=cmd_check_sync)
    s = sub.add_parser("import", help="turn another tool's tests into draft journeys (maestro)")
    s.add_argument("format", choices=["maestro"])
    s.add_argument("paths", nargs="+", help="flow files or folders")
    s.set_defaults(fn=cmd_import)
    s = sub.add_parser("init", help="start a new pack; asks which driver the team uses")
    s.add_argument("--project", help="the app's project folder (for the app id; its tests are shown as a hint only)")
    s.add_argument("--app", help="the app's package (default: applicationId from the project)")
    s.add_argument("--name", help="pack name (default: the folder name)")
    s.add_argument("--driver", choices=DRIVERS, help="the team's driver (asked when not given; never guessed)")
    s.set_defaults(fn=cmd_init)
    s = sub.add_parser("setup", help="install upstream Speedrun into third_party/")
    s.add_argument("--version", help="release tag, e.g. v0.4.14 (default: latest)")
    s.set_defaults(fn=cmd_setup)
    s = sub.add_parser("list", help="list sets and journeys")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_list)
    s = sub.add_parser("run", help="run a journey, a set, or all")
    s.add_argument("target")
    s.add_argument("--driver", choices=DRIVERS)
    s.add_argument("--serial", help="adb serial of the device")
    s.add_argument("--fake-app", help="fake app file (default <pack>/fake/app.yaml)")
    s.add_argument("--no-heal", action="store_true", help="no detours and no explore taps (the journey's own steps still run)")
    s.add_argument("--draft", action="store_true", help="the target is a draft journey (drafts never run otherwise)")
    s.add_argument("--run-id", type=_run_id, help=argparse.SUPPRESS)  # set by the MCP server for detached runs
    s.set_defaults(fn=cmd_run)
    s = sub.add_parser("tap-all", help="walk the app and tap every option once")
    s.add_argument("--driver", choices=DRIVERS)
    s.add_argument("--serial", help="adb serial of the device")
    s.add_argument("--fake-app", help="fake app file (default <pack>/fake/app.yaml)")
    s.add_argument("--dka", action="store_true", help="turn on don't-keep-activities for the walk")
    s.add_argument("--max-screens", type=_at_least_1, default=20)
    s.add_argument("--max-taps", type=_at_least_1, default=15)
    s.set_defaults(fn=cmd_tap_all)
    s = sub.add_parser("failures", help="list failures, or show one")
    s.add_argument("id", nargs="?")
    s.add_argument("--last", type=int, default=20)
    s.set_defaults(fn=cmd_failures)
    s = sub.add_parser("history", help="recent results per journey, with flaky ones marked")
    s.add_argument("--last", type=int, default=10)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_history)
    s = sub.add_parser("tracker", help="runs, work queue, agent activity and agent usage")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_tracker)
    s = sub.add_parser("serve", help="local tracker page on 127.0.0.1 (live run, runs, queue, agent activity, usage)")
    s.add_argument("--port", type=int, default=8765)
    s.set_defaults(fn=cmd_serve)
    sub.add_parser("watch", help="print the live state as JSON lines").set_defaults(fn=cmd_watch)
    sub.add_parser("mcp", help="serve the MCP tools on stdin/stdout").set_defaults(fn=cmd_mcp)
    sub.add_parser("build", help="run the pack's build steps").set_defaults(fn=cmd_steps)
    s = sub.add_parser("install", help="run the pack's install steps on a device")
    s.add_argument("--serial", help="adb serial of the device ({serial} in the steps)")
    s.set_defaults(fn=cmd_steps)
    s = sub.add_parser("proposals", help="list open proposals, or show one")
    s.add_argument("id", nargs="?")
    s.add_argument("--all", action="store_true", help="also promoted and rejected ones")
    s.set_defaults(fn=cmd_proposals)
    s = sub.add_parser("promote", help="write a proposal into the pack's files")
    s.add_argument("id")
    s.add_argument("--name", help="new-screen proposals: the screen's name")
    s.set_defaults(fn=cmd_promote)
    s = sub.add_parser("reject", help="reject a proposal")
    s.add_argument("id")
    s.add_argument("--why", required=True)
    s.set_defaults(fn=cmd_reject)
    return p


def on_kill() -> None:
    """SIGTERM/SIGHUP raise SystemExit, so a killed run or tap-all still ends as failed, never 'running'."""
    import signal

    def stop(signum, _frame):
        raise SystemExit(128 + signum)
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, stop)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.fn in (cmd_run, cmd_tap_all):
        on_kill()
    try:
        return args.fn(args)
    except (PackError, DriverError, RuntimeError, PromoteError, ValueError, OSError) as e:
        print(f"error: {mask(str(e))}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:  # the run itself is already marked failed
        print("stopped", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())

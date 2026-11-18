"""journeys <command>: the command line."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .drivers import DRIVERS, availability
from .drivers.base import Driver, DriverError
from .heal import PromoteError
from .pack import Pack, PackError
from .results import Store, default_state_dir
from .runner import Runner


def make_driver(kind: str, pack: Pack, args, store: Store) -> Driver:
    """The driver the pack names, or the --driver override. No silent fallback to another one."""
    if kind not in DRIVERS:
        raise DriverError(f"driver {kind} is not one of {', '.join(DRIVERS)}")
    missing = availability()[kind]
    if missing:
        others = [k for k, why in availability().items() if not why and k not in (kind, "fake")]
        raise DriverError(f"driver {kind} cannot run: {missing}"
                          + (f" (these can: {', '.join(others)}; pass --driver)" if others else ""))
    if kind == "uiautomator":
        from .drivers.uiautomator import UiAutomatorDriver
        return UiAutomatorDriver()
    if kind == "speedrun":
        from .drivers.speedrun import SpeedrunDriver, find_speedrun
        return SpeedrunDriver(find_speedrun(), store.root / "speedrun-home",
                              animations=str(pack.devices.get("animations", "normal")),
                              allow_changes=pack.devices.get("allow_settings_changes", "emulator"))
    if kind == "maestro":
        from .drivers.maestro import MaestroDriver
        return MaestroDriver()
    app = Path(args.fake_app) if args.fake_app else pack.root / "fake" / "app.yaml"
    if not app.exists():
        raise DriverError(f"{app} not found: the fake driver needs a fake app file")
    from .drivers.fake import FakeDriver
    return FakeDriver.from_file(app)


def _load(args) -> Pack:
    if not args.pack:
        raise PackError("no pack: pass --pack <folder> or set JOURNEYS_PACK")
    return Pack.load(args.pack)


def _store(args, pack: Pack) -> Store:
    return Store(Path(args.state).expanduser() if args.state else default_state_dir(pack.name))


def cmd_doctor(args) -> int:
    rows: list[tuple[str, str]] = []
    try:
        pack = _load(args)
        rows.append(("OK", f"pack {pack.name} ({pack.app}): {len(pack.map.screens)} screens, "
                           f"{len(pack.map.edges)} edges, {len(pack.journeys)} journeys"))
        rows += [("FAIL", e) for e in pack.errors]
    except PackError as e:
        rows.append(("FAIL", str(e)))
    rows.append(("OK" if sys.version_info >= (3, 11) else "FAIL", f"python {sys.version.split()[0]}"))
    wanted = pack.driver if "pack" in locals() else None
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
            tag = " (the pack's)" if kind == wanted else ""
            rows.append((level, f"driver {kind}{tag}: {missing or 'can run'}"))
    adb = shutil.which("adb")
    rows.append(("OK", f"adb {adb}") if adb else ("WARN", "adb not on PATH"))
    if adb:
        out = subprocess.run([adb, "devices"], capture_output=True, text=True, timeout=30).stdout
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
        }, indent=2))
        return 0
    for n in pack.sets:
        print(f"set {n}: {', '.join(j.name for j in pack.select(n))}")
    for j in pack.journeys.values():
        print(f"{j.name}  [{', '.join(j.tags)}]  {j.file.relative_to(pack.root)}:{j.line}")
    return 0


def _open(args, pack: Pack) -> tuple[Store, Driver]:
    kind = args.driver or pack.driver
    if kind != "fake" and not args.serial:
        raise DriverError("pass --serial <adb serial>: Journeys never picks a device by itself")
    store = _store(args, pack)
    return store, make_driver(kind, pack, args, store)


def cmd_tap_all(args) -> int:
    from .explore import tap_all
    pack = _load(args)
    store, driver = _open(args, pack)
    run = store.new_run(pack=pack.name, target="tap-all", driver=args.driver or pack.driver,
                        serial=args.serial, dka=args.dka)
    driver.start(pack.app, args.serial)
    try:
        rep = tap_all(pack, driver, store, run, serial=args.serial, dka=args.dka,
                      max_screens=args.max_screens, max_taps=args.max_taps)
    finally:
        driver.stop()
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
    return 1 if rep.crashes else 0


def cmd_run(args) -> int:
    pack = _load(args)
    journeys = pack.select(args.target, draft=args.draft)
    errors = pack.run_errors(args.target if args.draft else None)
    if errors:
        for e in errors:
            print(f"FAIL {e}", file=sys.stderr)
        return 2
    store, driver = _open(args, pack)
    driver.start(pack.app, args.serial)  # a refused start leaves no empty run behind
    run = store.new_run(args.run_id, pack=pack.name, target=args.target, driver=args.driver or pack.driver,
                        serial=args.serial)
    print(f"run {run.id}: {len(journeys)} journeys, results in {run.dir}")
    try:
        outs = Runner(pack, driver, store, run, serial=args.serial, heal=not args.no_heal).run_all(journeys)
    finally:
        driver.stop()
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


def cmd_serve(args) -> int:
    from .live import serve
    srv = serve(_store(args, _load(args)), args.port)
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
        code = subprocess.run(argv, cwd=pack.root, env=env).returncode
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
    try:
        lines = init_pack(dest, project, args.name or dest.name, args.app)
    except (FileExistsError, FileNotFoundError, ValueError) as e:
        raise PackError(str(e)) from None
    print("\n".join(lines))
    return 0


def cmd_validate(args) -> int:
    pack = _load(args)
    for e in pack.errors:
        print(f"FAIL {e}")
    print(f"{'OK' if not pack.errors else 'FAIL'} pack {pack.name}: {len(pack.journeys)} journeys, "
          f"{len(pack.drafts)} drafts, {len(pack.map.screens)} screens, {len(pack.map.edges)} edges, "
          f"{len(pack.errors)} errors")
    return 1 if pack.errors else 0


def cmd_new(args) -> int:
    from .authoring import new_draft
    print(f"draft written: {new_draft(_load(args), args.name, args.start)}")
    return 0


def cmd_promote_journey(args) -> int:
    from .authoring import promote_journey
    print(f"promoted: {promote_journey(_load(args), args.name)}")
    return 0


def cmd_export(args) -> int:
    from .formats.maestro import export
    pack = _load(args)
    j = pack.journeys.get(args.journey) or pack.drafts.get(args.journey)
    if not j:
        raise PackError(f"no journey or draft named {args.journey}")
    flow, notes = export(pack, j)
    for n in notes:
        print(f"NOTE {n}", file=sys.stderr)
    if args.out:
        Path(args.out).write_text(flow)
        print(f"written: {args.out}", file=sys.stderr)
    else:
        print(flow, end="")
    return 0


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
    s = sub.add_parser("new", help="write a new draft journey into drafts/")
    s.add_argument("name")
    s.add_argument("--start", help="the screen it starts on (default: the map's first)")
    s.set_defaults(fn=cmd_new)
    s = sub.add_parser("promote-journey", help="move a valid draft into journeys/")
    s.add_argument("name")
    s.set_defaults(fn=cmd_promote_journey)
    s = sub.add_parser("export", help="write a journey as another tool's test (maestro)")
    s.add_argument("format", choices=["maestro"])
    s.add_argument("journey")
    s.add_argument("-o", "--out", help="file to write (default: print)")
    s.set_defaults(fn=cmd_export)
    s = sub.add_parser("import", help="turn another tool's tests into draft journeys (maestro)")
    s.add_argument("format", choices=["maestro"])
    s.add_argument("paths", nargs="+", help="flow files or folders")
    s.set_defaults(fn=cmd_import)
    s = sub.add_parser("init", help="start a new pack; the driver follows the project's test framework")
    s.add_argument("--project", help="the app's project folder (its test framework picks the driver)")
    s.add_argument("--app", help="the app's package (default: applicationId from the project)")
    s.add_argument("--name", help="pack name (default: the folder name)")
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
    s.add_argument("--no-heal", action="store_true", help="no detours and no explore taps: read-only checks")
    s.add_argument("--draft", action="store_true", help="the target is a draft journey (drafts never run otherwise)")
    s.add_argument("--run-id", help=argparse.SUPPRESS)  # set by the MCP server for detached runs
    s.set_defaults(fn=cmd_run)
    s = sub.add_parser("tap-all", help="walk the app and tap every option once")
    s.add_argument("--driver", choices=DRIVERS)
    s.add_argument("--serial", help="adb serial of the device")
    s.add_argument("--fake-app", help="fake app file (default <pack>/fake/app.yaml)")
    s.add_argument("--dka", action="store_true", help="turn on don't-keep-activities for the walk")
    s.add_argument("--max-screens", type=int, default=20)
    s.add_argument("--max-taps", type=int, default=15)
    s.set_defaults(fn=cmd_tap_all)
    s = sub.add_parser("failures", help="list failures, or show one")
    s.add_argument("id", nargs="?")
    s.add_argument("--last", type=int, default=20)
    s.set_defaults(fn=cmd_failures)
    s = sub.add_parser("history", help="recent results per journey, with flaky ones marked")
    s.add_argument("--last", type=int, default=10)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_history)
    s = sub.add_parser("serve", help="local live page on 127.0.0.1")
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


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return args.fn(args)
    except (PackError, DriverError, RuntimeError, PromoteError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

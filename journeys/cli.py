"""journeys <command>: the command line."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from .drivers.base import Driver, DriverError
from .pack import Pack, PackError
from .results import Store, default_state_dir
from .runner import Runner


def make_driver(kind: str, pack: Pack, args) -> Driver:
    if kind == "fake":
        app = Path(args.fake_app) if args.fake_app else pack.root / "fake" / "app.yaml"
        if not app.exists():
            raise DriverError(f"{app} not found: the fake driver needs a fake app file")
        from .drivers.fake import FakeDriver
        return FakeDriver.from_file(app)
    raise DriverError(f"driver {kind} is not available yet")


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
    rows.append(("OK" if shutil.which("adb") else "WARN", "adb on PATH" if shutil.which("adb") else "adb not on PATH"))
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


def cmd_run(args) -> int:
    pack = _load(args)
    if pack.errors:
        for e in pack.errors:
            print(f"FAIL {e}", file=sys.stderr)
        return 2
    journeys = pack.select(args.target)
    driver = make_driver(args.driver or pack.driver, pack, args)
    store = _store(args, pack)
    run = store.new_run(pack=pack.name, target=args.target, driver=args.driver or pack.driver, serial=args.serial)
    print(f"run {run.id}: {len(journeys)} journeys, results in {run.dir}")
    driver.start(pack.app, args.serial)
    try:
        outs = Runner(pack, driver, store, run, serial=args.serial).run_all(journeys)
    finally:
        driver.stop()
    for o in outs:
        line = f"{o.status:7} {o.name}"
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


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="journeys", description="End-to-end journeys for Android apps.")
    p.add_argument("--pack", default=os.environ.get("JOURNEYS_PACK"), help="pack folder (or JOURNEYS_PACK)")
    p.add_argument("--state", help="state folder (default ~/.local/state/journeys/<pack>, or JOURNEYS_STATE)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor", help="check the pack and the tools").set_defaults(fn=cmd_doctor)
    s = sub.add_parser("list", help="list sets and journeys")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_list)
    s = sub.add_parser("run", help="run a journey, a set, or all")
    s.add_argument("target")
    s.add_argument("--driver", choices=["speedrun", "maestro", "fake"])
    s.add_argument("--serial", help="adb serial of the device")
    s.add_argument("--fake-app", help="fake app file (default <pack>/fake/app.yaml)")
    s.set_defaults(fn=cmd_run)
    s = sub.add_parser("failures", help="list failures, or show one")
    s.add_argument("id", nargs="?")
    s.add_argument("--last", type=int, default=20)
    s.set_defaults(fn=cmd_failures)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return args.fn(args)
    except (PackError, DriverError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

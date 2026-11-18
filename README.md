# Journeys

End-to-end journeys for Android apps, in a format that names no test framework. Each team keeps
its app map and journeys in a **pack** (a folder that can live in a private repo) and runs them
with the tool it already uses:

| Driver | Needs | |
|---|---|---|
| `maestro` | `maestro` on PATH | Each step runs as a Maestro flow. |
| `speedrun` | `journeys setup` | Upstream [Speedrun](https://github.com/hubastard/speedrun); fast; changes some device state (docs/packs.md). |
| `fake` | nothing | A small model of the app, for tests without a device. |

Journeys has no device driver of its own: every feature (journeys, detours, explore, tap-all,
look, results) runs through the driver the pack names, so a new driver brings all of them. The
pack's `driver:` is required; `--driver` overrides it; `journeys init --project <app>` picks the
one the project's tests already use. Maestro flows import into drafts, and journeys export back
to Maestro flows, so a team keeps its tools.

- **App map**: screens known by what is on them (texts, ids), and edges (which tap leads where).
- **Journeys**: named paths through the map with checks (`expect` a screen, `see` a text, an
  `event` fired), grouped in sets.
- **Self-healing**: when the app shows another known screen, the run takes a detour through the
  map and saves a *proposal*. When it fails, it explores the screen's options and proposes a fix.
  A person (or an agent) promotes or rejects each proposal; nothing changes the pack on its own.
- **tap-all**: walks the app, taps every option once, and reports crashes, dead ends and new
  screens. `--dka` repeats it with "don't keep activities" on.
- **Results**: run history, failures with screenshot and screen listing, flaky marks, a local
  live page.
- **Agents**: an MCP server. **Droid Lite**: a Tests-tab plugin.

## Start

```sh
uv sync                                    # Python 3.11+, PyYAML
export JOURNEYS_PACK=examples/settings-pack
uv run journeys doctor
uv run journeys run smoke --driver fake    # no phone: a small model of the app
uv run journeys run smoke --serial emulator-5554       # the pack's driver (maestro)
uv run journeys setup                                  # only for --driver speedrun
```

To add a journey, an agent (or a person) follows [skills/journeys/SKILL.md](skills/journeys/SKILL.md)
and its [format reference](skills/journeys/references/format.md): `new` a draft, `validate`,
`run --draft`, `promote-journey`.

Journeys never picks a device: every real run names one with `--serial`.

## Commands

| Command | |
|---|---|
| `doctor` | Check the pack, the drivers, adb and devices. |
| `validate` | Check the pack, every journey and draft (file:line). |
| `look --serial S` | The screen on the device now and its map screen. |
| `new NAME --start SCREEN` / `promote-journey NAME` | Start a draft / move it into `journeys/`. |
| `import maestro FLOWS` / `export maestro NAME` | Maestro flows to drafts / a journey to a flow. |
| `init --project APP` | A new pack whose driver follows the project's tests. |
| `list [--json]` | Sets and journeys. |
| `run <journey\|set\|all> --serial S` | Run them. `--driver`, `--draft`, `--no-heal`. |
| `tap-all --serial S [--dka]` | Walk the app; `--max-screens`, `--max-taps`. |
| `failures [F-n]` | Recent failures, or one in full. |
| `proposals [P-n] [--all]` | Open proposals, or one in full. |
| `promote P-n [--name X]` / `reject P-n --why ...` | Decide a proposal. |
| `history [--last N]` | Recent results per journey; flaky ones marked. |
| `serve [--port 8765]` / `watch` | Live page on 127.0.0.1 / live state as JSON lines. |
| `build` / `install --serial S` | The pack's build and install steps. |
| `mcp` | MCP tools on stdin and stdout. |
| `setup [--version vX]` | Install upstream Speedrun. |

State (runs, failures, proposals) goes to `~/.local/state/journeys/<pack>/`, or `--state` /
`JOURNEYS_STATE`. The Speedrun driver runs Speedrun with a private `HOME` under that folder.

## More

- [Pack format](docs/packs.md)
- [Agents (MCP) and Droid Lite](docs/agents.md)
- [Plan and design](docs/PLAN.md)

## License

MIT. Speedrun is Apache-2.0 and is not part of this repo: `journeys setup` downloads its release.

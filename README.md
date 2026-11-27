# Journeys

End-to-end journeys for Android apps, in a format that names no test framework. Each team keeps
its app map and journeys in a **pack** (a folder that can live in a private repo) and runs them
with the tool it already uses:

| Driver | Needs | |
|---|---|---|
| `maestro` | `maestro` on PATH | Each step runs as a Maestro flow. |
| `speedrun` | `journeys setup` | Upstream [Speedrun](https://github.com/hubastard/speedrun); fast; changes some device state (docs/packs.md). |
| `appium` | the team's Appium server (UiAutomator2) | Journeys talks W3C WebDriver to it (`JOURNEYS_APPIUM_URL`, default `http://127.0.0.1:4723`; add the server's base path, e.g. `/wd/hub`, if it has one); starts only on a device the pack allows to change: a session installs Appium's helper apps, sets the hidden API policy (Android 9+), on a phone allows mock location for its settings app (left on after the session) and unlocks the device. |
| `fake` | nothing | A small model of the app, for tests without a device. |

Journeys has no device driver of its own: every feature (journeys, detours, explore, tap-all,
look, results) runs through the driver the pack names, so a new driver brings all of them. The
pack's `driver:` is the team's answer; `--driver` overrides it. Journeys never assumes a driver:
`init` and every device command (`run`, `tap-all`, `look`) ask the user when neither names one, and
fail with the question when there is no terminal to ask in. The project's tests are shown as a hint
only, never taken as the answer. The driver tests run each driver against a simulated phone:
they check how Journeys calls the tool, not that your installed version accepts every key; check
those on your device. Maestro has no `Search` key: `press: search` is refused with the maestro driver
and in a Maestro export. Maestro matches selectors without case by default; Journeys' maestro driver
and export add `(?-ims)` in front of each text and id, so they match exactly as with the other drivers.
Maestro flows import into drafts, and a journey exports as a Maestro flow or a WebdriverIO spec
for Appium, so a team keeps its tools (docs/packs.md, Export).

- **App map**: screens known by what is on them (texts, ids), and edges (which tap leads where).
- **Journeys**: named paths through the map with checks (`expect` a screen, `see` a text, an
  `event` fired), grouped in sets.
- **Server setup**: pack hooks set a flag or make a test user before a journey; a journey's
  `after:` and a set's `before:`/`after:` clean up, passed or failed. The agent skill asks the team
  what the server needs and writes the scripts with them (docs/packs.md, pack.yaml).
- **Self-healing**: when the app shows another known screen, the run takes a detour through the
  map and saves a *proposal*. When it fails, it explores the screen's options and proposes a fix.
  A person (or an agent) promotes or rejects each proposal; nothing changes the pack on its own.
- **tap-all**: walks the app, taps every option once, and reports crashes, dead ends and new
  screens. `--dka` walks with "don't keep activities" on.
- **Results**: run history, failures with screenshot and screen listing, flaky marks.
- **Tracker**: a local page (`journeys serve`) with four parts: runs (live step, trend, pass and
  fail per run), the work queue (open failures, open proposals, drafts), agent activity (who did
  what: drafts, promotes, decisions, runs, agent tool calls) and agent usage (journeys built and
  issues solved per person or agent, per day, with the time a fix took).
- **Agents**: an MCP server. **Droid Lite**: a Tests-tab plugin.

## Start

```sh
uv sync                                    # Python 3.11+, PyYAML
export JOURNEYS_PACK=examples/settings-pack
uv run journeys doctor
uv run journeys run smoke --driver fake    # no phone: a small model of the app
uv run journeys run smoke --driver maestro --serial emulator-5554   # your team's tool
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
| `look --serial S` | The screen on the device now and its map screen. Reads only, but the driver's own session start still applies (appium: only where changes are allowed). |
| `new NAME --start SCREEN` / `promote-journey NAME` | Start a draft / move it into `journeys/`. |
| `import maestro FLOWS` / `export maestro\|appium NAME [-o FILE]` | Maestro flows to drafts / a journey to a Maestro flow or a WebdriverIO spec. |
| `init --project APP` | A new pack; asks which driver the team uses (or `--driver`). |
| `list [--json]` | Sets and journeys. |
| `run <journey\|set\|all> --serial S` | Run them. `--driver`, `--draft`, `--no-heal`. |
| `tap-all --serial S [--dka]` | Walk the app; `--max-screens`, `--max-taps`. |
| `failures [F-n]` | Recent failures, or one in full. |
| `proposals [P-n] [--all]` | Open proposals, or one in full. |
| `promote P-n [--name X]` / `reject P-n --why ...` | Decide a proposal. |
| `history [--last N]` | Recent results per journey; flaky ones marked. |
| `serve [--port 8765]` / `watch` | Tracker page on 127.0.0.1 / live state as JSON lines. |
| `tracker [--json]` | The tracker's four parts as text or JSON. |
| `sync [--dry-run] [--prune] [--accept FILE] [--decline FILE]` / `check-sync` | Keep the journeys and the team's flow files as one (pack.yaml `sync:`) / list the drift. |
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

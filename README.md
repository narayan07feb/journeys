# Journeys

End-to-end journeys for Android apps. Journeys drives a device with
[Speedrun](https://github.com/hubastard/speedrun) (Maestro as a fallback) and keeps each team's app
map and journeys in a **pack**: a folder that can live in a private repo.

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
uv run journeys setup                      # upstream Speedrun into third_party/ (SHA-256 checked)
export JOURNEYS_PACK=examples/settings-pack
uv run journeys doctor
uv run journeys run smoke --driver fake    # no phone: a small model of the app
uv run journeys run smoke --serial emulator-5554
```

Journeys never picks a device: every real run names one with `--serial`.

## Commands

| Command | |
|---|---|
| `doctor` | Check the pack, Speedrun, adb and devices. |
| `list [--json]` | Sets and journeys. |
| `run <journey\|set\|all> --serial S` | Run them. `--driver fake\|speedrun\|maestro`. |
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
`JOURNEYS_STATE`. Speedrun runs with its own private `HOME` under that folder.

## More

- [Pack format](docs/packs.md)
- [Agents (MCP) and Droid Lite](docs/agents.md)
- [Plan and design](docs/PLAN.md)

## License

MIT. Speedrun is Apache-2.0 and is not part of this repo: `journeys setup` downloads its release.

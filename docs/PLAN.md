# Journeys: plan

Journeys is a generic, open-source end-to-end test tool for Android apps. Journeys are written in a
neutral format and run with the team's own tool through a driver: Maestro or upstream
[Speedrun](https://github.com/hubastard/speedrun) (public). Journeys has no device driver of its
own (owner, 18 Nov 2026). Section 3.1 and the sections after it
describe the first design (Speedrun first); M9 to M13 in section 5 replaced it.
A company keeps its own app map and journeys in a private **pack**; the public repo has the core
and one example pack for a public demo app.

Status: M0 to M8 built 17 Nov 2026; the Settings smoke and a game tap-all passed on a real phone (speedrun driver). M9 to M13 (driver-generic, neutral format, detection, Maestro import/export, drafts and the skill) built 18 Nov 2026. Name: Journeys (owner OK), MIT.

## 1. Clean-room rule

Everything here is written from scratch from the public Speedrun docs, the public Droid Lite plugin
spec and general testing ideas. No code, names, flows or data from any employer tool. Speedrun comes
only from the upstream release (installed into `third_party/`, never `~/.speedrun`).

## 2. Language: Python 3.11+

Why Python:
- A CLI, a stdio MCP server, a small local web page and subprocess control of `speedrun`, `maestro`
  and `adb` are all short in the standard library. The only runtime dependency is PyYAML.
- No build step. The Droid Lite plugin runs it directly (`python3 -m journeys ...`).
- Pack authors (QA, Android devs) can read and extend it; pack hooks may be any script.
- Tests run with `pytest` and a fake driver, with no phone, in a second.
- Cheap on the Mac: no JVM, no Gradle, no node_modules.

Go (one static binary) was the other choice. It is better for distribution but slower to change
while the design is still moving. Revisit after the MVP if a single binary matters.

The MCP server is a small hand-written JSON-RPC 2.0 loop over stdio (`initialize`, `tools/list`,
`tools/call`), so it needs no SDK and is easy to test.

## 3. Architecture

```
journeys/                 python package
  cli.py                  `journeys <command>` entry point
  pack.py                 load + validate a pack folder
  appmap.py               screens, edges, screen identification, path finding
  journey.py              journey / set model
  runner.py               runs a journey step by step, checks, detours
  heal.py                 detour proposals, explore-on-failure, promote / reject
  explore.py              tap-all walker, don't-keep-activities mode
  results.py              run store, failures, flaky stats
  live.py                 local live page (http.server, 127.0.0.1 only)
  mcp_server.py           MCP stdio server
  drivers/
    base.py               Driver interface + Screen / Element types
    speedrun.py           upstream Speedrun session CLI
    maestro.py            Maestro driver (maestro test per step, maestro hierarchy)
    fake.py               scripted in-memory app for tests
droid-lite-plugin/plugin.json
examples/settings-pack/   example pack for Android Settings (com.android.settings)
tests/
third_party/speedrun/     upstream release binary (git-ignored, fetched by `journeys setup`)
```

### 3.1 Driver interface

One device at a time, chosen by adb serial.

```python
class Driver:
    def start(self, app_id: str, serial: str | None) -> None
    def look(self) -> Screen            # elements: ref, kind, text, id, state
    def do(self, commands: list) -> StepResult   # Maestro/Speedrun flow commands
    def screenshot(self, path: Path) -> Path | None
    def app_alive(self) -> bool         # for crash detection
    def stop(self) -> None
```

- **SpeedrunDriver** wraps `speedrun session start --app <id> --device <serial>`, `speedrun look`
  (parses the one-line-per-element listing), `speedrun do` and `speedrun session stop`. It runs
  with `HOME` set to a private folder (`<state>/speedrun-home`) so Speedrun's own state never goes
  to `~/.speedrun`.
- **MaestroDriver**: each `do` writes a temp flow and runs `maestro --device <serial>
  test`; `look` parses `maestro hierarchy`. Used when the pack says `driver: maestro` (no fallback).
- **FakeDriver**: an app described as screens and tap targets in YAML/dicts. It can inject an
  unexpected screen, a crash, or a missing element. All unit and integration tests use it.

### 3.2 App map

A screen is identified from what is on it, never from coordinates:

```yaml
# map/screens.yaml
screens:
  SETTINGS-HOME:
    title: Settings home
    match:
      all: [{text: "Settings"}]
      any: [{id: "search_action_bar"}, {text: "Network & internet"}]
      none: [{text: "Search settings"}]
  SETTINGS-DISPLAY:
    match:
      all: [{text: "Display"}, {text: "Brightness level"}]
```

- `all`, `any`, `none` take `text` (exact or `/regex/`), `id` and `kind`.
- If more than one screen matches, the one with the most matched rules wins; a tie is reported as
  ambiguous (a map error the doctor shows).

Edges say which action leads where:

```yaml
# map/edges.yaml
edges:
  - from: SETTINGS-HOME
    to: SETTINGS-DISPLAY
    do: [{scroll_to: "Display"}, {tap: "Display"}]
  - from: SETTINGS-DISPLAY
    to: SETTINGS-HOME
    do: [back]
```

Path finding is a breadth-first search over edges (shortest number of edges). A `goto` step uses it.

### 3.3 Journeys

```yaml
# journeys/display.yaml
name: open-display
tags: [smoke]
start: {launch: true, clear_state: false}
steps:
  - expect: SETTINGS-HOME
  - goto: SETTINGS-DISPLAY            # follows map edges
  - see: "Brightness level"           # text shown
  - do: [{tap: "Dark theme"}]         # neutral actions (docs/packs.md)
  - hook: set_flag {flag: dark, on: true}   # pack script
  - event: {name: theme_changed}      # pack event checker
  - expect: SETTINGS-DISPLAY
```

- Step kinds: `expect` (screen reached), `goto`, `see`, `not_see`, `do`, `input` (with `${VAR}`
  from env), `hook`, `event`.
- `event` calls the pack's event checker command (for example one that reads logcat or an
  analytics debug endpoint) and passes when it prints `{"fired": true}`.
- Sets: `sets.yaml` maps a set name to journeys or tags. `journeys run all` runs every journey.

### 3.4 Self-healing

1. **Detour.** An `expect` or `goto` lands on screen B, but A was wanted. If B is a known screen
   and the map has a path B→A, the runner follows it, the journey goes on, and the run records a
   proposal `P-<n>`: "between step k and k+1 the app now shows B; add the detour". The journey is
   reported as *passed with a change*, not as failed.
2. **Explore on failure.** If there is no path (B is unknown, or an element is missing), the runner
   records failure `F-<n>` with the screenshot, the screen listing and the step. Then it explores:
   for each tappable element on B it taps, looks where it went (known screen, new screen, crash,
   no change), and goes back (restarting the journey up to that step when `back` does not return).
   The options and their outcomes go into the failure, and the best one (it reaches the expected
   screen) becomes a proposal.
3. **Review.** `journeys proposals`, `journeys promote P-3`, `journeys reject P-3 --why ...`.
   Promote writes the new edge or screen into the pack's map files (a normal diff to review in the
   pack's git). Nothing changes the pack without a promote.

### 3.5 Explore / tap-all

`journeys tap-all [--max-screens N] [--max-taps N] [--dka]`: breadth-first walk. On each screen,
tap every element once, record the result, go back. Report: crashes (app process gone, with the
last logcat lines), dead ends (no way back), new unknown screens (proposed names from their
heading). `--dka` turns on Android's "don't keep activities" for the run and turns it back off at
the end (emulator or a device the pack marks as allowed in `devices.allow_settings_changes`; refused elsewhere).

### 3.6 Results

- State folder: `--state` or `$JOURNEYS_STATE`, default `~/.local/state/journeys/<pack name>/`.
- Each run: `runs/<run id>/run.json` (journeys, steps, timings, status), screenshots, screen
  listings, driver log.
- `failures.json`, `proposals.json` with ids `F-<n>`, `P-<n>`.
- Flaky: per journey, the last results (`history --last N`, default 10); a journey that both passed and failed on the same
  commit of the app build is marked flaky.
- `journeys serve`: a local page on `127.0.0.1` that shows the current step and
  screen, refreshed from `live.json`.

### 3.7 Agent access (MCP)

`journeys mcp --pack <path>` tools: `doctor`, `list_journeys`, `start_run`, `run_status`,
`wait_run`, `failures`, `failure`, `proposals`, `promote`, `reject` (`look` is CLI only).
A run started from MCP runs as a detached child process, so the MCP call returns at once.

### 3.8 Droid Lite plugin

`droid-lite-plugin/plugin.json` with `"dir": ".."`: `list` prints sets and journeys as a case tree
(`file` = the journey YAML), `run {id}` prints JSON status lines, `watch` prints `live.json` changes,
views `Failures` and `Proposals`, action `Doctor`. The plugin never writes into its own folder (state
goes to the state folder).

### 3.9 Packs

```
my-pack/
  pack.yaml            name, app id, driver, device rules, build/install, env, hooks, events
  map/screens.yaml
  map/edges.yaml
  journeys/*.yaml
  sets.yaml
  hooks/               any executables the pack names
```

```yaml
# pack.yaml
name: settings-demo
app: com.android.settings
driver: speedrun            # or maestro
build:   []                 # argv lists, run by `journeys build`
install: []                 # e.g. [adb, install, -r, app.apk]
accounts:                   # values come only from env vars, never from the pack
  main: {user: DEMO_USER, password: DEMO_PASSWORD}
hooks:
  set_flag: [hooks/set_flag.sh]      # gets args as JSON on argv[1]
events:
  check: [hooks/check_event.sh]
devices:
  allow_settings_changes: emulator   # where --dka may change settings
```

The core loads a pack by path (`--pack` or `$JOURNEYS_PACK`). A private pack lives in its own
private repo.

## 4. MVP scope

In: pack loading and validation, map + identification + path finding, journeys and sets, the
runner with `expect/goto/see/not_see/do/input/hook/event`, detours with proposals, failures with
screenshot and listing, promote / reject, Speedrun driver, fake driver, CLI, results store, the
example pack, Doctor.

After MVP: Maestro fallback, explore-on-failure, tap-all and DKA, live page, flaky stats, MCP
server, Droid Lite plugin. iOS and web are out of scope.

## 5. Milestones

| # | Content | Done when |
|---|---|---|
| M0 | This plan, LICENSE, repo skeleton | committed |
| M1 | Pack loader + validation, app map, identification, path finding, journey model, fake driver, runner (no healing), results store, CLI `doctor/list/run`, example pack | `pytest` green; `journeys run` passes a journey on the fake driver |
| M2 | Speedrun driver (upstream release into `third_party/`, private HOME), `look` parser, `journeys setup` | parser tests on recorded `look` text; emulator check after the owner's yes |
| M3 | Detours + proposals, explore-on-failure, promote / reject | fake-driver tests for each heal case |
| M4 | Tap-all, crash and dead-end report, DKA | fake-driver tests; emulator check after yes |
| M5 | Live page, history, flaky tracking, Maestro fallback | tests |
| M6 | MCP server | JSON-RPC tests over a pipe |
| M7 | Droid Lite plugin | `list/run/watch` output checked against the spec |
| M8 | Docs, README, emulator end-to-end with the example pack | owner review |
| M9 | Driver-generic: one Driver contract, driver per pack (asked when missing, never guessed) + `--driver`, no silent fallback; no own device driver (a uiautomator driver was built, then removed by the owner) | one contract test suite passes on fake, maestro, speedrun (pretend phone) |
| M10 | Neutral format: packs use Journeys' own action words and selectors; framework words are errors | validate tests |
| M11 | Framework detection: `init --project` shows the project's tests as a hint; the driver is always the user's answer (owner, 19 Nov: never assume one; init and device commands ask); doctor shows it | tests on sample projects; a pack without `driver:` asks, never guesses |
| M12 | Maestro import (flows to drafts) and export (journey to flow) | round-trip tests |
| M13 | Drafts (`new`, `validate`, `run --draft`, `promote-journey`) and the skill (`skills/journeys/`) | a fresh agent writes a valid journey from the skill alone |
| M14 | Device check with the team's driver on a real phone | owner-approved device |
| M15 (built 20 Nov) | Two-way sync, one source of truth (`sync:` in pack.yaml: source journeys (default) or framework). Journeys -> generated team-framework files with a header and hash; a developer's new flow -> draft journey; `check-sync` reports hand edits, stale or missing files, unimported flows | fake tests per drift case, mutation-checked |
| M16 (built 22 Nov) | Appium driver (W3C WebDriver to the team's Appium server, host side): passes the same driver contract tests (pretend phone), tap-all and journeys; device check owed | contract tests + wire tests |
| M17 (built 26 Nov) | Appium export: `journeys export appium` writes a WebdriverIO spec (whole journey at once, one-way); it shares the journey walk with the Maestro export; all three drivers kept (owner, 23 Nov) | specs run under node against a pretend WebdriverIO; check on a real Appium owed |
| Parked | Espresso/Compose export as an androidTest: built only when a team asks for it (owner, 23 Nov) | per framework |
| Next | Backend setup through the agent skill (owner, 23 Nov): the skill asks what a journey needs on the server, writes and tests the pack's hook script after the team's yes; `after:` cleanup hooks and a set's `before:` in the runner; `init` asks about server setup | fake-driver tests per phase |

## 6. Tests

- Unit: pack validation errors, screen matching (all/any/none, regex, ties), BFS, `look` parser,
  journey parsing, proposal promote writes the right YAML.
- Integration on the fake driver: a journey passes; an unexpected screen gives a detour and a
  proposal; a missing element gives a failure with options; a crash in tap-all is reported.
- MCP and plugin: run the real entry points as subprocesses and check the JSON.
- Device: only on an emulator, only after the owner says yes. Never the owner's test phones.

## 7. Open points for the owner

1. Name: keep "Journeys"?
2. License: MIT for this repo (Speedrun itself is Apache-2.0; we only run its binary, we do not
   copy its code, so MIT is fine for us).
3. Emulator checks: yes / no, and when (the Mac is low on memory and disk today).

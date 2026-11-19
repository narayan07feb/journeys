---
name: journeys
description: Create, check, run and promote end-to-end journeys for an Android app with Journeys. Use when asked to add a journey, test a user flow, write an e2e test in a Journeys pack, import Maestro flows, or export a journey to a team's framework.
---

# Journeys: add a journey

Journeys tests an Android app by walking a map of its screens. A pack (a folder) holds the map and
the journeys. The format is neutral: no step names a test framework. The pack's `driver` (or
`--driver`) is the team's own tool that drives the device: `maestro` or `speedrun` (`fake` runs
without a device). Every step below works the same with any driver; nothing here needs Speedrun.
Never pick a driver for the user, not even from the project's tests: when the pack has no `driver:`,
ask the user which tool their team uses and pass the answer as `--driver` (or `driver` on
`start_run`); the commands ask the same question and stop rather than guess.

Read `references/format.md` before you write a step. It has the exact format and a worked example.

## Steps

1. Find the pack: `--pack <folder>` or `$JOURNEYS_PACK`. Run `journeys --pack P validate`. Fix or
   report any FAIL that is not yours before you start.
2. Read `map/screens.yaml` and `map/edges.yaml`. Find the screens your journey visits. A screen
   that is missing needs a rule first (format.md, "Screens"); a way between screens needs an edge.
   To see a screen's real texts and ids: with that screen open on the device (ask the user to
   open it, or let a draft run stop there), run `journeys --pack P look --serial <serial>`. It
   prints each element (kind, "text", #id) and which map screen it matches. It sends no input
   (the driver may set up its own helper on the device first, as it does for any run).
3. Start a draft: `journeys --pack P new <name> --start <SCREEN>`. It writes `drafts/<name>.yaml`.
4. Write the steps (format.md, "Journeys"): `expect` and `goto` for screens, `see`/`not_see` for
   checks, `do:` with action words for taps and typing, `input` for text from env vars.
5. Run `journeys --pack P validate` until it prints `OK`. Each FAIL names file:line and the fix.
6. Run the draft: `journeys --pack P run <name> --draft --serial <adb serial> --no-heal` (the pack's
   driver; `--no-heal`: no detours and no explore taps, so only the draft's own steps touch the app),
   or `--driver fake` when the pack has `fake/app.yaml`. A device run needs the user's device
   serial; never pick a device yourself. Results go to `--state <folder>` (default
   `~/.local/state/journeys/<pack name>/`).
7. If it fails: `journeys --pack P failures F-<n>` shows the screen, the step and (when taps are
   allowed) where each option led. Fix the draft or the map, then run again. With `--driver fake`
   the cause can also be the fake model: add the missing element to `fake/app.yaml` (format.md,
   "Fake app").
8. When it passes: `journeys --pack P promote-journey <name>`. It moves the draft to `journeys/`
   (it refuses errors and a journey with the same steps as another one).

## Rules

- Use only the neutral words in format.md. `tapOn`, `inputText`, `click`, `sendKeys` and other
  framework words are errors.
- Selectors: `"Exact text"`, `"/regex/"` (whole text), `{id: name}`, `{text: ..., index: 1}`.
- Secrets never go in the pack: declare the env var in `pack.yaml` (`env:` or `accounts:`) and use
  `${NAME}`.
- One journey per draft file. Names are lowercase words with dashes.
- A team that uses Maestro: `journeys import maestro <flows>` makes drafts from its flows;
  `journeys export maestro <journey> -o flow.yaml` writes a flow for its own `maestro test`.
- New pack for an app: `journeys --pack <new folder> init --project <app project> --driver <the
  user's answer>`; ask the user first (the project's test framework is a hint, never the answer).
- The tracker (`journeys tracker`, the `serve` page, the MCP `tracker` tool) counts the journeys you
  build and the issues you solve under your name: set `JOURNEYS_ACTOR=<your agent name>` when your
  shell does not set `AI_AGENT`. Read its work queue (open failures, proposals, drafts) to pick
  what to do next.

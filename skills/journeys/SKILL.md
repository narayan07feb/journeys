---
name: journeys
description: Create, check, run and promote end-to-end journeys for an Android app with Journeys. Use when asked to add a journey, test a user flow, write an e2e test in a Journeys pack, import Maestro flows, or export a journey to a team's framework.
---

# Journeys: add a journey

Journeys tests an Android app by walking a map of its screens. A pack (a folder) holds the map and
the journeys. The format is neutral: no step names a test framework. The pack's `driver` (or
`--driver`) picks the tool that drives the device: `uiautomator` (adb only), `maestro`, `speedrun`,
or `fake` (no device). Nothing here needs Speedrun.

Read `references/format.md` before you write a step. It has the exact format and a worked example.

## Steps

1. Find the pack: `--pack <folder>` or `$JOURNEYS_PACK`. Run `journeys --pack P validate`. Fix or
   report any FAIL that is not yours before you start.
2. Read `map/screens.yaml` and `map/edges.yaml`. Find the screens your journey visits. A screen
   that is missing needs a rule first (format.md, "Screens"); a way between screens needs an edge.
   To see a screen's real texts and ids: with that screen open on the device (ask the user to
   open it, or let a draft run stop there), run `journeys --pack P look --serial <serial>`. It
   prints each element (kind, "text", #id) and which map screen it matches. With the uiautomator
   driver it changes nothing (maestro and speedrun first set up their own driver on the device), and
   it lists only the pack's app: with another app in front the list is empty.
3. Start a draft: `journeys --pack P new <name> --start <SCREEN>`. It writes `drafts/<name>.yaml`.
4. Write the steps (format.md, "Journeys"): `expect` and `goto` for screens, `see`/`not_see` for
   checks, `do:` with action words for taps and typing, `input` for text from env vars.
5. Run `journeys --pack P validate` until it prints `OK`. Each FAIL names file:line and the fix.
6. Run the draft: `journeys --pack P run <name> --draft --serial <adb serial> --no-heal` (the pack's
   driver; `--no-heal` keeps a first device run read-only: no detours and no explore taps),
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
- New pack for an app: `journeys --pack <new folder> init --project <app project>`; the driver
  follows the project's test framework.

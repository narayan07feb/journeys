# Journeys: notes for the Gemini session

Journeys is a generic, open-source end-to-end testing tool for Android apps (Python 3.11+, PyYAML only).
A team adds its own journeys as a private pack on top.

## Where things are

- Roadmap and milestones: `docs/PLAN.md` (the "Next" row is the next item).
- Living handoff (not in git): `.claude/handoff/journeys.md`. Read it first, continue from **Next**.
- Agent and pack docs: `docs/agents.md`, `docs/packs.md`, `README.md`.

## Rules

1. **Clean room.** Never open, read, copy, grep or diff the employer folders listed in the local
   `BRIEF.md` (not in git). Never put employer names, apps, hosts, people, flows or data in this repo
   (code, docs, tests, examples, commit messages). Use public demo apps only.
2. **Never assume a driver.** Every feature works through any driver (maestro, speedrun, appium; fake for
   tests). Init and device commands ask the user, or stop with the question when there is no tty.
3. **Commits.** Author and committer `narayan07feb@gmail.com`. No Co-Authored-By or AI trailer.
   Before every push: clean-room grep = 0, gmail author and committer, no trailer.
4. **Review.** Each round is a fresh Codex reviewer:
   `/Users/narayansoni/Documents/ForgeByte/studio/tools/codex-review.sh <repo dir> <brief file> <report file>`.
   The brief names the repo, PARENT and HEAD SHAs (`git rev-parse` after the commit), the checklist and the
   report format (Critical / Major / Minor), and no earlier findings. Fix every finding, then a new round.
   A stage ends at the first round with 0 Critical and 0 Major; fix that round's Minors, then merge (ff) and push.
5. **Reports to ceo.** Write `/Users/narayansoni/Documents/ForgeByte/studio/ceo/inbox/.tmp-journeys-<HHMMSS>.md`,
   then `mv` it to `<HHMM>-journeys.md` in the same folder.
6. **Devices.** Take a device lock only with a guarded command that re-reads the lock and writes only when it
   starts with `free`. M14 (Speedrun) runs only on the Mi A3, under its lock. Media volume 0. No settings
   changes, no animation-scale changes, never `svc wifi`/`svc data` over Wi-Fi adb, never `logcat -c`/`-G`.
   Device waiters need `ipconfig getifaddr en0` to start with `192.168.0.`.
7. **Not this lane.** Droid Lite belongs to another session: never profile or fix it.

## Checks

- Tests: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider`
- Python 3.11: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD uvx --python 3.11 --with pyyaml pytest -q -p no:cacheprovider`
- Lint: `uvx pyflakes journeys tests`
- Mutation checks: `.claude/handoff/mut.py` (edit its M list, run from the repo root).

## Next item

The Appium export (`journeys export appium`, a WebdriverIO spec) on branch `feat/appium-export`.
See the handoff for its state and what is left.

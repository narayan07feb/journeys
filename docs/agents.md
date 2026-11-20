# Agents and Droid Lite

## MCP

```sh
claude mcp add journeys -- /path/to/journeys/bin/journeys --pack /path/to/pack mcp
```

Tools: `doctor`, `list_journeys`, `start_run` (target, serial, driver), `run_status`, `wait_run`,
`failures`, `failure`, `proposals`, `promote`, `reject`, `tracker`, `check_sync`.

`start_run` starts the run as a separate process and returns its id at once. `wait_run` returns
when the run ends, or after `timeout_s` with "still running: call wait_run again". A real driver
needs `serial`.

A heal, for an agent: read `failure(id)`. Its `options` list each tap on the failed screen and
where it led; `proposal` names the fix it suggests. Promote it when it is the step the journey
means; else reject it with the reason and edit the pack by hand.

## Tracker: what agents did

Every draft, import, promote-journey, proposal decision and MCP tool call is logged in the state
folder (`activity.jsonl`, secrets masked) with who did it; runs come from their own `run.json`. `journeys serve` shows it as a page,
`journeys tracker` as text or JSON, and the MCP `tracker` tool as JSON.

Who did it: `JOURNEYS_ACTOR` when set (a person's or an agent's name); the MCP client's own name
(from its `initialize`), which runs it starts also carry; `droid-lite` for Droid Lite runs; an agent
whose shell sets `AI_AGENT` or `CLAUDECODE`; else `person`.

Agent usage counts drafts (written or imported), journeys built (drafts promoted) and issues
solved, each issue once: a journey that passed again after failing (one pass is one issue, however
many failure records the nightly runs left; only a run that started after the failure counts; a
tap-all crash is fixed by a later clean tap-all that walked every screen with the same `--dka`),
plus failures healed by a promoted proposal and not already counted that way. A promoted detour
proposal (no failure) counts as a change, not an issue, and is never credited with a fix. A flaky
journey that passes once between failures counts as fixed by that pass; its next failure opens a new
issue. Decided counts promotes and rejects; a reject solves
nothing, and its failure stays in the work queue for a heal by hand. Who fixed a failure is a best
guess: whoever last promoted its proposal or that journey before the pass, else whoever ran the
passing run; the time from failure to that pass is the fix time. Journeys does not see tokens or
cost. The queue lists the newest 50 open failures and counts all of them; the per-day table shows
the last 14 days. Every call to a Journeys MCP tool counts as a tool call, `wait_run` polls included. The page
answers only requests addressed to 127.0.0.1 or localhost.

## Droid Lite

Add the plugin by picking this repo's folder in the Tests tab (**Add plugin…**); Droid Lite reads
`droid-lite-plugin/plugin.json`. Settings come from the environment Droid Lite starts with:

| Variable | |
|---|---|
| `JOURNEYS_PACK` | The pack. Default: `<project>/journeys-pack` if there is one, else the example pack. |
| `JOURNEYS_SERIAL` | The device. Without it only `JOURNEYS_DRIVER=fake` runs. |
| `JOURNEYS_DRIVER` | `maestro`, `speedrun` or `fake`: the user's answer, for Droid Lite, the CLI and MCP (`--driver` or MCP `driver` wins). Default: the pack's `driver`; when the pack has none, ask the user, never pick one. |
| `JOURNEYS_STATE` | The state folder. |

The tree shows every journey (each opens its YAML) and every set. Views: Failures, Proposals.
Action: Doctor. The panel shows the live step and screen. The plugin writes nothing in its own
folder, so the OK Droid Lite asks for stays valid.

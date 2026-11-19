# Agents and Droid Lite

## MCP

```sh
claude mcp add journeys -- /path/to/journeys/bin/journeys --pack /path/to/pack mcp
```

Tools: `doctor`, `list_journeys`, `start_run` (target, serial, driver), `run_status`, `wait_run`,
`failures`, `failure`, `proposals`, `promote`, `reject`.

`start_run` starts the run as a separate process and returns its id at once. `wait_run` returns
when the run ends, or after `timeout_s` with "still running: call wait_run again". A real driver
needs `serial`.

A heal, for an agent: read `failure(id)`. Its `options` list each tap on the failed screen and
where it led; `proposal` names the fix it suggests. Promote it when it is the step the journey
means; else reject it with the reason and edit the pack by hand.

## Droid Lite

Add the plugin by picking this repo's folder in the Tests tab (**Add plugin…**); Droid Lite reads
`droid-lite-plugin/plugin.json`. Settings come from the environment Droid Lite starts with:

| Variable | |
|---|---|
| `JOURNEYS_PACK` | The pack. Default: `<project>/journeys-pack` if there is one, else the example pack. |
| `JOURNEYS_SERIAL` | The device. Without it only `JOURNEYS_DRIVER=fake` runs. |
| `JOURNEYS_DRIVER` | `maestro`, `speedrun` or `fake`: the user's answer, for Droid Lite and the CLI (`--driver` wins). Default: the pack's `driver`; when the pack has none, ask the user, never pick one. |
| `JOURNEYS_STATE` | The state folder. |

The tree shows every journey (each opens its YAML) and every set. Views: Failures, Proposals.
Action: Doctor. The panel shows the live step and screen. The plugin writes nothing in its own
folder, so the OK Droid Lite asks for stays valid.

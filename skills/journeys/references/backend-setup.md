# Server setup for a journey (backend hooks)

Some journeys need the team's server in a known state first: a feature flag on, a test user, an
order to refund. Journeys does this with **hooks**: scripts in the pack that the runner calls. The
team never writes hook scripts by hand. You ask, read, write and test them with the team.

Journeys is generic. Each team has its own backend tool (a flag service, an admin API, a seed
script). Never guess the tool and never name one in a public example.

## How the runner calls a hook

- `pack.yaml` names it: `hooks: {set_flag: [hooks/set_flag.sh]}` (argv; a script in the pack or any
  command such as `[python3, hooks/new_user.py]`).
- The working folder is the pack. The step's args come as **one JSON text, last on the command
  line** (`$1` in a shell script, `sys.argv[-1]` in Python). The env has `JOURNEYS_APP`,
  `JOURNEYS_SERIAL`, `JOURNEYS_PACK`, and the shell's own env vars.
- Exit 0 means done. Any other exit fails the step, and the end of stderr (or stdout) is the
  message. It times out after 300 s.
- Three places call hooks:
  1. A step: `- hook: {name: set_flag, args: {flag: new_checkout, enabled: true}}`.
  2. A journey's `after:`: hooks that run when the journey ends, **passed or failed** (also after
     Ctrl-C or a lost device). Every one runs, even when one fails. A failed one fails a journey
     that had passed.
  3. A set's `before:` (once, before its journeys) and `after:` (once, at the end, always). They
     run only when that set is the run target (`run nightly`), not for `run all` or one journey.
     A failed `before:` runs no journey, but the set's `after:` still runs.

## Steps

1. **Ask what the journey needs on the server.** One question at a time: which flag, user, or
   data, and in which state? Write down the answer.
2. **Find how the team sets it.** Ask which tool or API does it. If the user points to the team's
   own repo (their backend, their scripts), read it there to find the call. Never invent an
   endpoint. If nobody knows, stop and say so.
3. **Find where the key lives.** Ask which env var holds the token or key (for example
   `FLAGS_TOKEN`). Add that name to `pack.yaml` `env:`. This is required, not optional: Journeys
   hides only declared values in its logs and results, and a hook's stderr goes into the results.
   Never write a key, token or password into the pack.
4. **Write the setup script** into `hooks/<name>.sh` (or `.py`) and add it to `pack.yaml hooks:`.
   Make it executable (`chmod +x hooks/<name>.sh`), or name the interpreter first in the argv
   (`[python3, hooks/<name>.py]`). Read its input from the JSON argument. Print a short reason to
   stderr and exit non-zero on any error. Never print the token.
5. **Write the cleanup script too** (for example `reset_flag`). It must be safe to run when setup
   never ran or only half ran (turning off a flag that is already off is fine; deleting a user
   that does not exist is fine).
6. **Wire them in.** Add the `hook:` step where the journey needs it, and the cleanup to the
   journey's `after:` (or the set's `before:` / `after:` when many journeys share the state).
   Run `journeys --pack P validate` until it prints `OK`.
7. **Test once, only after the team says yes.** It changes a real server. Ask first, and say which
   server and what will change. Run it the way the runner does: from the pack folder, with the JSON
   argument last:
   `cd P && JOURNEYS_PACK=$PWD JOURNEYS_APP=<app> hooks/set_flag.sh '{"flag": "new_checkout", "on": true}'`
   Then run the cleanup the same way. Report both exit codes and outputs (with secrets hidden).
8. **Hand it over for review.** The team reads the scripts before you promote the journey. Say
   which files you added and what each one changes on the server.

## Example (generic)

```yaml
# pack.yaml
env: [FLAGS_URL, FLAGS_TOKEN]
hooks:
  set_flag: [hooks/set_flag.sh]
  reset_flag: [hooks/reset_flag.sh]
```

```yaml
# journeys/new-checkout.yaml
name: new-checkout
after: [{name: reset_flag, args: {flag: new_checkout}}]
steps:
  - hook: {name: set_flag, args: {flag: new_checkout, enabled: true}}
  - expect: HOME
  - goto: CHECKOUT
  - see: "Pay now"
```

```yaml
# sets.yaml: one setup for the whole set
sets:
  nightly:
    tags: [nightly]
    before: [{name: set_flag, args: {flag: new_checkout, enabled: true}}]
    after: [{name: reset_flag, args: {flag: new_checkout}}]
```

## A new pack

`journeys --pack P init ... --server-hook set_flag --server-hook reset_flag` (or answer the
question `init` asks on a terminal) adds the `hooks:` entries and a stub script for each. A stub
fails until it is written, so a run never passes on a hook that does nothing.

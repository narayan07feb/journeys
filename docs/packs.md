# Pack format

A pack is a folder. The core loads it by path (`--pack` or `JOURNEYS_PACK`). Keep a company's pack
in its own private repo; this repo has only `examples/settings-pack` (Android's Settings app).

```
my-pack/
  pack.yaml
  map/screens.yaml
  map/edges.yaml
  journeys/*.yaml
  drafts/*.yaml   new journeys: run only with `run NAME --draft`, never in sets or `all`
  sets.yaml
  hooks/          scripts the pack names
  fake/app.yaml   optional: a model of the app for `--driver fake`
```

The exact format, every step and action, and a worked example are in
[skills/journeys/references/format.md](../skills/journeys/references/format.md).

## pack.yaml

```yaml
name: my-app                 # also the state folder's name
app: com.example.app
driver: maestro              # the team's answer: maestro, speedrun or appium (fake for tests); never guessed,
                             # a pack without it makes every device command ask the user
env: [SEARCH_TEXT]           # variables a journey may use as ${SEARCH_TEXT}
accounts:                    # test accounts: values only from env vars, never in the pack
  main: {user: APP_USER, password: APP_PASSWORD}     # ${APP_USER} or ${main.user}
hooks:
  set_flag: [hooks/set_flag.sh]     # argv; the step's args come as one JSON text at the end
  new_user: [python3, hooks/new_user.py]
events:
  check: [hooks/check_event.sh]     # prints {"fired": true} when the event was seen
build:   [[./gradlew, assembleDebug]]
install: [[adb, -s, "{serial}", install, -r, app/build/outputs/apk/debug/app-debug.apk]]
devices:
  allow_settings_changes: emulator  # devices Journeys may change: emulator, any, or a list of serials.
                                    # --dka sets "don't keep activities" over adb (maestro, speedrun, appium).
                                    # appium starts only where changes are allowed (it installs its helpers).
                                    # On a device not listed --dka is refused (the fake driver models an
                                    # emulator), and Speedrun does not start when it would change the phone
                                    # (Android 13+, other agent builds, a Maestro driver, animation scales)
  animations: normal                # speedrun driver only: --animations normal leaves the scales at 1
tap_all:
  allowed: true                     # false: tap-all and explore-on-failure never tap in this app
  deny: [{text: "/(?i)delete account/"}, {id: logout_button}]   # never tapped (rules as for screens);
                                    # a text rule also reads the id's name (delete_account, btnDelete);
                                    # an element with the same text or id as a denied one is skipped too;
                                    # rule values are quoted text, and a broken
                                    # rule stops tap-all until validate passes
```

Tap-all and explore always skip a built-in deny list (reset, erase, uninstall, Wi-Fi, airplane mode,
Bluetooth, sign out, pay, buy, send, share, ...); `deny` adds to it. On a screen that shows a denied word
("Delete all your data?"), they also skip its confirm buttons (Yes, OK, Confirm, Continue, Accept,
Agree, Allow, Proceed, Apply, Enable, Turn on, Update, Got it). The same holds on a screen with warning words and
no denied word ("Are you sure?", "cannot be undone", "permanently"). When a tap puts denied words over the same
screen (a dialog), the screen's old option list is dropped: tap-all reloads the screen, explore
stops. A tap-all that tapped nothing fails (nothing tested is not a pass). What `--max-screens` and `--max-taps` leave
out is listed in the report ("not visited", "N more beyond --max-taps"). An unmapped screen whose
buttons change on every launch (feed items as buttons) cannot be found again and counts as
unreachable: map it. After each tap, when another app
or a system screen is in front, they never tap inside it: explore goes back, tap-all restarts the app
and replays the route to the screen it was on.

Hooks and the event checker run in the pack folder with `JOURNEYS_APP`, `JOURNEYS_SERIAL` and
`JOURNEYS_PACK` in their environment. Exit 0 means it worked.

## Screens

```yaml
screens:
  CHECKOUT:
    title: Checkout
    match:
      all:  [{text: "Checkout"}]                 # every rule must match an element
      any:  [{id: pay_button}, {text: "Pay"}]    # at least one, when given
      none: [{text: "Your cart is empty"}]       # none may match
```

A rule has `text` (exact, or `/regex/`), `id` and `kind` (Text, Button, Field, Switch, ...).
When several screens match, the one with the most matching rules wins; a tie is "unknown".
Some drivers join a row's texts into one label or cut long texts: match those with a `/regex/`.

## Edges

```yaml
edges:
  - from: CART
    to: CHECKOUT
    do: [{tap: "Checkout"}]          # actions: the neutral words below
```

Actions (the same words in edges and in `do:` steps, for every driver):

| Action | Does |
|---|---|
| `{tap: SEL}` | Tap the element. |
| `{type: "text"}` | Type into the focused field. |
| `{erase: 10}` | Delete characters in the focused field. |
| `{press: enter}` | Press a key: enter, back, home, tab, delete, search. |
| `{scroll_to: SEL}` | Scroll down until the element shows; `{scroll_to: {element: SEL, direction: up}}`. |
| `scroll` | Scroll down once. |
| `{see: SEL}`, `{not_see: SEL}` | Check the element is (not) on screen. |
| `back`, `hide_keyboard`, `wait_idle` | |
| `launch`, `{launch: {clear_state: true}}`, `stop` | Start or stop the pack's app. |

A selector (SEL) is `"Exact text"`, `"/regex/"` (must match the whole text), `{id: name}`, or
`{text: ..., id: ..., index: 1}` (index 0 is the first match). Words from a test framework
(`tapOn`, `inputText`, ...) are a pack error that names the word to use.

`goto` takes the shortest path of edges.

## Journeys

A file holds one journey or a list of them.

```yaml
name: buy-one
tags: [smoke]
start: {launch: true, clear_state: true}   # default: launch, keep state
steps:
  - hook: {name: new_user, args: {country: test}}
  - expect: HOME                  # this screen must show
  - goto: CHECKOUT                # walk the map
  - see: "Total"                  # text shown (not_see: text not shown)
  - input: "${APP_USER}"          # type
  - do: [{tap: "Pay"}]            # actions (table above)
  - event: {name: purchase}       # the pack's checker says it fired
```

## Sets

```yaml
sets:
  smoke: {tags: [smoke]}
  nightly: {journeys: [buy-one, refund]}
```

`run all` runs every journey.

## Proposals

| Kind | What promote does |
|---|---|
| expect-to-goto | a journey's `expect: S` becomes `goto: S` (the app now opens elsewhere first) |
| edge-to | an edge's `to:` changes (the tap now opens another known screen first) |
| new-edge | appends an edge |
| new-screen | appends a screen (named from its heading, or `--name`) and edges from it |

Promote edits the files in place and re-checks the pack. Review the diff in the pack's git.

## Sync with the team's flow files

A team that keeps Maestro flows in its project can keep them and the journeys as one, with one
source of truth:

```yaml
sync:
  source: journeys      # journeys (default): the journeys are the truth; or framework: the flows are
  dir: ../app/.maestro/journeys   # the team's flow folder, from the pack folder
  format: maestro       # the only format sync knows (the Appium export is one-way)
```

- `source: journeys`: `journeys sync` writes each journey as `<name>.yaml` in `dir`, with a
  `# generated by Journeys` header and a hash of the flow. A developer's own new flow there (no
  header) is imported as a draft; once that draft is promoted, `sync` writes `<name>.yaml` for it and
  deletes the developer's file (whatever its name), if that file did not change since the import.
  When the import left something out (a `runFlow`, a `swipe`: no neutral word), the flow is `lossy`:
  sync keeps it until you carry those parts into the journey and run `journeys sync --accept <file>`.
  When two flows have one name, the top-level one keeps it and one in a subfolder becomes a
  `<folder>-<name>` draft (`login.yaml` is `login`, `auth/login.yaml` is `auth-login`); a name that
  is still taken is reported as `name-taken`. A file that differs from a journey's file only in case (`Open-Display.yaml`) is never
  written over (macOS and Windows folders ignore case): it is `name-taken`, unless it is the flow
  that journey was imported from, which sync replaces like any other (the same holds for
  `<name>.yml`). A flow that another flow runs (`runFlow`) is never deleted: it is `referenced`
  until that flow points at the generated one. A developer flow renamed after its import is a new
  flow to sync: it imports as another draft, and the old record is dropped (or, with
  `source: framework`, reported as `flow-removed`). A draft renamed before it is
  promoted keeps its link to its flow (found by its steps), so no second copy appears.
- `source: framework`: the flows are the truth. `journeys sync` imports each flow as a draft and
  imports it again when the flow changes: the steps come from the flow, tags and other keys added in
  Journeys stay, comments in the journey file do not. A journey whose steps were edited in Journeys
  is reported (`journey-edited`, or `conflict` when the flow changed too), never overwritten.
- `journeys check-sync` lists every difference and exits 1 when there is any: missing, stale,
  hand-edited and orphan flows, unimported, edited, superseded or lossy developer flows, a flow with a
  journey's name, unreadable or invalid files, changed or removed flows, journeys edited in Journeys,
  conflicts and journeys with no flow. `sync` fixes
  what it safely can and lists the rest; it never overwrites a hand edit, and deletes a generated
  flow whose journey is gone only with `--prune`.
- Every `.yaml` and `.yml` file under `dir` is a flow, subfolders too, except Maestro's
  `config.yaml` (never written: a journey named `config` is reported) and files or folders that
  start with a dot.
- With `source: journeys`, to decline a developer's flow, delete the draft it became and run
  `journeys sync --decline <file>`: the flow stays out until it changes. Until then it is reported as
  `unlinked` (a draft renamed and edited before promotion looks the same: delete the old flow then).
  With `source: framework` the flow is the truth: delete the flow to drop it. A flow none of whose commands could be imported is reported (`empty-import`) until its
  draft gets real steps or is deleted. A journey that cannot be written as a flow is reported
  (`unexportable`); a generated flow under another file name is a `duplicate` (`--prune` deletes it,
  as it does orphans, unless it was edited by hand).
- `sync-state.json` in the pack remembers which flow became which journey: commit it with the pack.

## Export

`journeys export maestro NAME [-o flow.yaml]` and `journeys export appium NAME [-o name.e2e.js]` write
one journey (or draft) as the team's own test, to run with its own tool. Without `-o` the test goes to
stdout; `-o` never writes over a file. Each line about a part that could not be exported starts with
`NOTE` on stderr.

What both exports do:

- The whole journey runs at once, as one test. There are no detours, no explore and no proposals:
  those need Journeys.
- `goto` follows the map's path from the last `expect` or `goto` screen. With no such screen, or
  with a `do`, `input` or hook step since then, the start is unknown: the `goto` is left out with a
  note (add an `expect` before it).
  `expect: SCREEN` becomes checks for the screen's `all` rules that have a text or an id.
- Hook steps and `events.check` steps run only in Journeys: they are left out with a note.

Maestro (`appId` + flow): `${NAME}` stays a Maestro variable; run it with `maestro test -e NAME=...`.

Appium (a WebdriverIO mocha spec):

- It uses only WebdriverIO's globals (`describe`, `it`, `$`, `driver`) and the team's `wdio.conf`
  picks the Appium server and the device.
- It finds elements as the Journeys Appium driver does. It reads the page source and gives each
  element one label: its text, else its content description, else its hint. It leaves out elements
  entirely outside the window, counts an `index` in page order, and taps the centre of the visible
  part with a W3C pointer action. A selector matches the exact label, or the label as a whole-text
  regex. `{id: name}` and `{id: pkg:id/name}` both match by the name only, as in the driver.
- Patterns run as JavaScript regexes; Journeys reads them as Python's. Most read the same. A
  pattern JavaScript cannot read, such as `(?P<n>...)`, stops the spec when it loads, before it
  touches the device.
- Checks and taps wait 5 s for the element; `scroll_to` swipes (a slow drag) for up to 20 s until
  all of the element is on the screen; `wait_idle` waits up to 3 s for the screen to stop changing;
  `launch` waits up to 5 s for the app's elements. The test sets its own mocha timeout from the
  number of steps (mocha's default 60 s is too short for most journeys).
- Keys and `erase` use `mobile: pressKey`; `launch` with `clear_state` uses `mobile: clearApp`.
  An expired session fails the step at once; a page source that cannot be read is read again until
  the wait ends.
- Typed `${NAME}` is read from the environment when the spec runs (an account field from its env
  var), so the value is never in the file. A `${NAME}` in a selector is refused: write the value, or
  run that journey with Journeys.
- A journey where every step is left out is refused ("nothing to export").
- The export is one-way: a spec cannot be imported back, and `sync:` does not write specs.
- The spec is tested against a pretend WebdriverIO (`tests/wdio_sim.js`), not yet against a real
  WebdriverIO and Appium server. Run it once on your setup before you trust it.

## Secrets

Journeys masks `env:` values (as •••) in the text and JSON it writes to the state folder. Screenshots
are pictures and are not masked: a typed value can show in one. Values shorter than 3 characters (a short PIN) are not
masked either: too many ordinary words contain them. The driver's own
tool keeps its own logs outside that: Maestro under `~/.maestro`, Speedrun under
`<state>/speedrun-home`. Those logs can hold a typed value, such as a password. Use test accounts, and
clear or protect those folders on a shared machine.

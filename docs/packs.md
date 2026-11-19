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
driver: maestro              # the team's answer: maestro or speedrun (fake for tests); never guessed,
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
                                    # --dka sets "don't keep activities" over adb (maestro, speedrun).
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
Agree, Allow, Proceed, Apply, Enable, Turn on, Update, Got it). When a tap puts denied words over the same
screen (a dialog), the screen's old option list is dropped: tap-all reloads the screen, explore
stops. A tap-all that tapped nothing fails (nothing tested is not a pass). What `--max-screens` and `--max-taps` leave
out is listed in the report ("not visited", "N more beyond --max-taps"). After each tap, when another app
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

## Secrets

Journeys masks `env:` values (as •••) in the text and JSON it writes to the state folder. Screenshots
are pictures and are not masked: a typed value can show in one. Values shorter than 3 characters (a short PIN) are not
masked either: too many ordinary words contain them. The driver's own
tool keeps its own logs outside that: Maestro under `~/.maestro`, Speedrun under
`<state>/speedrun-home`. Those logs can hold a typed value, such as a password. Use test accounts, and
clear or protect those folders on a shared machine.

# Pack format

A pack is a folder. The core loads it by path (`--pack` or `JOURNEYS_PACK`). Keep a company's pack
in its own private repo; this repo has only `examples/settings-pack` (Android's Settings app).

```
my-pack/
  pack.yaml
  map/screens.yaml
  map/edges.yaml
  journeys/*.yaml
  sets.yaml
  hooks/          scripts the pack names
  fake/app.yaml   optional: a model of the app for `--driver fake`
```

## pack.yaml

```yaml
name: my-app                 # also the state folder's name
app: com.example.app
driver: speedrun             # or maestro
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
  allow_settings_changes: emulator  # where --dka may change a setting: emulator, any, or a list of serials
  animations: normal                # Speedrun's --animations: normal (default) leaves the scales at 1
tap_all:
  allowed: true                     # false: tap-all and explore-on-failure never tap in this app
  deny: [{text: "/(?i)delete account/"}, {id: logout_button}]   # never tapped (rules as for screens)
```

Tap-all and explore always skip a built-in deny list (reset, erase, uninstall, Wi-Fi, airplane mode,
Bluetooth, sign out, pay, buy, send, share, ...); `deny` adds to it. After each tap, when another app
or a system screen is in front, they go back and never tap inside it.

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
Speedrun shows an id only when it is the only one on the screen, and cuts texts at 80 characters.

## Edges

```yaml
edges:
  - from: CART
    to: CHECKOUT
    do: [{tapOn: "Checkout"}]        # Maestro / Speedrun flow commands
```

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
  - do: [{tapOn: "Pay"}]          # any flow commands
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

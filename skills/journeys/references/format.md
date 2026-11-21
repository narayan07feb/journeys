# Journeys pack format (exact)

## Folder

```
pack/
  pack.yaml            name, app, driver, env, accounts, hooks, events, devices, tap_all
  map/screens.yaml     how to know each screen
  map/edges.yaml       how to get from one screen to another
  journeys/*.yaml      promoted journeys (run in sets and `run all`)
  drafts/*.yaml        new journeys (run only with `run <name> --draft`)
  sets.yaml            named groups of journeys
  hooks/               scripts named in pack.yaml
  fake/app.yaml        optional model of the app for --driver fake
```

## pack.yaml

```yaml
name: shop                     # required
app: com.example.shop          # required: the app's package
driver: maestro                # the user's answer: maestro | speedrun | appium (fake for tests); missing = commands ask
project: ../shop-android       # optional: the app's project (doctor checks its test framework)
app_packages: [com.example.shop.web]   # optional: other packages the app's own screens run in
                               # (they count as the app: not_see, explore and tap-all stay inside them)
env: [SEARCH_TEXT]             # env vars journeys may use as ${SEARCH_TEXT}
accounts:
  main: {user: SHOP_USER, password: SHOP_PASSWORD}   # ${SHOP_USER} or ${main.user}; values from env only
hooks:
  new_user: [python3, hooks/new_user.py]   # argv; the step's args are added as one JSON text
events:
  check: [hooks/check_event.sh]            # prints {"fired": true} when the event was seen
tap_all:
  allowed: true                # false: tap-all and explore never tap in this app
  deny: [{text: "/(?i)delete account/"}]
```

## Screens (map/screens.yaml)

```yaml
screens:
  CART:                        # any name; the custom is UPPER-CASE words with dashes
    title: Cart
    match:
      all: [{text: "Your cart"}]              # every rule must match
      any: [{id: checkout}, {text: "/^Total/"}]   # at least one must match (if given)
      none: [{text: "Sign in"}]               # none may match
```

- A screen needs `all` or `any`.
- A rule is `{text: ...}`, `{id: ...}`, `{kind: ...}` or a mix of them. A bare string means `{text: ...}`.
- Rule text: `"Exact"`, or `"/regex/"`. In rules a regex may match PART of a text (use `^`, `$`).
- `id`: the full id (`com.example.shop:id/checkout`) or its name (`checkout`).
- `kind`: Text, Button, Field, Switch, CheckBox, Image, View, Tab, Link, Radio.
- The screen with the most matching rules wins. A tie is "unknown".

## Edges (map/edges.yaml)

```yaml
edges:
  - from: HOME
    to: CART
    do: [{tap: {id: cart}}]
  - from: CART
    to: HOME
    do: [back]
```

`goto` follows the shortest path of edges.

## Actions (in edges and in `do:` steps)

| Action | Does |
|---|---|
| `{tap: SEL}` | Tap the element (waits a moment for it). |
| `{type: "text"}` | Type into the focused field. |
| `{erase: 10}` | Delete 10 characters in the focused field (1 to 1000). |
| `{press: enter}` | A key: enter, back, home, tab, delete, search. |
| `{scroll_to: SEL}` | Scroll down until the element shows. Up: `{scroll_to: {element: SEL, direction: up}}`. |
| `scroll` | Scroll down once. |
| `{see: SEL}` / `{not_see: SEL}` | Check the element is / is not on screen. |
| `back`, `hide_keyboard`, `wait_idle` | `wait_idle` lets animations end. |
| `launch`, `{launch: {clear_state: true}}`, `stop` | Start (fresh) or stop the pack's app. |

Selector (SEL), for actions and for `see`/`not_see` steps:
- `"Exact text"`, the whole text.
- `"/regex/"`, which must match the WHOLE text (`"/Total.*/"` for "Total 12.50").
- `{id: name}`.
- `{text: "Save", index: 1}`: the second match (0 is the first). `text` and `id` can go together.
- Quote any text YAML would read as something else: numbers, times and yes/no words.
  Write `"0123"`, `"12:30"`, `"1_000"`, `"Yes"`, `"No"`, `"On"`, `"Off"`. Unquoted they are a
  validate error (`quote it`), the same for `type:` and `input:`.

## Journeys (journeys/*.yaml or drafts/*.yaml)

```yaml
name: buy-one                  # required; lowercase words with dashes; unique in the pack
tags: [smoke]                  # optional; sets can pick by tag
start: {launch: true, clear_state: false}   # optional; this is the default
steps:                         # required; each step is ONE of:
  - expect: HOME               # this screen must show (retries while the app settles)
  - goto: CART                 # walk the map's edges to this screen, then check it
  - see: "Your cart"           # SEL is on screen
  - not_see: "/Error.*/"       # SEL is not on screen
  - do: [{tap: Checkout}, {type: "4111"}, hide_keyboard]   # actions, in order
  - input: "${SEARCH_TEXT}"    # type text (env vars allowed)
  - hook: {name: new_user, args: {country: test}}          # run a pack hook
  - event: {name: purchase}    # the pack's event checker must say it fired
```

- A file holds one journey (a map) or a list of journeys. A draft file holds one.
- `goto` checks the screen it reaches: no `expect` is needed after it.
- `goto` finds its path from the screen the app is on when it runs. Put an `expect` (or another
  `goto`) before it so `validate` can check that a path exists.
- `see` and `not_see` check the current screen, whatever it is; they wait a moment for the element.
- After `do`, `input` or `hook` the screen is unknown again until the next `expect`/`goto`.
- `${NAME}` works in any step text for names declared in `env:` or `accounts:`; an undeclared
  name is a validate error.

## Sets (sets.yaml)

```yaml
sets:
  smoke: {tags: [smoke]}
  nightly: {journeys: [buy-one, refund]}
```

`journeys run smoke`, `journeys run all`. Drafts are never in a set or in `all`.

## Fake app (fake/app.yaml, for --driver fake)

A small model of the app, so journeys run without a device. Keep it in step with the real app.

```yaml
app: com.example.shop          # the pack's app
start: HOME                    # the screen `launch` opens
screens:
  HOME:
    package: com.example.shop  # optional: the app on screen (another package = left the app)
    elements:                  # top to bottom
      - {kind: Text, text: "Shop"}
      - {kind: Button, text: "Cart, 2 items", id: cart, to: CART}   # a tap opens CART
      - {kind: Field, text: "", id: search, state: [focused]}
      - {kind: Button, text: "Crash me", to: CRASH}                 # a tap kills the app
      - {kind: Button, text: "Skip", to: CART, replace: true}       # opens CART, closes this screen
  CART:
    back: HOME                 # optional: where back goes (default: the screen before)
    elements: [{kind: Text, text: "Your cart"}]
```

`kind`: Text, Button, Field, Switch, CheckBox, Image, View, Tab, Link, Radio. `state`: checked, selected, focused,
disabled. Selectors match `text` and `id` as on a device.

## Commands

| Command | |
|---|---|
| `journeys --pack P validate` | Check everything; exit 1 on any error (file:line). |
| `journeys --pack P new NAME --start SCREEN` | A draft skeleton in `drafts/`. |
| `journeys --pack P run NAME --draft --serial S` | Run one draft. `--driver D` overrides the pack. |
| `journeys --pack P run TARGET --serial S` | A journey, a set or `all`. `--no-heal`: no detours, no explore taps. |
| `journeys --pack P failures [F-n]`, `proposals [P-n]`, `promote P-n`, `reject P-n --why ...` | Results and fixes. |
| `journeys --pack P promote-journey NAME` | Draft to `journeys/`. |
| `journeys --pack P import maestro FLOWS...` | Maestro flows to drafts (what has no neutral word is listed). |
| `journeys --pack P export maestro NAME [-o F]` | A journey as a Maestro flow. |
| `journeys --pack NEW init --project APP [--app ID]` | A new pack; asks which driver the team uses (`--driver D` answers; the project's framework is a hint only). |
| `journeys --pack P look --serial S` | The screen on the device now (kind, "text", #id per element) and its map screen. |
| `journeys --pack P doctor` | Tools, drivers and devices. |

Global options go before the command: `--pack P` (or `$JOURNEYS_PACK`), `--state S` (results;
default `~/.local/state/journeys/<pack name>/`, or `$JOURNEYS_STATE`).

## Worked example (the example pack: Android Settings)

The map has SETTINGS-HOME, SETTINGS-DISPLAY, SETTINGS-ABOUT and SETTINGS-SEARCH, with edges both
ways. Goal: "Display shows the Dark theme option".

1. `journeys --pack examples/settings-pack new dark-theme-shown --start SETTINGS-HOME`
2. Edit `examples/settings-pack/drafts/dark-theme-shown.yaml`:

   ```yaml
   name: dark-theme-shown
   tags: [smoke]
   steps:
     - expect: SETTINGS-HOME
     - goto: SETTINGS-DISPLAY
     - do: [{scroll_to: "/Dark theme.*/"}]
     - see: "/Dark theme.*/"
   ```

   The row's label may hold more text ("Dark theme, Off"): some drivers read a row's texts as one
   label. The exact text `"Dark theme"` would not match it, so the selector is a regex for the whole
   text. Use exact text only for an element that shows just that text (a button, a title).
3. `journeys --pack examples/settings-pack validate` prints `OK pack settings-demo: ... 1 drafts ...`.
4. `journeys --pack examples/settings-pack run dark-theme-shown --draft --driver fake` passes on the
   pack's fake app; on a device: `--serial <serial> --driver <your team's tool>` (the example pack names no driver).
5. `journeys --pack examples/settings-pack promote-journey dark-theme-shown`.

Common FAILs and fixes:
- `unknown action tapOn (that is another tool's word: use tap)`: use the neutral word.
- `goto X: the map has no path from Y`: add an edge, or a screen between them.
- `expect names unknown screen X`: add X to map/screens.yaml.
- `a selector is "text", "/regex/", {id: name} or {text, id, index}`: fix the selector's form.

"""Journey -> a WebdriverIO spec, so a team that runs Appium keeps its own runner and config.

The spec uses only WebdriverIO's globals (describe, it, $$, driver, browser) and Appium's UiAutomator2
commands; the team's wdio.conf picks the server and the device. Selectors follow the Journeys rules as
far as UiAutomator allows: text is a regex over the whole text (then the content description), ids
match by name. The whole journey runs at once: no detours, no explore, no hooks.
"""
from __future__ import annotations

import json
import re

from ..actions import ActionError, escape
from ..drivers.appium import KEYCODES
from ..drivers.base import command_name, shown
from ..journey import Journey
from ..pack import _VAR, Pack
from . import journey_commands

SCROLL_MS = 20_000  # as the Appium driver: scroll_to looks for 20 s
WAIT_MS = 5_000

_HELPERS = """\
const WAIT = %(wait)d;

function env(name) {
  const value = process.env[name];
  if (value === undefined) throw new Error(`set ${name} in the environment (the pack never holds it)`);
  return value;
}

// The element on screen a selector names, matched by text or content description, counted top to bottom.
async function pick(sel) {
  const seen = new Map();
  for (const by of sel.by) {
    for (const el of await $$(`android=${by}`)) {
      if (!seen.has(el.elementId) && await el.isDisplayed()) seen.set(el.elementId, el);
    }
  }
  const els = [...seen.values()];
  const at = await Promise.all(els.map((el) => el.getLocation()));
  const order = els.map((el, i) => i).sort((a, b) => at[a].y - at[b].y || at[a].x - at[b].x || a - b);
  return sel.index < order.length ? els[order[sel.index]] : null;
}

async function find(sel) {
  let hit = null;
  await browser.waitUntil(async () => (hit = await pick(sel)) !== null, { timeout: WAIT, interval: 300 })
    .catch(() => {});
  if (!hit) throw new Error(`not on the screen: ${sel.show}`);
  return hit;
}

async function gone(sel) {
  const ok = await browser.waitUntil(async () => (await pick(sel)) === null, { timeout: WAIT, interval: 300 })
    .catch(() => false);
  if (!ok) throw new Error(`still on the screen: ${sel.show}`);
}

async function launch(clear) {
  await driver.terminateApp(APP);
  if (clear) await driver.execute("mobile: clearApp", { appId: APP });
  await driver.activateApp(APP);
}

// direction is where the finger goes: "up" shows what is further down.
async function swipe(direction) {
  const r = await driver.getWindowRect();
  await driver.execute("mobile: swipeGesture", {
    left: Math.round(r.x + r.width * 0.1), top: Math.round(r.y + r.height * 0.2),
    width: Math.round(r.width * 0.8), height: Math.round(r.height * 0.6), direction, percent: 0.6 });
}

async function scrollTo(sel, down) {
  const end = Date.now() + %(scroll)d;
  while (Date.now() < end) {
    if (await pick(sel)) return;
    await swipe(down ? "up" : "down");
    await driver.pause(300);
  }
  throw new Error(`not found while scrolling: ${sel.show}`);
}

async function idle() {
  let last = await driver.getPageSource();
  for (const end = Date.now() + 3000; Date.now() < end;) {
    await driver.pause(300);
    const now = await driver.getPageSource();
    if (now === last) return;
    last = now;
  }
}

async function type(text) {
  const el = await $(await driver.getActiveElement());
  await el.addValue(text);
}

async function erase(count) {
  for (let i = 0; i < count; i++) await driver.pressKeyCode(67);
}
"""


def _java(s: str) -> str:
    """A Java string literal for a UiSelector argument."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _rx(regex: str) -> str:
    """Control characters as regex escapes, so the UiSelector string holds none."""
    return re.sub(r"[\x00-\x1f\x7f]", lambda m: f"\\x{ord(m.group()):02x}", regex)


def _no_vars(value: str, what: str) -> str:
    if _VAR.search(value) or "\\$\\{" in value:
        raise ActionError(f"{what} {shown(value)!r} uses ${{NAME}}: the Appium export cannot put a variable "
                          "in a selector; write the value, or run this journey with Journeys")
    return value


def _sel(sel) -> str:
    """A shared-form selector -> the JS object pick() takes (UiSelector strings, index, a label)."""
    show = shown(sel)
    if isinstance(sel, str):
        sel = {"text": sel}
    base = "new UiSelector()"
    if "id" in sel:
        rid = _no_vars(sel["id"], "id")
        if ":id/" in rid:
            pattern = escape(rid)
        else:
            pattern = "(?:.*:id/)?" + escape(rid.rsplit("/", 1)[-1])
        base += f".resourceIdMatches({_java(_rx(pattern))})"
    by = [base]
    if "text" in sel:
        text = _java(_rx(_no_vars(str(sel["text"]), "text")))
        by = [f"{base}.textMatches({text})", f"{base}.descriptionMatches({text})"]
    return json.dumps({"by": by, "index": sel.get("index", 0), "show": show})


def _text(pack: Pack, value: str) -> str:
    """A JS expression for typed text: ${NAME} reads the env var it stands for at run time."""
    parts, at = [], 0
    for m in _VAR.finditer(value):
        if m.start() > at:
            parts.append(json.dumps(value[at:m.start()]))
        parts.append(f"env({json.dumps(_env_name(pack, m.group(1)))})")
        at = m.end()
    if at < len(value) or not parts:
        parts.append(json.dumps(value[at:]))
    return " + ".join(parts)


def _env_name(pack: Pack, name: str) -> str:
    if name in pack.env_names:
        return name
    for acc, fields in pack.accounts.items():
        for fld, var in (fields or {}).items():
            if name in (var, f"{acc}.{fld}"):
                return var
    raise ActionError(f"${{{name}}} is not declared in pack.yaml (env: or accounts:)")


def _js(pack: Pack, cmd) -> str:
    name, arg = command_name(cmd)
    if name == "launchApp":
        return f"await launch({'true' if isinstance(arg, dict) and arg.get('clearState') else 'false'});"
    if name == "stopApp":
        return "await driver.terminateApp(APP);"
    if name == "back":
        return "await driver.back();"
    if name == "hideKeyboard":
        return "if (await driver.isKeyboardShown()) await driver.hideKeyboard();"
    if name == "waitForAnimationToEnd":
        return "await idle();"
    if name == "scroll":
        return 'await swipe("up");'
    if name == "tapOn":
        return f"await (await find({_sel(arg)})).click();"
    if name == "assertVisible":
        return f"await find({_sel(arg)});"
    if name == "assertNotVisible":
        return f"await gone({_sel(arg)});"
    if name == "scrollUntilVisible":
        down = str(arg.get("direction", "DOWN")).upper() == "DOWN"
        return f"await scrollTo({_sel(arg['element'])}, {'true' if down else 'false'});"
    if name == "inputText":
        return f"await type({_text(pack, str(arg))});"
    if name == "eraseText":
        return f"await erase({int(arg)});"
    if name == "pressKey":
        return f"await driver.pressKeyCode({KEYCODES[str(arg).lower()]});  // {arg}"
    raise ActionError(f"{name} has no WebdriverIO form")


def export(pack: Pack, j: Journey) -> tuple[str, list[str]]:
    """A WebdriverIO spec for the journey, and what could not be exported."""
    notes: list[str] = []
    cmds = journey_commands(pack, j, "Appium", notes)
    if len(cmds) == j.launch:
        raise ActionError("no step of this journey has a WebdriverIO form; nothing to export")
    body = "\n".join(f"    {_js(pack, c)}" for c in cmds)
    notes.append("selectors run as UiAutomator regexes (Java) on an element's text or content description "
                 "(either one, never its hint), and an index counts matches top to bottom")
    head = (f"// Exported from Journeys journey {j.name} (pack {pack.name}).\n"
            "// Runs with WebdriverIO (mocha) and Appium UiAutomator2; your wdio.conf picks the server and "
            "the device.\n"
            f"const APP = {json.dumps(pack.app)};\n")
    helpers = _HELPERS % {"wait": WAIT_MS, "scroll": SCROLL_MS}
    test = (f"\ndescribe({json.dumps('Journeys: ' + j.name)}, () => {{\n"
            f"  it({json.dumps(j.name)}, async () => {{\n{body}\n  }});\n}});\n")
    return head + helpers + test, notes

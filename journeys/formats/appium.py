"""Journey -> a WebdriverIO spec, so a team that runs Appium keeps its own runner and config.

The spec finds elements the way the Journeys Appium driver does: it reads the page source, gives each
element one label (text, else content description, else hint), leaves out what is entirely off the
window, counts matches in page order, and taps the centre of the visible part with a W3C pointer
action. It uses only WebdriverIO's globals (describe, it, $, driver) and the team's wdio.conf picks
the server and the device. The whole journey runs at once: no detours, no explore, no hooks.
"""
from __future__ import annotations

import json
import re

from ..actions import ActionError
from ..drivers.appium import KEYCODES
from ..drivers.base import command_name, shown
from ..journey import Journey
from ..pack import _VAR, Pack
from ..regex_parity import JS, notes_for
from . import MapText, journey_commands

SCROLL_MS = 20_000  # as the Appium driver: scroll_to looks for 20 s
WAIT_MS = 5_000  # as the Appium driver: an element gets 5 s to come or go

_HELPERS = """\
const WAIT = %(wait)d;
// WebdriverIO puts the W3C code in the error's name (or error) and the server's words in its message.
const code = (e) => e.error ?? e.name;
// The session is gone, so nothing later can work: as the Journeys driver's AppiumError.fatal.
const fatal = (e) => ["invalid session id", "no such window"].includes(code(e)) || /invalid session/i.test(e.message);

function env(name) {
  const value = process.env[name];
  if (value === undefined) throw new Error(`set ${name} in the environment (the pack never holds it)`);
  return value;
}

// A selector from the journey. Journeys reads patterns as Python regexes; one JavaScript cannot read
// stops the spec here, before it touches the device.
function S(sel) {
  if (sel.rx) {
    try {
      sel.rx = new RegExp(`^(?:${sel.text})$`);
    } catch (e) {
      throw new Error(`the pattern ${JSON.stringify(sel.text)} works in Journeys but not in JavaScript: ${e.message}`);
    }
  }
  return sel;
}

function unxml(s) {
  const named = { lt: "<", gt: ">", quot: '"', apos: "'", amp: "&" };
  return s.replace(/&(#x[0-9a-fA-F]+|#[0-9]+|lt|gt|quot|apos|amp);/g, (m, e) =>
    e[0] !== "#" ? named[e] : String.fromCodePoint(e[1] === "x" ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10)));
}

// The screen as the Journeys driver reads it: elements with a label, an id or a tap, in page order,
// none entirely outside the window; each with the centre of its visible part.
async function screen() {
  const xml = await driver.getPageSource();
  const { width: w, height: h } = await driver.getWindowRect();
  const out = [];
  for (const [, attrs] of xml.matchAll(/<[\\w.$-]+((?:\\s+[\\w:.-]+="[^"]*")*)\\s*\\/?>/g)) {
    const a = {};
    for (const [, k, v] of attrs.matchAll(/([\\w:.-]+)="([^"]*)"/g)) a[k] = unxml(v);
    const b = /^\\[(-?\\d+),(-?\\d+)\\]\\[(-?\\d+),(-?\\d+)\\]$/.exec(a.bounds || "");
    const text = a.text || a["content-desc"] || a.hint || "";
    const id = (a["resource-id"] || "").split("/").pop();
    if (!b || !(text || id || a.clickable === "true")) continue;
    const [x1, y1, x2, y2] = b.slice(1).map(Number);
    const [c1, d1, c2, d2] = [Math.max(x1, 0), Math.max(y1, 0), Math.min(x2, w), Math.min(y2, h)];
    if (c1 < c2 && d1 < d2) {
      out.push({ text, id, x: Math.floor((c1 + c2) / 2), y: Math.floor((d1 + d2) / 2),
                 whole: c1 === x1 && d1 === y1 && c2 === x2 && d2 === y2 });
    }
  }
  return out;
}

function hit(sel, els) {
  if (sel.text === null && sel.id === null) return null;
  const hits = els.filter((e) => (sel.id === null || (sel.id !== "" && e.id === sel.id))
    && (sel.text === null || (sel.exact !== false && e.text === sel.text) || (sel.rx !== false && sel.rx.test(e.text))));
  return hits[sel.index] ?? null;
}

// The element now, or after looking again for up to `wait` ms. A screen that cannot be read until
// the end fails with its own error; an expired session fails at once.
async function look(sel, wait) {
  const end = Date.now() + wait;
  for (;;) {
    let el = null, err = null;
    try {
      el = hit(sel, await screen());
    } catch (e) {
      if (fatal(e)) throw e;
      err = e;
    }
    if (el || Date.now() >= end) {
      if (!el && err) throw err;
      return el;
    }
    await driver.pause(500);
  }
}

async function find(sel) {
  const el = await look(sel, WAIT);
  if (!el) throw new Error(`not on the screen: ${sel.show}`);
  return el;
}

async function gone(sel) {
  const end = Date.now() + WAIT;
  while (await look(sel, 0)) {
    if (Date.now() >= end) throw new Error(`still on the screen: ${sel.show}`);
    await driver.pause(500);
  }
}

// A finger: down at the first point, a slow drag through the rest (no fling), up. One point is a tap.
async function finger(points) {
  const [[x, y], ...rest] = points;
  const actions = [{ type: "pointerMove", duration: 0, x, y }, { type: "pointerDown", button: 0 },
    ...rest.map(([x, y]) => ({ type: "pointerMove", duration: 600, x, y })), { type: "pointerUp", button: 0 }];
  await driver.performActions([{ type: "pointer", id: "finger", parameters: { pointerType: "touch" }, actions }]);
}

async function tap(sel) {
  const el = await find(sel);
  await finger([[el.x, el.y]]);
}

// up: the finger moves up, to show what is further down.
async function swipe(up) {
  const { width: w, height: h } = await driver.getWindowRect();
  const [a, b] = up ? [Math.trunc(h * 0.7), Math.trunc(h * 0.3)] : [Math.trunc(h * 0.3), Math.trunc(h * 0.7)];
  await finger([[Math.floor(w / 2), a], [Math.floor(w / 2), b]]);
}

// Until all of the element is on the screen, as Maestro does.
async function scrollTo(sel, up) {
  const end = Date.now() + %(scroll)d;
  while (Date.now() < end) {
    if ((await look(sel, 0))?.whole) return;
    await swipe(up);
    await driver.pause(300);
  }
  if (!(await look(sel, 0))?.whole) throw new Error(`not found while scrolling: ${sel.show}`);
}

// A fresh start; activateApp returns before the first screen is drawn, so wait for the app's elements.
async function launch(clear) {
  await driver.execute("mobile: terminateApp", { appId: APP });
  if (clear) await driver.execute("mobile: clearApp", { appId: APP });
  await driver.execute("mobile: activateApp", { appId: APP });
  for (const end = Date.now() + WAIT; Date.now() < end; await driver.pause(300)) {
    try {
      if ((await driver.getPageSource()).includes(`package="${APP}"`)) return;
    } catch (e) {
      if (fatal(e)) throw e;  // else the screen is still being drawn: look again
    }
  }
}

async function idle() {
  let last = null;
  for (const end = Date.now() + 3000; Date.now() < end;) {
    const now = await driver.getPageSource();
    if (now === last) return;
    last = now;
    await driver.pause(300);
  }
}

async function key(keycode) {
  await driver.execute("mobile: pressKey", { keycode });
}

// As the Journeys driver: the old route, else the mobile: command; "no keyboard" is nothing to hide,
// a keyboard that "cannot be hidden" fails.
async function hideKeyboard() {
  try {
    try {
      await driver.hideKeyboard();
    } catch (e) {
      if (!["unknown command", "unknown method"].includes(code(e))) throw e;
      await driver.execute("mobile: hideKeyboard", {});
    }
  } catch (e) {
    if (fatal(e) || !/keyboard.*(not present|not shown)|no keyboard/i.test(e.message)) throw e;
  }
}

async function type(text) {
  const el = await $(await driver.getActiveElement());
  await el.addValue(text);
}
"""


_ESCAPED_VAR = re.compile(r"\\\$\\\{([A-Za-z_](?:[A-Za-z0-9_]|\\\.)*)\\\}")  # ${NAME} after escape()


def _no_vars(value: str, what: str, declared: set[str]) -> str:
    """Refuse ${NAME} in a selector. A plain text's ${NAME} reaches here escaped (\\$\\{NAME\\}); a pattern
    can hold that spelling on purpose for the literal text, so the escaped one counts only for a name the
    pack declares (Journeys substitutes those; validate refuses an undeclared one in a plain text)."""
    if _VAR.search(value) or any(m[1].replace("\\.", ".") in declared for m in _ESCAPED_VAR.finditer(value)):
        raise ActionError(f"{what} {shown(value)!r} uses ${{NAME}}: the Appium export cannot put a variable "
                          "in a selector; write the value, or run this journey with Journeys")
    return value


def _sel(sel, sels: list[str], declared: set[str]) -> str:
    """A shared-form selector -> SEL[n], an S({...}) made when the spec loads, matched as
    journeys.drivers.base.find does."""
    show = shown(sel)
    if isinstance(sel, str):
        sel = {"text": sel}
    try:
        index = int(sel.get("index", 0))
    except (TypeError, ValueError):
        raise ActionError(f"index {sel.get('index')!r} is not a number") from None
    out = {"text": None, "rx": False, "id": None, "index": index, "show": show}
    if "id" in sel:
        want = _no_vars(str(sel["id"]), "id", declared)  # the driver reports bare names, so only the name counts
        out["id"] = want.rpartition(":id/")[2] if ":id/" in want else want.rsplit("/", 1)[-1]
    if "text" in sel:
        from_map = isinstance(sel["text"], MapText)  # a map rule: never a variable, only a pattern
        out["text"] = str(sel["text"]) if from_map else _no_vars(str(sel["text"]), "text", declared)
        try:
            re.compile(out["text"])
            out["rx"] = True
        except re.error:
            pass  # Journeys then matches the exact text only
        if from_map:
            out["exact"] = False
    sels.append(f"S({json.dumps(out)})")
    return f"SEL[{len(sels) - 1}]"


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


def _count(arg) -> int:
    try:
        return int(arg if arg is not None else 50)  # as the driver: erase with no number takes 50
    except (TypeError, ValueError):
        raise ActionError(f"erase takes a number of characters, not {arg!r}") from None


def _js(pack: Pack, cmd, sels: list[str]) -> str:
    name, arg = command_name(cmd)
    if name == "launchApp":
        return f"await launch({'true' if isinstance(arg, dict) and arg.get('clearState') else 'false'});"
    if name == "stopApp":
        return 'await driver.execute("mobile: terminateApp", { appId: APP });'
    if name == "back":
        return "await driver.back();"
    if name == "hideKeyboard":
        return "await hideKeyboard();"
    if name == "waitForAnimationToEnd":
        return "await idle();"
    if name == "scroll":
        return "await swipe(true);"
    if name == "tapOn":
        return f"await tap({_sel(arg, sels, pack.declared())});"
    if name == "assertVisible":
        return f"await find({_sel(arg, sels, pack.declared())});"
    if name == "assertNotVisible":
        return f"await gone({_sel(arg, sels, pack.declared())});"
    if name == "scrollUntilVisible":
        opts = arg if isinstance(arg, dict) and "element" in arg else {"element": arg}
        up = str(opts.get("direction", "DOWN")).upper() != "UP"
        return f"await scrollTo({_sel(opts['element'], sels, pack.declared())}, {'true' if up else 'false'});"
    if name == "inputText":
        return f"await type({_text(pack, str(arg))});"
    if name == "eraseText":
        return f"for (let i = 0; i < {_count(arg)}; i++) await key({KEYCODES['backspace']});"
    if name == "pressKey":
        return f"await key({KEYCODES[str(arg).lower()]});  // {arg}"
    raise ActionError(f"{name} has no WebdriverIO form")


def export(pack: Pack, j: Journey) -> tuple[str, list[str]]:
    """A WebdriverIO spec for the journey, and what could not be exported."""
    notes: list[str] = []
    cmds = journey_commands(pack, j, "Appium", notes)
    if len(cmds) == j.launch:
        raise ActionError("no step of this journey has a WebdriverIO form; nothing to export")
    sels: list[str] = []
    body = "\n".join(f"    {_js(pack, c, sels)}" for c in cmds)
    scrolls = sum(command_name(c)[0] == "scrollUntilVisible" for c in cmds)
    erased = sum(_count(command_name(c)[1]) for c in cmds if command_name(c)[0] == "eraseText")
    # mocha's own 60 s is too short for a journey; erase is one call per character
    timeout = 60_000 + 10_000 * len(cmds) + SCROLL_MS * scrolls + 200 * erased
    notes += notes_for(cmds, JS)  # refuses or notes; ids match by name, not as a pattern
    head = (f"// Exported from Journeys: journey {json.dumps(j.name)}, pack {json.dumps(pack.name)}.\n"
            "// Runs with WebdriverIO (mocha) and Appium UiAutomator2; your wdio.conf picks the server and "
            "the device.\n"
            f"const APP = {json.dumps(pack.app)};\n")
    helpers = _HELPERS % {"wait": WAIT_MS, "scroll": SCROLL_MS}
    table = "\nconst SEL = [\n" + "".join(f"  {x},\n" for x in sels) + "];\n"
    test = (f"\ndescribe({json.dumps('Journeys: ' + j.name)}, () => {{\n"
            f"  it({json.dumps(j.name)}, async function () {{\n    this.timeout({timeout});\n{body}\n  }});\n}});\n")
    return head + helpers + table + test, notes

// A pretend WebdriverIO + Appium UiAutomator2 for the export tests: runs an exported spec against a small fake app.
// usage: node wdio_sim.js <spec.js> <app.json>; prints one JSON line per call, then PASS or FAIL <message>.
// app.json: {"app": "pkg", "start": "NAME", "screens": {"NAME": [element, ...] or {"xml": "<page source>"}}}
// element: {text, desc, hint, id, clickable, bounds: [x1, y1, x2, y2], y, below, go, field}
// The page source is XML as UiAutomator2 writes it; the last call line is ["end", <ms on the fake clock>]. An element with go: SCREEN moves there when tapped;
// below: N is off the window until N swipes up; field: true takes the focus. Any WebdriverIO method,
// mobile: command or argument the sim does not know throws, so a wrong call fails the test.
"use strict";
const fs = require("fs");
const [spec, appFile] = process.argv.slice(2);
const app = JSON.parse(fs.readFileSync(appFile, "utf8"));
const W = 1080, H = 2400;
let clock = 0, screen = null, scrolled = 0, focus = null, keyboard = false, running = false;
const log = (...call) => console.log(JSON.stringify(call));
Date.now = () => clock;

const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
function elements() {
  const s = app.screens[screen];
  return Array.isArray(s) ? s : [];
}
function bounds(e, i) {
  if (e.bounds) return e.bounds;
  const y = e.y ?? 100 + i * 100;
  return (e.below || 0) > scrolled ? [0, H + 100 + i * 100, W, H + 180 + i * 100] : [0, y, W, y + 80];
}
function source() {
  if (!running) return '<?xml version="1.0" encoding="UTF-8"?><hierarchy rotation="0"><android.widget.FrameLayout package="launcher" bounds="[0,0][1080,2400]"/></hierarchy>';
  const s = app.screens[screen];
  if (!Array.isArray(s)) return s.xml;
  const nodes = s.map((e, i) => {
    const rid = e.id === undefined ? "" : e.id.includes(":id/") ? e.id : `${app.app}:id/${e.id}`;
    const [x1, y1, x2, y2] = bounds(e, i);
    return `<android.widget.TextView index="${i}" package="${esc(e.pkg || app.app)}" class="android.widget.TextView" `
      + `text="${esc(e.text || "")}" content-desc="${esc(e.desc || "")}"${e.hint ? ` hint="${esc(e.hint)}"` : ""} `
      + `resource-id="${esc(rid)}" clickable="${e.clickable || e.go || e.field ? "true" : "false"}" `
      + `bounds="[${x1},${y1}][${x2},${y2}]" />`;
  });
  return `<?xml version="1.0" encoding="UTF-8"?><hierarchy rotation="0"><android.widget.FrameLayout package="${app.app}" bounds="[0,0][${W},${H}]">${nodes.join("")}</android.widget.FrameLayout></hierarchy>`;
}
function tapAt(x, y) {
  const els = elements();
  const i = els.map((e, k) => k).reverse().find((k) => {
    const [x1, y1, x2, y2] = bounds(els[k], k);
    return x1 <= x && x < x2 && y1 <= y && y < y2;
  });
  const e = els[i];
  log("tap", e ? e.text || e.desc || e.hint || e.id : null, x, y);
  if (!e) return;
  if (e.field) { focus = e; keyboard = true; }
  if (e.go) { screen = e.go; scrolled = 0; }
}
function strict(name, obj) {
  return new Proxy(obj, { get(t, k) {
    if (k in t) return t[k];
    if (typeof k === "symbol" || k === "then") return undefined;
    throw new Error(`${name}.${String(k)} is not a WebdriverIO method the sim knows`);
  } });
}
function keys(name, args, want) {
  const got = Object.keys(args).sort().join(",");
  if (got !== [...want].sort().join(",")) throw new Error(`${name} takes ${want.join(", ")}, not ${got}`);
}
const MOBILE = {
  "mobile: clearApp": (a) => { keys("mobile: clearApp", a, ["appId"]); log("mobile: clearApp", a.appId); },
  "mobile: pressKey": (a) => { keys("mobile: pressKey", a, ["keycode"]); log("key", a.keycode); },
};
const field = strict("element", { addValue: async (t) => { log("type", t); } });
globalThis.$ = async (ref) => { if (ref !== "FOCUSED") throw new Error("$ wants the active element"); return field; };
globalThis.driver = strict("driver", {
  terminateApp: async (id) => { log("terminate", id); running = false; },
  activateApp: async (id) => { log("activate", id); running = true; screen = app.start; scrolled = 0; },
  execute: async (name, args) => {
    if (!MOBILE[name]) throw new Error(`unknown mobile: command ${name}`);
    MOBILE[name](args);
  },
  performActions: async (acts) => {
    keys("performActions", acts[0], ["type", "id", "parameters", "actions"]);
    if (acts.length !== 1 || acts[0].type !== "pointer" || acts[0].parameters.pointerType !== "touch") throw new Error("one touch pointer");
    const a = acts[0].actions, moves = a.filter((m) => m.type === "pointerMove");
    if (a[1].type !== "pointerDown" || a[a.length - 1].type !== "pointerUp") throw new Error("down, then up");
    if (moves.length === 1) return tapAt(moves[0].x, moves[0].y);
    const up = moves[1].y < moves[0].y;
    log("swipe", up ? "up" : "down", moves[1].duration);
    scrolled = up ? scrolled + 1 : Math.max(0, scrolled - 1);
  },
  back: async () => log("back"),
  isKeyboardShown: async () => keyboard,
  hideKeyboard: async () => { log("hideKeyboard"); keyboard = false; },
  getWindowRect: async () => ({ x: 0, y: 0, width: W, height: H }),
  getActiveElement: async () => { if (!focus) throw new Error("no such element"); return "FOCUSED"; },
  getPageSource: async () => {  // app.json reads: ["ok", "fail", "dead", ...] for the reads while the app runs
    const r = running && app.reads ? app.reads.shift() ?? "ok" : "ok";
    if (r === "dead") { app.reads.unshift("dead"); throw new Error("invalid session id: the session is gone"); }
    if (r === "fail") throw new Error("cannot read the screen");
    return source();
  },
  pause: async (ms) => { clock += ms; },
});
const tests = [];
globalThis.describe = (name, fn) => fn();
globalThis.it = (name, fn) => tests.push(fn);
(async () => {
  try {
    require(require("path").resolve(spec));
    for (const t of tests) await t.call({ timeout: (ms) => log("timeout", ms) });
    log("end", clock);
    console.log("PASS");
  } catch (e) {
    log("end", clock);
    console.log(`FAIL ${e.message}`);
  }
})();

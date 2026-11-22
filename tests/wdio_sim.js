// A pretend WebdriverIO + Appium for the export tests: runs an exported spec against a small fake app.
// usage: node wdio_sim.js <spec.js> <app.json>; prints one JSON line per call, then PASS or FAIL <message>.
// app.json: {"app": "pkg", "screens": {"NAME": [{text, desc, id, go, below, field}]}, "start": "NAME"}
// An element with go: SCREEN moves there on tap; below: N shows only after N swipes up; field: true takes focus.
"use strict";
const fs = require("fs");
const [spec, appFile] = process.argv.slice(2);
const app = JSON.parse(fs.readFileSync(appFile, "utf8"));
let clock = 0, screen = null, scrolled = 0, focus = null, keyboard = false, running = false;
const typed = [];
const log = (...call) => console.log(JSON.stringify(call));
Date.now = () => clock;

function unjava(s) { return s.replace(/\\(.)/g, "$1"); }
function parse(by) {
  const m = /^android=new UiSelector\(\)((?:\.\w+\("(?:[^"\\]|\\.)*"\))*)$/.exec(by);
  if (!m) throw new Error(`bad UiSelector: ${by}`);
  return [...m[1].matchAll(/\.(\w+)\("((?:[^"\\]|\\.)*)"\)/g)].map(([, k, v]) => [k, new RegExp(`^(?:${unjava(v)})$`)]);
}
function element(e) {
  return {
    elementId: `${screen}/${app.screens[screen].indexOf(e)}`,
    getLocation: async () => ({ x: e.x || 0, y: e.y ?? app.screens[screen].indexOf(e) * 100 }),
    isDisplayed: async () => (e.below || 0) <= scrolled,
    click: async () => {
      log("click", e.text || e.desc || e.id);
      if (e.field) { focus = e; keyboard = true; }
      if (e.go) { screen = e.go; scrolled = 0; }
    },
  };
}
const field = { addValue: async (t) => { typed.push(t); log("type", t); } };
const ATTR = { textMatches: "text", descriptionMatches: "desc", resourceIdMatches: "id" };
globalThis.$$ = async (by) => {
  const rules = parse(by);
  if (!running || !screen) return [];
  return app.screens[screen].filter((e) => rules.every(([k, rx]) => rx.test(e[ATTR[k]] ?? ""))).map(element);
};
globalThis.$ = async (ref) => { if (ref !== "FOCUSED") throw new Error("$ wants the active element"); return field; };
globalThis.browser = {
  waitUntil: async (cond, { timeout, interval }) => {
    for (const end = clock + timeout; ; clock += interval) {
      if (await cond()) return true;
      if (clock >= end) throw new Error("waitUntil timeout");
    }
  },
};
globalThis.driver = {
  terminateApp: async (id) => { log("terminate", id); running = false; },
  activateApp: async (id) => { log("activate", id); running = true; screen = app.start; scrolled = 0; },
  execute: async (name, args) => {
    log(name, args.appId ?? args.direction);
    if (name === "mobile: swipeGesture" && args.direction === "up") scrolled++;
    if (name === "mobile: swipeGesture" && args.direction === "down") scrolled = Math.max(0, scrolled - 1);
  },
  back: async () => log("back"),
  isKeyboardShown: async () => keyboard,
  hideKeyboard: async () => { log("hideKeyboard"); keyboard = false; },
  getWindowRect: async () => ({ x: 0, y: 0, width: 1080, height: 2400 }),
  getActiveElement: async () => { if (!focus) throw new Error("no such element"); return "FOCUSED"; },
  getPageSource: async () => `${screen}:${scrolled}`,
  pause: async (ms) => { clock += ms; },
  pressKeyCode: async (code) => log("key", code),
};
const tests = [];
globalThis.describe = (name, fn) => fn();
globalThis.it = (name, fn) => tests.push(fn);
require(require("path").resolve(spec));
(async () => {
  try {
    for (const t of tests) await t();
    console.log("PASS");
  } catch (e) {
    console.log(`FAIL ${e.message}`);
  }
})();

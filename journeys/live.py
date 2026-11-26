"""A small local page (127.0.0.1 only): the run in progress, runs, the work queue, agent activity and usage."""
from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterator

from .results import Store, read_json

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Journeys tracker</title>
<style>
:root{--bg:#fff;--fg:#1b1b1b;--muted:#666;--ok:#1a7f37;--bad:#cf222e;--line:#ddd;--bar:#d0d7de}
@media (prefers-color-scheme:dark){:root{--bg:#111;--fg:#eee;--muted:#999;--ok:#3fb950;--bad:#f85149;--line:#333;--bar:#30363d}}
body{background:var(--bg);color:var(--fg);font:15px/1.4 system-ui,sans-serif;margin:0 auto;max-width:1000px;padding:16px}
h1{font-size:20px;margin:0 0 8px}h2{font-size:16px;margin:28px 0 6px}h3{font-size:14px;margin:14px 0 4px}
.muted{color:var(--muted)}.passed{color:var(--ok)}.failed{color:var(--bad)}
nav a{color:inherit;margin-right:14px;font-size:14px}
.wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:14px}
th{text-align:left;color:var(--muted);font-weight:500;padding:4px 8px 4px 0}
td{border-top:1px solid var(--line);padding:4px 8px 4px 0;vertical-align:top;overflow-wrap:anywhere}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
.trend{display:flex;gap:2px;align-items:flex-end;height:36px;margin:6px 0}
.trend span{width:8px;background:var(--bar)}.trend span.passed{background:var(--ok)}.trend span.failed{background:var(--bad)}
</style></head><body><h1>Journeys tracker</h1>
<nav><a href="#runs">Runs</a><a href="#queue">Work queue</a><a href="#activity">Agent activity</a><a href="#usage">Agent usage</a></nav>
<h2 id="runs">Runs</h2><div id="live" class="muted">no run in progress</div>
<div class="trend" id="trend" title="last runs, oldest first"></div>
<div class="wrap"><table><thead><tr><th>Run</th><th>Target</th><th>By</th><th>Status</th><th class="n">Passed</th>
<th class="n">Failed</th><th class="n">Seconds</th></tr></thead><tbody id="runrows"></tbody></table></div>
<h2 id="queue">Work queue</h2>
<h3>Open failures <span id="fcount" class="muted"></span></h3><div class="wrap"><table><tbody id="failures"></tbody></table></div>
<h3>Open proposals</h3><div class="wrap"><table><tbody id="proposals"></tbody></table></div>
<h3>Drafts waiting for promote-journey</h3><div id="drafts" class="muted"></div>
<h2 id="activity">Agent activity</h2>
<div class="wrap"><table><thead><tr><th>When</th><th>Who</th><th>What</th><th>Detail</th></tr></thead>
<tbody id="acts"></tbody></table></div>
<h2 id="usage">Agent usage</h2><p class="muted">Journeys built and issues solved, per person or agent.
Fixed: journeys (and set hooks) that passed again after failing (one pass is one issue, however many failure records).
Solved: fixed, plus failures healed by a promoted proposal. Decided: promotes and rejects. Changes:
detours accepted. Who fixed a failure is a best guess. The per-day table shows the last 14 days.</p>
<div class="wrap"><table><thead><tr><th>Who</th><th class="n">Drafts</th><th class="n">Journeys built</th>
<th class="n">Proposals decided</th><th class="n">Issues fixed</th><th class="n">Solved</th><th class="n">Avg fix (min)</th>
<th class="n">Changes</th><th class="n">Runs</th><th class="n">Tool calls</th></tr></thead><tbody id="actors"></tbody></table></div>
<h3>Per day</h3><div class="wrap"><table><thead><tr><th>Day</th><th>Who</th><th class="n">Drafts</th><th class="n">Built</th>
<th class="n">Solved</th><th class="n">Runs</th><th class="n">Tool calls</th></tr></thead><tbody id="days"></tbody></table></div>
<script>
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const when=t=>t?new Date(t*1000).toLocaleString():"";
const set=(id,html)=>{const e=document.getElementById(id);if(e.innerHTML!==html)e.innerHTML=html};
const detail=a=>Object.entries(a).filter(([k])=>!["time","actor","action"].includes(k))
  .map(([k,v])=>`${esc(k)}: ${esc(typeof v==="object"?JSON.stringify(v):v)}`).join(" · ");
const num=v=>v??"";
async function tick(){
  try{
    const l=await (await fetch("live.json")).json();
    if(l.run) set("live",(l.status==="running"?"":"last run: ")+`<b>${esc(l.journey)}</b> <span class="${esc(l.status)}">${esc(l.status)}</span> · ${esc(l.step)}`+
      ` · ${esc(l.text)} <span class="muted">· run ${esc(l.run)}</span>`+(l.message?` <span class="failed">${esc(l.message)}</span>`:""));
    const t=await (await fetch("tracker.json")).json();
    if(t.error){set("live",`<span class="failed">tracker: ${esc(t.error)}</span>`);return}
    set("fcount",t.queue.failures_open>t.queue.failures.length?`(newest ${t.queue.failures.length} of ${t.queue.failures_open})`:"");
    set("trend",t.runs.map(r=>{const n=r.passed+r.failed,h=n?Math.max(4,36*r.passed/n):4;
      return `<span class="${esc(r.status)}" style="height:${h}px" title="${esc(r.id)}: ${r.passed}/${n}"></span>`}).join(""));
    set("runrows",t.runs.slice().reverse().map(r=>`<tr><td>${esc(r.id)}</td><td>${esc(r.target)}</td><td>${esc(r.actor)}</td>`+
      `<td class="${esc(r.status)}">${esc(r.status)}</td><td class="n">${r.passed}</td><td class="n">${r.failed}</td>`+
      `<td class="n">${num(r.seconds)}</td></tr>`).join(""));
    set("failures",t.queue.failures.slice().reverse().map(f=>`<tr><td>${esc(f.id)}</td><td>${esc(f.journey)}</td>`+
      `<td>${esc(f.message)}</td></tr>`).join("")||`<tr><td class="muted">none</td></tr>`);
    set("proposals",t.queue.proposals.map(p=>`<tr><td>${esc(p.id)}</td><td>${esc(p.summary)}</td></tr>`).join("")||
      `<tr><td class="muted">none</td></tr>`);
    set("drafts",t.queue.drafts.map(esc).join(", ")||"none");
    set("acts",t.activity.map(a=>`<tr><td>${esc(when(a.time))}</td><td>${esc(a.actor)}</td><td>${esc(a.action)}</td>`+
      `<td>${detail(a)}</td></tr>`).join(""));
    set("actors",Object.entries(t.usage.actors).map(([k,u])=>`<tr><td>${esc(k)}</td><td class="n">${u.drafts}</td>`+
      `<td class="n">${u.journeys_built}</td><td class="n">${u.decided}</td><td class="n">${u.fixed}</td><td class="n">${u.solved}</td>`+
      `<td class="n">${num(u.avg_fix_minutes)}</td><td class="n">${u.changes}</td><td class="n">${u.runs}</td><td class="n">${u.tool_calls}</td></tr>`).join(""));
    set("days",Object.entries(t.usage.days).reverse().flatMap(([d,rows])=>Object.entries(rows).map(([k,u])=>
      `<tr><td>${esc(d)}</td><td>${esc(k)}</td><td class="n">${u.drafts}</td><td class="n">${u.journeys_built}</td><td class="n">${u.solved}</td>`+
      `<td class="n">${u.runs}</td><td class="n">${u.tool_calls}</td></tr>`)).join(""));
  }catch(e){}
}
tick();setInterval(tick,2000);
</script></body></html>"""

_JSON = {"/live.json": ("live.json", {}), "/failures.json": ("failures.json", []),
         "/proposals.json": ("proposals.json", [])}


def make_handler(store: Store, drafts=lambda: [], hook_keys=lambda: None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
            if host not in ("127.0.0.1", "localhost"):  # a DNS-rebinding page cannot read the activity log
                return self._send(403, b"forbidden", "text/plain")
            path = self.path.split("?")[0]
            if path == "/":
                return self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            if path in _JSON:
                name, default = _JSON[path]
                return self._send(200, json.dumps(read_json(store.root / name, default)).encode(),
                                  "application/json")
            if path == "/tracker.json":
                from .tracker import summary
                try:
                    body = json.dumps(summary(store, drafts(), hook_keys()), default=str, allow_nan=False).encode()
                except Exception as e:  # the page says what broke, the server keeps serving
                    return self._send(500, json.dumps({"error": str(e)}).encode(), "application/json")
                return self._send(200, body, "application/json")
            if path == "/history.json":
                return self._send(200, json.dumps(store.history()).encode(), "application/json")
            self._send(404, b"not found", "text/plain")

    return Handler


def serve(store: Store, port: int = 8765, drafts=lambda: [], hook_keys=lambda: None) -> ThreadingHTTPServer:
    """`drafts` names the pack's drafts and `hook_keys` its set hooks, for the work queue (read again
    on each request)."""
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(store, drafts, hook_keys))


def watch(store: Store, interval: float = 0.5, stop_after: float | None = None) -> Iterator[dict]:
    """Yield the live state each time it changes (for `journeys watch` and the Droid Lite panel)."""
    path, last, t0 = store.root / "live.json", None, time.monotonic()
    while stop_after is None or time.monotonic() - t0 < stop_after:
        cur = read_json(path, None)
        if cur and cur != last:
            last = cur
            yield cur
        time.sleep(interval)


def panel(live: dict) -> dict:
    """Droid Lite's watch line: a title, a status and rows."""
    return {"title": f"{live.get('journey', '')} ({live.get('run', '')})", "status": live.get("status", ""),
            "rows": [["Step", f"{live.get('step', '')}: {live.get('text', '')}"],
                     ["Screen", live.get("screen") or "?"], *([["Error", live["message"]]] if live.get("message") else [])]}

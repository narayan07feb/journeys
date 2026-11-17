"""A small local page (127.0.0.1 only) that shows the run in progress, failures and proposals."""
from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterator

from .results import Store, read_json

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Journeys live</title>
<style>
:root{--bg:#fff;--fg:#1b1b1b;--muted:#666;--ok:#1a7f37;--bad:#cf222e;--line:#ddd}
@media (prefers-color-scheme:dark){:root{--bg:#111;--fg:#eee;--muted:#999;--ok:#3fb950;--bad:#f85149;--line:#333}}
body{background:var(--bg);color:var(--fg);font:15px/1.4 system-ui,sans-serif;margin:0 auto;max-width:900px;padding:16px}
h1{font-size:20px}h2{font-size:16px;margin-top:24px}.muted{color:var(--muted)}
.passed{color:var(--ok)}.failed{color:var(--bad)}table{border-collapse:collapse;width:100%}
td{border-top:1px solid var(--line);padding:4px 8px 4px 0;vertical-align:top;overflow-wrap:anywhere}
</style></head><body><h1>Journeys</h1><div id="live" class="muted">waiting for a run…</div>
<h2>Recent failures</h2><table id="failures"></table><h2>Open proposals</h2><table id="proposals"></table>
<script>
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
async function tick(){
  try{
    const l=await (await fetch("live.json")).json();
    if(l.run) document.getElementById("live").innerHTML=
      `<b>${esc(l.journey)}</b> <span class="${esc(l.status)}">${esc(l.status)}</span> · step ${esc(l.step)}`+
      ` · ${esc(l.text)}<br><span class="muted">screen ${esc(l.screen||"?")} · run ${esc(l.run)}</span>`+
      (l.message?`<br><span class="failed">${esc(l.message)}</span>`:"");
    const f=await (await fetch("failures.json")).json();
    document.getElementById("failures").innerHTML=f.slice(-15).reverse().map(x=>
      `<tr><td>${esc(x.id)}</td><td>${esc(x.journey)}</td><td>${esc(x.message)}</td></tr>`).join("");
    const p=await (await fetch("proposals.json")).json();
    document.getElementById("proposals").innerHTML=p.filter(x=>x.status==="proposed").map(x=>
      `<tr><td>${esc(x.id)}</td><td>${esc(x.summary)}</td></tr>`).join("");
  }catch(e){}
}
tick();setInterval(tick,1000);
</script></body></html>"""

_JSON = {"/live.json": ("live.json", {}), "/failures.json": ("failures.json", []),
         "/proposals.json": ("proposals.json", [])}


def make_handler(store: Store):
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
            path = self.path.split("?")[0]
            if path == "/":
                return self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            if path in _JSON:
                name, default = _JSON[path]
                return self._send(200, json.dumps(read_json(store.root / name, default)).encode(),
                                  "application/json")
            if path == "/history.json":
                return self._send(200, json.dumps(store.history()).encode(), "application/json")
            self._send(404, b"not found", "text/plain")

    return Handler


def serve(store: Store, port: int = 8765) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(store))


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

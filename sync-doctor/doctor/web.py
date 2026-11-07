"""Web del informe (solo lectura, más un botón para volver a comprobar)."""
import json
import logging
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .verify import VERDICTS

log = logging.getLogger("doctor.web")

PAGE = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sync Doctor</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--fg:#1d1f23;--mut:#6b7280;--line:#e3e5e8;
 --ok:#2f9e44;--fix:#1c7ed6;--cut:#e8590c;--dis:#f08c00;--err:#868e96}
@media (prefers-color-scheme:dark){:root{--bg:#141517;--card:#1f2023;--fg:#e9ecef;--mut:#9aa0a6;--line:#2e3034}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif}
main{max-width:1200px;margin:0 auto;padding:20px 16px}
h1{font-size:20px;margin:0 0 4px}.sub{color:var(--mut);margin:0 0 16px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px;cursor:pointer}
.card b{display:block;font-size:22px}.card.sel{outline:2px solid var(--fg)}
.card small{color:var(--mut)}
.bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px}
button{background:var(--card);color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:6px 10px;cursor:pointer}
.wrap{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;min-width:760px}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{font-size:12px;color:var(--mut);font-weight:600}
td.num{font-variant-numeric:tabular-nums;white-space:nowrap}
.pill{display:inline-block;padding:1px 8px;border-radius:10px;color:#fff;font-size:12px;font-weight:600;white-space:nowrap}
.v-ok{background:var(--ok)}.v-fixable{background:var(--fix)}.v-other_cut{background:var(--cut)}
.v-disagree{background:var(--dis)}.v-error,.v-unsupported,.v-pending,.v-gone{background:var(--err)}
.lv{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px}
.lv-red{background:#e03131}.lv-yellow{background:#f08c00}.lv-green{background:#2f9e44}
a{color:inherit}.muted{color:var(--mut);font-size:12px}
.tabs{display:flex;gap:4px;border-bottom:1px solid var(--line);margin-bottom:14px}
.tabs a{padding:8px 14px;text-decoration:none;color:var(--mut);border-bottom:2px solid transparent;margin-bottom:-1px}
.tabs a.on{color:var(--fg);border-bottom-color:var(--fg);font-weight:600}
</style></head><body><main>
<h1>Sync Doctor</h1>
<p class="sub" id="status">Cargando…</p>
<nav class="tabs" id="tabs"></nav>
<div class="cards" id="cards"></div>
<div class="bar"><button id="scan">Leer Bazarr ahora</button><button id="retry">Reintentar errores</button>
<label><input type="checkbox" id="controls" checked> Mostrar grupo de control (verdes)</label></div>
<div class="wrap"><table><thead><tr>
<th>Título</th><th>Bazarr</th><th>Veredicto</th><th>ffsubsync</th><th>alass</th><th>Comprobado</th><th></th>
</tr></thead><tbody id="rows"></tbody></table></div>
<p class="muted">ffsubsync y alass indican cuánto moverían el subtítulo <i>actual</i>. Cerca de 0 = ya está bien.
"Tramos" &gt; 1 significa que alass tuvo que cortar el sub en partes (escenas que no coinciden).</p>
</main><script>
const LABELS = __VERDICTS__;
const BAZARR = __BAZARR__;
let data = null, filter = null;
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"})[c]);
const sec = v => v == null ? "–" : (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(2) + " s";
const when = t => t ? new Date(t * 1000).toLocaleString() : "";
function eng(e){ if(!e) return "–"; let s = sec(e.median); if(e.segments > 1) s += ` · ${e.segments} tramos`; if(e.split_fraction) s += ` · ${Math.round(e.split_fraction*100)}% fuera`; return s; }
async function load(){
  data = await fetch("api/results").then(r => r.json());
  const st = data.state;
  document.getElementById("status").textContent =
    (st.current ? `Comprobando: ${st.current}. ` : "En espera. ") +
    (st.last_scan ? `Último escaneo: ${when(st.last_scan)}.` : "") +
    (st.last_scan_error ? ` Error leyendo Bazarr: ${st.last_scan_error}` : "");
  render();
}
const TABS = {all: ["Todo", null], peliculas: ["Películas", "movies"], series: ["Series", "episodes"]};
function currentTab(){
  const kinds = data.state.kinds || ["movies", "episodes"];
  const t = location.hash.slice(1);
  if (kinds.length < 2) return kinds[0] === "movies" ? "peliculas" : "series";
  return TABS[t] ? t : "all";
}
function render(){
  const showCtl = document.getElementById("controls").checked;
  const kinds = data.state.kinds || ["movies", "episodes"];
  const tab = currentTab();
  const visible = data.items.filter(i => showCtl || !i.is_control);
  const tabsEl = document.getElementById("tabs");
  if (kinds.length < 2) {
    tabsEl.innerHTML = `<a class="on">${TABS[tab][0]}</a><span class="muted" style="align-self:center;margin-left:8px">(solo se revisan ${TABS[tab][0].toLowerCase()}: MEDIA_TYPES)</span>`;
  } else {
    tabsEl.innerHTML = Object.entries(TABS).map(([k, [label, kind]]) => {
      const n = visible.filter(i => !kind || i.kind === kind).length;
      return `<a href="#${k}" class="${k === tab ? "on" : ""}">${label} (${n})</a>`;
    }).join("");
  }
  const tabKind = TABS[tab][1];
  const items = visible.filter(i => !tabKind || i.kind === tabKind);
  const counts = {};
  for(const i of items){ const v = i.verdict || "pending"; counts[v] = (counts[v]||0) + 1; }
  const order = ["pending","ok","fixable","other_cut","disagree","gone","error","unsupported"];
  document.getElementById("cards").innerHTML = order.filter(v => counts[v]).map(v =>
    `<div class="card ${filter===v?"sel":""}" data-v="${v}"><b>${counts[v]}</b><small>${esc(v==="pending"?"Pendientes":LABELS[v])}</small></div>`).join("");
  document.querySelectorAll(".card").forEach(c => c.onclick = () => { filter = filter === c.dataset.v ? null : c.dataset.v; render(); });
  const rank = {disagree:0, other_cut:1, fixable:2, error:3, pending:4, ok:5, gone:6, unsupported:7};
  const list = items.filter(i => !filter || (i.verdict || "pending") === filter)
    .sort((a,b) => (rank[a.verdict||"pending"] - rank[b.verdict||"pending"]) || Math.abs(b.bazarr_offset) - Math.abs(a.bazarr_offset));
  document.getElementById("rows").innerHTML = list.map(i => {
    const r = i.result || {}; const v = i.verdict || "pending";
    const href = i.kind === "movies" ? `${BAZARR}/movies/${i.media_id}` : `${BAZARR}/series/${i.series_id}`;
    return `<tr><td><a href="${esc(href)}" target="_blank">${esc(i.title)}</a>
      <div class="muted">${esc(i.language)}${i.is_control ? " · control" : ""}${i.note ? " · " + esc(i.note) : ""}${r.error ? " · " + esc(r.error) : ""}</div></td>
      <td class="num"><span class="lv lv-${esc(i.level)}"></span>${sec(i.bazarr_offset)}</td>
      <td><span class="pill v-${esc(v)}">${esc(v==="pending"?"Pendiente":LABELS[v])}</span></td>
      <td class="num">${eng(r.ffsubsync)}</td><td class="num">${eng(r.alass)}</td>
      <td class="muted">${when(i.checked_at)}</td>
      <td><button data-k="${esc(i.key)}">Repetir</button></td></tr>`;
  }).join("");
  document.querySelectorAll("button[data-k]").forEach(b => b.onclick = async () => {
    await fetch("api/recheck?key=" + encodeURIComponent(b.dataset.k), {method:"POST"}); load(); });
}
document.getElementById("controls").onchange = render;
window.addEventListener("hashchange", () => { filter = null; render(); });
document.getElementById("scan").onclick = async () => { await fetch("api/scan", {method:"POST"}); setTimeout(load, 1500); };
document.getElementById("retry").onclick = async () => { await fetch("api/recheck", {method:"POST"}); load(); };
load(); setInterval(load, 30000);
</script></body></html>"""


def make_handler(cfg, store, worker):
    page = (PAGE.replace("__VERDICTS__", json.dumps(VERDICTS, ensure_ascii=False))
                .replace("__BAZARR__", json.dumps(cfg.bazarr_url)))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            log.debug(fmt, *args)

        def _send(self, code, body, ctype):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = urllib.parse.urlparse(self.path).path
            if path in ("/", "/index.html"):
                self._send(200, page, "text/html; charset=utf-8")
            elif path == "/api/results":
                body = {"state": worker.state, "items": store.all()}
                self._send(200, json.dumps(body, ensure_ascii=False), "application/json; charset=utf-8")
            elif path == "/health":
                self._send(200, "ok", "text/plain")
            else:
                self._send(404, "not found", "text/plain")

        def do_POST(self):
            url = urllib.parse.urlparse(self.path)
            if url.path == "/api/scan":
                worker.wake.set()
            elif url.path == "/api/recheck":
                key = urllib.parse.parse_qs(url.query).get("key", [None])[0]
                store.requeue(key)
                worker.wake.set()
            else:
                return self._send(404, "not found", "text/plain")
            self._send(204, "", "text/plain")

    return Handler


def serve(cfg, store, worker):
    server = ThreadingHTTPServer(("0.0.0.0", cfg.port), make_handler(cfg, store, worker))
    log.info("Web del informe en el puerto %d", cfg.port)
    server.serve_forever()

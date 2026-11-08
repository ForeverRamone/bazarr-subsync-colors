"""Web del informe (solo lectura, más un botón para volver a comprobar)."""
import json
import logging
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .logbuf import BUFFER
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
button.primary{background:var(--fg);color:var(--bg);border-color:var(--fg)}
button:disabled{opacity:.55;cursor:default}
.status{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:0 0 16px;color:var(--mut)}
.dot{width:9px;height:9px;border-radius:50%;background:var(--err)}.dot.busy{background:var(--fix);animation:pulse 1.2s infinite}
@keyframes pulse{50%{opacity:.3}}
details.log{margin-top:16px;background:var(--card);border:1px solid var(--line);border-radius:10px}
details.log summary{padding:10px 12px;cursor:pointer;font-weight:600}
#log{margin:0;padding:8px 12px 12px;max-height:340px;overflow:auto;font:12px/1.5 ui-monospace,Consolas,monospace;white-space:pre-wrap;border-top:1px solid var(--line)}
#log .WARNING{color:var(--dis)}#log .ERROR{color:#e03131}
.head{display:flex;align-items:center;justify-content:space-between;gap:12px}
.acts{display:flex;flex-direction:column;gap:4px;align-items:stretch}
.acts button{white-space:nowrap}
button.warn{border-color:var(--cut);color:var(--cut)}button.fix{border-color:var(--fix);color:var(--fix)}
.note{color:var(--fix);font-size:12px;margin-top:2px}
.auto{display:inline-block;font-size:12px;padding:1px 8px;border-radius:10px;border:1px solid var(--line);color:var(--mut)}
.auto.on{border-color:var(--cut);color:var(--cut)}
dialog{border:1px solid var(--line);border-radius:12px;background:var(--card);color:var(--fg);padding:0;width:min(520px,calc(100vw - 32px))}
dialog::backdrop{background:rgba(0,0,0,.45)}
dialog form{padding:18px 20px}dialog h2{margin:0 0 12px;font-size:17px}
dialog fieldset{border:1px solid var(--line);border-radius:8px;margin:0 0 12px;padding:8px 12px}
dialog legend{font-weight:600;padding:0 4px}
dialog label{display:block;margin:6px 0}dialog input[type=text],dialog input[type=number]{background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:5px 8px;width:110px}
dialog .row{display:flex;justify-content:flex-end;gap:8px;margin-top:8px}
#setmsg{color:#e03131;font-size:13px;min-height:1em}
</style></head><body><main>
<div class="head"><h1>Sync Doctor</h1><div><span class="auto" id="autobadge"></span> <button id="openset">⚙ Ajustes</button></div></div>
<dialog id="settings"><form method="dialog" id="setform">
<h2>Ajustes</h2>
<fieldset><legend>Qué revisar</legend>
<label><input type="checkbox" name="k_movies"> Películas</label>
<label><input type="checkbox" name="k_episodes"> Series</label></fieldset>
<fieldset><legend>Cuándo</legend>
<label>Horario automático <input type="text" name="active_hours" placeholder="1-8"> <span class="muted">vacío = siempre</span></label>
<label>Verdes de control <input type="number" name="control_samples" min="0" max="50"></label></fieldset>
<fieldset><legend>Acciones automáticas después de comprobar</legend>
<label><input type="checkbox" name="a_other_cut"> <b>Otra versión</b> → descartar y buscar otro</label>
<label><input type="checkbox" name="a_disagree"> <b>Dudoso</b> → descartar y buscar otro</label>
<label><input type="checkbox" name="a_fixable"> <b>Corregible</b> → aplicar la corrección</label>
<label><input type="checkbox" name="a_gone"> <b>Ya no está</b> → pedir a Bazarr que busque sub</label>
<label>Máximo de acciones por título e idioma <input type="number" name="max_attempts" min="1" max="10"></label>
<p class="muted" style="margin:4px 0 0">"Descartar" = Bazarr borra el sub, lo mete en su lista negra y descarga el siguiente mejor. Tras cada acción, el sub nuevo se comprueba en cuanto Bazarr lo tenga.</p></fieldset>
<p id="setmsg"></p>
<div class="row"><button value="cancel" formnovalidate>Cancelar</button><button id="saveset" class="primary" value="default">Guardar</button></div>
</form></dialog>
<p class="status"><span class="dot" id="dot"></span><span id="status">Cargando…</span></p>
<nav class="tabs" id="tabs"></nav>
<div class="cards" id="cards"></div>
<div class="bar"><button id="runall" class="primary">Comprobar pendientes ahora</button><button id="stop">Detener</button>
<button id="scan">Leer Bazarr ahora</button><button id="retry">Reintentar errores</button>
<label><input type="checkbox" id="controls" checked> Mostrar grupo de control (verdes)</label></div>
<div class="wrap"><table><thead><tr>
<th>Título</th><th>Bazarr</th><th>Veredicto</th><th>ffsubsync</th><th>alass</th><th>Comprobado</th><th></th>
</tr></thead><tbody id="rows"></tbody></table></div>
<p class="muted">ffsubsync y alass indican cuánto moverían el subtítulo <i>actual</i>. Cerca de 0 = ya está bien.
"Tramos" &gt; 1 significa que alass tuvo que cortar el sub en partes (escenas que no coinciden).</p>
<details class="log" id="logbox" open><summary>Registro</summary><pre id="log"></pre></details>
</main><script>
const LABELS = __VERDICTS__;
const BAZARR = __BAZARR__;
let data = null, filter = null;
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"})[c]);
const sec = v => v == null ? "–" : (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(2) + " s";
const when = t => t ? new Date(t * 1000).toLocaleString() : "";
function eng(e){ if(!e) return "–"; let s = sec(e.median); if(e.segments > 1) s += ` · ${e.segments} tramos`; if(e.split_fraction) s += ` · ${Math.round(e.split_fraction*100)}% fuera`; return s; }
const busy = () => data && (data.state.current || data.state.queue.length || data.state.run_all);
async function load(){
  try { data = await fetch("api/results").then(r => r.json()); } catch(e) { return; }
  const st = data.state;
  const pending = data.items.filter(i => i.status === "pending").length;
  let txt;
  if (st.current) txt = `Comprobando: ${st.current}.`;
  else if (st.run_all) txt = "Procesando la cola…";
  else if (!st.in_active_hours && pending) txt = `En espera: ${pending} pendientes para el horario automático (${st.active_hours} h), o pulsa "Comprobar pendientes ahora".`;
  else txt = pending ? `${pending} pendientes.` : "Todo comprobado.";
  if (st.queue.length) txt += ` En cola a mano: ${st.queue.length}.`;
  if (st.watching && st.watching.length) txt += ` Esperando sub nuevo de Bazarr: ${st.watching.join(", ")}.`;
  if (st.last_scan) txt += ` Bazarr leído: ${when(st.last_scan)}.`;
  if (st.last_scan_error) txt += ` Error leyendo Bazarr: ${st.last_scan_error}`;
  document.getElementById("status").textContent = txt;
  document.getElementById("dot").className = "dot" + (busy() ? " busy" : "");
  document.getElementById("runall").disabled = !!st.run_all || !pending;
  document.getElementById("stop").disabled = !busy();
  render();
}
async function post(url){ await fetch(url, {method: "POST"}); await load(); pollLog(); }
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
    tabsEl.innerHTML = `<a class="on">${TABS[tab][0]}</a><span class="muted" style="align-self:center;margin-left:8px">(solo se revisan ${TABS[tab][0].toLowerCase()}; cámbialo en Ajustes)</span>`;
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
      <div class="muted">${esc(i.language)}${i.is_control ? " · control" : ""}${i.provider ? " · " + esc(i.provider) : ""}${i.note ? " · " + esc(i.note) : ""}${r.error ? " · " + esc(r.error) : ""}</div>
      ${i.action_note ? `<div class="note">${esc(i.action_note)}</div>` : ""}</td>
      <td class="num"><span class="lv lv-${esc(i.level)}"></span>${sec(i.bazarr_offset)}</td>
      <td><span class="pill v-${esc(v)}">${esc(v==="pending"?"Pendiente":LABELS[v])}</span></td>
      <td class="num">${eng(r.ffsubsync)}</td><td class="num">${eng(r.alass)}</td>
      <td class="muted">${when(i.checked_at)}</td>
      <td><div class="acts">${actionButtons(i)}${rowButton(i)}</div></td></tr>`;
  }).join("");
  document.querySelectorAll("button[data-k]").forEach(b => b.onclick = () => {
    b.disabled = true; b.textContent = "En cola…";
    post("api/run?key=" + encodeURIComponent(b.dataset.k));
  });
  document.querySelectorAll("button[data-act]").forEach(b => b.onclick = () => act(b));
}
function rowButton(i){
  const st = data.state;
  if (st.current_key === i.key) return `<button disabled>Comprobando…</button>`;
  if (st.queue.includes(i.key)) return `<button disabled>En cola…</button>`;
  if (i.status === "acted") return "";
  return `<button data-k="${esc(i.key)}">${i.status === "done" ? "Volver a comprobar" : "Comprobar ahora"}</button>`;
}
function actionButtons(i){
  if (i.status !== "done" || data.state.current_key === i.key) return "";
  const r = i.result || {}; const v = i.verdict; const k = esc(i.key);
  const out = [];
  if (v === "fixable" && r.ffsubsync && r.alass)
    out.push(`<button class="fix" data-act="fix" data-key="${k}">Aplicar ${sec((r.ffsubsync.median + r.alass.median) / 2)}</button>`);
  if (["other_cut","disagree","fixable","ok","error"].includes(v) && i.sub_path)
    out.push(`<button class="warn" data-act="replace" data-key="${k}" data-title="${esc(i.title)}">Descartar y buscar otro</button>`);
  if (v === "gone")
    out.push(`<button data-act="search" data-key="${k}">Buscar sub</button>`);
  return out.join("");
}
async function act(b){
  const what = b.dataset.act;
  if (what === "replace" && !confirm(`"${b.dataset.title}": Bazarr borrará este subtítulo, lo meterá en su lista negra y buscará otro. ¿Seguro?`)) return;
  b.disabled = true; b.textContent = "Enviando…";
  const res = await fetch(`api/action?do=${what}&key=${encodeURIComponent(b.dataset.key)}`, {method: "POST"}).then(r => r.json()).catch(() => ({ok: false, message: "sin respuesta"}));
  if (!res.ok) alert("No se pudo: " + res.message);
  await load(); pollLog();
}
// Ajustes
const AUTO = {other_cut: "replace", disagree: "replace", fixable: "fix", gone: "search"};
async function openSettings(){
  const s = await fetch("api/settings").then(r => r.json());
  const f = document.getElementById("setform");
  f.k_movies.checked = s.kinds.includes("movies"); f.k_episodes.checked = s.kinds.includes("episodes");
  f.active_hours.value = s.active_hours || ""; f.control_samples.value = s.control_samples; f.max_attempts.value = s.max_attempts;
  for (const v of Object.keys(AUTO)) f["a_" + v].checked = !!s.auto_actions[v];
  document.getElementById("setmsg").textContent = "";
  document.getElementById("settings").showModal();
}
async function saveSettings(ev){
  ev.preventDefault();
  const f = document.getElementById("setform");
  const body = {
    kinds: [f.k_movies.checked && "movies", f.k_episodes.checked && "episodes"].filter(Boolean),
    active_hours: f.active_hours.value.trim(), control_samples: +f.control_samples.value, max_attempts: +f.max_attempts.value,
    auto_actions: Object.fromEntries(Object.entries(AUTO).filter(([v]) => f["a_" + v].checked)),
  };
  const res = await fetch("api/settings", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)}).then(r => r.json());
  if (!res.ok) { document.getElementById("setmsg").textContent = res.message; return; }
  document.getElementById("settings").close();
  refreshAutoBadge(); await load(); pollLog();
}
async function refreshAutoBadge(){
  const s = await fetch("api/settings").then(r => r.json()).catch(() => null);
  if (!s) return;
  const n = Object.keys(s.auto_actions).length;
  const el = document.getElementById("autobadge");
  el.textContent = n ? `Acciones automáticas: ${n} activas` : "Acciones automáticas: no";
  el.className = "auto" + (n ? " on" : "");
}
document.getElementById("openset").onclick = openSettings;
document.getElementById("saveset").onclick = saveSettings;
refreshAutoBadge();
// Registro
let logAfter = 0, logBusy = false;
async function pollLog(){
  if (!document.getElementById("logbox").open || logBusy) return;
  logBusy = true;
  let lines;
  try { lines = (await fetch("api/log?after=" + logAfter).then(r => r.json())).lines; }
  catch(e) { return; } finally { logBusy = false; }
  lines = lines.filter(l => l.n > logAfter);
  if (!lines.length) return;
  const pre = document.getElementById("log");
  const atBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 30;
  pre.insertAdjacentHTML("beforeend", lines.map(l => `<span class="${esc(l.level)}">${esc(l.text)}</span>\n`).join(""));
  logAfter = lines[lines.length - 1].n;
  if (atBottom) pre.scrollTop = pre.scrollHeight;
}
document.getElementById("controls").onchange = render;
window.addEventListener("hashchange", () => { filter = null; render(); });
document.getElementById("runall").onclick = () => post("api/run");
document.getElementById("stop").onclick = () => post("api/stop");
document.getElementById("scan").onclick = async () => { await post("api/scan"); setTimeout(load, 2000); };
document.getElementById("retry").onclick = () => post("api/retry-errors");
document.getElementById("logbox").ontoggle = pollLog;
(async function loop(){ await load(); await pollLog(); setTimeout(loop, busy() ? 3000 : 15000); })();
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
                body = {"state": worker.public_state(), "items": store.all()}
                self._send(200, json.dumps(body, ensure_ascii=False), "application/json; charset=utf-8")
            elif path == "/api/log":
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                try:
                    after = int(q.get("after", ["0"])[0])
                except ValueError:
                    after = 0
                self._send(200, json.dumps({"lines": BUFFER.since(after)}, ensure_ascii=False),
                           "application/json; charset=utf-8")
            elif path == "/api/settings":
                self._send(200, json.dumps(cfg.editable(), ensure_ascii=False), "application/json; charset=utf-8")
            elif path == "/health":
                self._send(200, "ok", "text/plain")
            else:
                self._send(404, "not found", "text/plain")

        def do_POST(self):
            url = urllib.parse.urlparse(self.path)
            if url.path == "/api/scan":
                worker.request_scan()
            elif url.path == "/api/run":
                key = urllib.parse.parse_qs(url.query).get("key", [None])[0]
                if key:
                    if not worker.request_one(key):
                        return self._send(404, "no existe", "text/plain")
                else:
                    worker.request_all()
            elif url.path == "/api/stop":
                worker.stop()
            elif url.path == "/api/retry-errors":
                for item in store.all():
                    if item["verdict"] == "error":
                        worker.request_one(item["key"])
            elif url.path == "/api/action":
                q = urllib.parse.parse_qs(url.query)
                ok, msg = worker.perform(q.get("key", [""])[0], q.get("do", [""])[0])
                return self._send(200 if ok else 409, json.dumps({"ok": ok, "message": msg}, ensure_ascii=False),
                                  "application/json; charset=utf-8")
            elif url.path == "/api/settings":
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    values = json.loads(self.rfile.read(length) or b"{}")
                    clean = cfg.apply(values)
                except Exception as e:  # noqa: BLE001
                    return self._send(400, json.dumps({"ok": False, "message": str(e)}, ensure_ascii=False),
                                      "application/json; charset=utf-8")
                store.save_settings(clean)
                log.info("Ajustes guardados: %s", ", ".join(f"{k}={v}" for k, v in clean.items()))
                worker.request_scan()
                return self._send(200, json.dumps({"ok": True, "settings": cfg.editable()}, ensure_ascii=False),
                                  "application/json; charset=utf-8")
            else:
                return self._send(404, "not found", "text/plain")
            self._send(204, "", "text/plain")

    return Handler


def serve(cfg, store, worker):
    server = ThreadingHTTPServer(("0.0.0.0", cfg.port), make_handler(cfg, store, worker))
    log.info("Web del informe en el puerto %d", cfg.port)
    server.serve_forever()

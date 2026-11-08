// ==UserScript==
// @name         Bazarr – colores de sincronización
// @namespace    fixforbazarr
// @version      1.1.0
// @description  Muestra el desplazamiento de cada sincronización en el historial de Bazarr, colorea las filas, añade un panel con los subtítulos a revisar y, si usas Sync Doctor, su veredicto.
// @homepageURL  https://github.com/ForeverRamone/bazarr-subsync-colors
// @updateURL    https://raw.githubusercontent.com/ForeverRamone/bazarr-subsync-colors/main/bazarr-subsync-colors.user.js
// @downloadURL  https://raw.githubusercontent.com/ForeverRamone/bazarr-subsync-colors/main/bazarr-subsync-colors.user.js
// @match        http://192.168.0.60:6767/*
// @grant        none
// @run-at       document-idle
// ==/UserScript==

(function () {
  "use strict";

  // ---------------------------------------------------------------------------
  // Ajustes: cambia aquí los umbrales (en segundos, valor absoluto del offset).
  // ---------------------------------------------------------------------------
  const CONFIG = {
    greenMax: 1, // |offset| <= 1 s   → verde (sincronizado bien)
    yellowMax: 10, // |offset| <= 10 s → amarillo (revisar si quieres)
    //                 |offset| >  10 s → rojo (casi seguro que va mal)
    // Si el factor de framerate no es 1.00, se marca como mínimo en amarillo.
    framerateCountsAsWarning: true,
    // Dirección de Sync Doctor (vacío = no mostrar sus veredictos).
    doctorUrl: "http://192.168.0.60:8687",
    // Si Sync Doctor ya comprobó la fila, colorearla según su veredicto (no por el offset).
    doctorColors: true,
  };

  const SYNC_RE =
    /offset of\s+(-?\d+(?:\.\d+)?)\s+seconds and a framerate scale factor of\s+(-?\d+(?:\.\d+)?)/i;
  const REPLACING_ACTIONS = new Set([0, 1, 2, 3, 4]); // borrado, descarga, manual, mejora, subida

  // ---------------------------------------------------------------------------
  // Sync Doctor: veredictos (se leen de su API cada minuto)
  // ---------------------------------------------------------------------------
  const DOCTOR = { map: new Map(), ok: false, error: null };
  const DOCTOR_LABELS = {
    ok: ["Bien", "green"], fixable: ["Corregible", "yellow"], other_cut: ["Otra versión", "red"],
    disagree: ["Dudoso", "yellow"], gone: ["Ya no está", "gray"], error: ["Error", "gray"],
    unsupported: ["No soportado", "gray"],
  };

  async function loadDoctor() {
    if (!CONFIG.doctorUrl) return;
    try {
      const res = await fetch(CONFIG.doctorUrl.replace(/\/$/, "") + "/api/results");
      const json = await res.json();
      const map = new Map();
      for (const it of json.items || []) map.set(it.key, it);
      DOCTOR.map = map;
      DOCTOR.ok = true;
      DOCTOR.error = null;
      scheduleDecorate();
    } catch (e) {
      DOCTOR.ok = false;
      DOCTOR.error = String(e);
    }
  }

  // Sync Doctor identifica cada sincronización por "ruta del sub|fecha".
  function doctorFor(row) {
    if (!row || !DOCTOR.map.size) return null;
    const path = row.subtitlesPath || row.subtitles_path;
    const ts = row.parsedTimestamp || row.parsed_timestamp;
    return path && ts ? DOCTOR.map.get(`${path}|${ts}`) || null : null;
  }

  function doctorLabel(it) {
    if (it.manual_ok) return ["Bien ✓ a mano", "green"];
    if (it.status === "acted") return ["Acción en curso", "blue"];
    if (!it.verdict) return ["Pendiente", "gray"];
    return DOCTOR_LABELS[it.verdict] || [it.verdict, "gray"];
  }

  function doctorTooltip(it) {
    const r = it.result || {};
    const f = (e) => (e ? `${e.median > 0 ? "+" : ""}${e.median.toFixed(2)} s${e.segments > 1 ? ` (${e.segments} tramos)` : ""}` : "–");
    let t = `Sync Doctor: ${doctorLabel(it)[0]}`;
    if (r.ffsubsync || r.alass) t += ` · ffsubsync ${f(r.ffsubsync)} · alass ${f(r.alass)}`;
    if (it.action_note) t += ` · ${it.action_note}`;
    return t + " · clic para abrirlo en Sync Doctor";
  }

  function doctorLink(title) {
    return CONFIG.doctorUrl.replace(/\/$/, "") + "/?q=" + encodeURIComponent(title || "");
  }

  // ---------------------------------------------------------------------------
  // Utilidades
  // ---------------------------------------------------------------------------
  function parseSync(description) {
    if (!description) return null;
    const m = SYNC_RE.exec(description);
    if (!m) return null;
    const offset = parseFloat(m[1]);
    const framerate = parseFloat(m[2]);
    if (Number.isNaN(offset)) return null;
    return { offset, framerate, level: levelFor(offset, framerate) };
  }

  function levelFor(offset, framerate) {
    const abs = Math.abs(offset);
    let level = abs <= CONFIG.greenMax ? "green" : abs <= CONFIG.yellowMax ? "yellow" : "red";
    const fpsChanged = !Number.isNaN(framerate) && Math.abs(framerate - 1) >= 0.005;
    if (CONFIG.framerateCountsAsWarning && fpsChanged && level === "green") level = "yellow";
    return level;
  }

  function formatOffset(offset) {
    const sign = offset > 0 ? "+" : offset < 0 ? "−" : "";
    const abs = Math.abs(offset);
    const txt = abs < 10 ? abs.toFixed(2) : abs.toFixed(1);
    return `${sign}${txt} s`;
  }

  function badgeText(sync) {
    let t = formatOffset(sync.offset);
    if (!Number.isNaN(sync.framerate) && Math.abs(sync.framerate - 1) >= 0.005) {
      t += ` · fps ×${sync.framerate.toFixed(2)}`;
    }
    return t;
  }

  // Lee el registro de datos que React guarda en una celda de la tabla.
  // Las celdas de Bazarr se pintan con TanStack Table: el componente de cada
  // celda recibe { row: { original } }, que es la fila completa del historial.
  // React reutiliza las filas al cambiar de página y guarda dos copias de cada
  // nodo (la actual y la anterior). Se miran todas las candidatas y se elige la
  // que coincide con lo que se ve en la fila (título y fecha).
  function getRowData(tr) {
    const text = tr.textContent || "";
    let best = null;
    let bestScore = -1;
    for (const td of tr.children) {
      for (const cand of rowCandidates(td)) {
        const r = cand.row;
        let score = cand.bonus;
        const title = r.title || r.seriesTitle;
        if (title && text.includes(title)) score += 4;
        if (r.timestamp && text.includes(r.timestamp)) score += 2;
        if (r.episodeTitle && text.includes(r.episodeTitle)) score += 1;
        if (score > bestScore) {
          best = r;
          bestScore = score;
        }
      }
      if (bestScore >= 4) break;
    }
    return best;
  }

  function rowCandidates(td) {
    const out = [];
    for (const k of Object.keys(td)) {
      if (k.startsWith("__reactFiber$")) {
        const f = td[k];
        for (const fiber of [f, f && f.alternate]) {
          if (!fiber) continue;
          const row = findRowInFiber(fiber.child, 0);
          if (row) out.push({ row, bonus: isCurrentFiber(fiber) ? 1 : 0 });
        }
      } else if (k.startsWith("__reactProps$")) {
        const row = findRowInElement(td[k].children, 0);
        if (row) out.push({ row, bonus: 0 });
      }
    }
    return out;
  }

  function isCurrentFiber(fiber) {
    let x = fiber;
    for (let i = 0; i < 200 && x.return; i++) x = x.return;
    return !!(x.stateNode && x.stateNode.current === x);
  }

  function findRowInFiber(fiber, depth) {
    while (fiber && depth < 6) {
      const props = fiber.memoizedProps;
      if (props && props.row && props.row.original && typeof props.row.original === "object") {
        return props.row.original;
      }
      const inChild = findRowInFiber(fiber.child, depth + 1);
      if (inChild) return inChild;
      fiber = fiber.sibling;
    }
    return null;
  }

  function findRowInElement(node, depth) {
    if (!node || depth > 5) return null;
    if (Array.isArray(node)) {
      for (const n of node) {
        const f = findRowInElement(n, depth + 1);
        if (f) return f;
      }
      return null;
    }
    const props = node.props;
    if (!props) return null;
    if (props.row && props.row.original && typeof props.row.original === "object") {
      return props.row.original;
    }
    return findRowInElement(props.children, depth + 1);
  }

  // ---------------------------------------------------------------------------
  // Estilos
  // ---------------------------------------------------------------------------
  const css = `
    tr[data-subsync] > td:first-child { box-shadow: inset 4px 0 0 var(--subsync-color); }
    tr[data-subsync] > td { background-color: var(--subsync-bg) !important; }
    tr[data-subsync="green"]  { --subsync-color: #2f9e44; --subsync-bg: rgba(47,158,68,.10); }
    tr[data-subsync="yellow"] { --subsync-color: #f08c00; --subsync-bg: rgba(240,140,0,.14); }
    tr[data-subsync="red"]    { --subsync-color: #e03131; --subsync-bg: rgba(224,49,49,.18); }
    td[data-subsync-badge] { white-space: nowrap; }
    td[data-subsync-badge] > * { display: inline-block; vertical-align: middle; }
    td[data-subsync-badge]::after {
      content: attr(data-subsync-badge);
      display: inline-block; vertical-align: middle; margin-left: 6px;
      padding: 1px 7px; border-radius: 10px; font-size: 12px; font-weight: 600;
      color: #fff; background: var(--subsync-color);
      font-variant-numeric: tabular-nums;
    }

    a.sd-badge {
      display: inline-block; margin-left: 8px; padding: 0 7px; border-radius: 10px; vertical-align: middle;
      font-size: 11px; font-weight: 600; line-height: 18px; text-decoration: none; white-space: nowrap;
      color: var(--sd-c); border: 1.5px solid var(--sd-c); background: transparent;
    }
    a.sd-badge::before { content: "⚕ "; }
    a.sd-badge:hover { background: var(--sd-c); color: #fff; }
    a.sd-badge[data-c="green"] { --sd-c: #2f9e44; } a.sd-badge[data-c="yellow"] { --sd-c: #e67700; }
    a.sd-badge[data-c="red"] { --sd-c: #e03131; } a.sd-badge[data-c="gray"] { --sd-c: #868e96; }
    a.sd-badge[data-c="blue"] { --sd-c: #1971c2; }
    #subsync-panel .ss-doc { font-size: 11px; font-weight: 600; white-space: nowrap; margin-left: 6px; }
    #subsync-panel .ss-doc[data-c="green"] { color: #51cf66; } #subsync-panel .ss-doc[data-c="yellow"] { color: #fcc419; }
    #subsync-panel .ss-doc[data-c="red"] { color: #ff6b6b; } #subsync-panel .ss-doc[data-c="gray"], #subsync-panel .ss-doc[data-c="blue"] { color: #adb5bd; }
    #subsync-fab {
      position: fixed; right: 18px; bottom: 18px; z-index: 9999;
      border: 0; border-radius: 22px; padding: 9px 14px; cursor: pointer;
      font: 600 13px system-ui, sans-serif; color: #fff; background: #5f3dc4;
      box-shadow: 0 2px 10px rgba(0,0,0,.35);
    }
    #subsync-fab:hover { background: #7048e8; }
    #subsync-panel {
      position: fixed; right: 18px; bottom: 66px; z-index: 9999;
      width: min(560px, calc(100vw - 36px)); max-height: 70vh; display: flex; flex-direction: column;
      background: #1f1f23; color: #e9ecef; border: 1px solid #3a3a40; border-radius: 10px;
      box-shadow: 0 6px 28px rgba(0,0,0,.5); font: 13px system-ui, sans-serif;
    }
    #subsync-panel[hidden] { display: none; }
    #subsync-panel header { display: flex; align-items: center; gap: 8px; padding: 10px 12px; border-bottom: 1px solid #3a3a40; }
    #subsync-panel header b { flex: 1; font-size: 14px; }
    #subsync-panel header button { background: none; border: 0; color: #adb5bd; cursor: pointer; font-size: 16px; }
    #subsync-panel .ss-filters { display: flex; flex-wrap: wrap; gap: 12px; padding: 8px 12px; border-bottom: 1px solid #3a3a40; }
    #subsync-panel .ss-filters label { cursor: pointer; user-select: none; }
    #subsync-panel .ss-body { overflow: auto; padding: 4px 0; }
    #subsync-panel .ss-msg { padding: 14px 12px; color: #adb5bd; }
    #subsync-panel .ss-item { display: flex; gap: 10px; align-items: center; padding: 6px 12px; border-left: 4px solid var(--c); }
    #subsync-panel .ss-item:hover { background: #2a2a30; }
    #subsync-panel .ss-item a { color: #e9ecef; text-decoration: none; flex: 1; min-width: 0; }
    #subsync-panel .ss-item a:hover { text-decoration: underline; }
    #subsync-panel .ss-item small { display: block; color: #868e96; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    #subsync-panel .ss-off { font-weight: 700; color: var(--c); white-space: nowrap; font-variant-numeric: tabular-nums; text-align: right; }
    #subsync-panel .ss-item.green { --c: #40c057; } #subsync-panel .ss-item.yellow { --c: #fab005; } #subsync-panel .ss-item.red { --c: #ff6b6b; }
  `;

  function addStyles() {
    const style = document.createElement("style");
    style.id = "subsync-style";
    style.textContent = css;
    document.head.appendChild(style);
  }

  // ---------------------------------------------------------------------------
  // Tablas: colorear filas y poner la etiqueta en la columna Info
  // ---------------------------------------------------------------------------
  function decorateTables() {
    for (const tr of document.querySelectorAll("table tbody tr")) {
      const data = getRowData(tr);
      const sync = data ? parseSync(data.description) : null;
      const infoTd = sync ? findInfoCell(tr) : null;

      const doc = sync ? doctorFor(data) : null;
      decorateDoctor(tr, data, doc);

      if (!sync) {
        if (tr.hasAttribute("data-subsync")) tr.removeAttribute("data-subsync");
        for (const td of tr.querySelectorAll("td[data-subsync-badge]")) td.removeAttribute("data-subsync-badge");
        continue;
      }
      let level = sync.level;
      if (doc && CONFIG.doctorColors) {
        const c = doctorLabel(doc)[1];
        if (c === "green" || c === "yellow" || c === "red") level = c;
      }
      if (tr.getAttribute("data-subsync") !== level) tr.setAttribute("data-subsync", level);
      const text = badgeText(sync);
      for (const td of tr.querySelectorAll("td[data-subsync-badge]")) {
        if (td !== infoTd) td.removeAttribute("data-subsync-badge");
      }
      if (infoTd && infoTd.getAttribute("data-subsync-badge") !== text) {
        infoTd.setAttribute("data-subsync-badge", text);
      }
    }
  }

  // Etiqueta de Sync Doctor junto al título (enlace a su web, filtrada por ese título).
  function decorateDoctor(tr, data, doc) {
    let a = tr.querySelector("a.sd-badge");
    if (!doc) {
      if (a) a.remove();
      return;
    }
    const nameTd = findCell(tr, "name") || tr.children[1];
    if (!nameTd) return;
    const [label, color] = doctorLabel(doc);
    if (!a || a.parentElement !== nameTd) {
      if (a) a.remove();
      a = document.createElement("a");
      a.className = "sd-badge";
      a.target = "_blank";
      a.rel = "noopener";
      nameTd.appendChild(a);
    }
    const href = doctorLink(doc.title || data.title);
    if (a.textContent !== label) a.textContent = label;
    if (a.dataset.c !== color) a.dataset.c = color;
    if (a.getAttribute("href") !== href) a.setAttribute("href", href);
    const tip = doctorTooltip(doc);
    if (a.title !== tip) a.title = tip;
  }

  function findCell(tr, header) {
    const table = tr.closest("table");
    if (!table) return null;
    const ths = [...table.querySelectorAll("thead th")];
    const idx = ths.findIndex((th) => th.textContent.trim().toLowerCase() === header);
    return idx >= 0 ? tr.children[idx] || null : null;
  }

  // La columna "Info" se localiza por su cabecera; si no, la celda con el icono de info.
  function findInfoCell(tr) {
    const table = tr.closest("table");
    if (table) {
      const ths = [...table.querySelectorAll("thead th")];
      const idx = ths.findIndex((th) => th.textContent.trim().toLowerCase() === "info");
      if (idx >= 0 && tr.children[idx]) return tr.children[idx];
    }
    const icon = tr.querySelector('svg[data-icon="circle-info"], svg[data-icon="info-circle"]');
    return icon ? icon.closest("td") : null;
  }

  let scheduled = false;
  function scheduleDecorate() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => {
      scheduled = false;
      try {
        decorateTables();
      } catch (e) {
        console.warn("[subsync-colors]", e);
      }
    });
  }

  // ---------------------------------------------------------------------------
  // Panel "Subs a revisar": recorre todo el historial por la API
  // ---------------------------------------------------------------------------
  const env = () => window.Bazarr || {};

  // Si se inicia sesión sin recargar, window.Bazarr no trae la clave: la
  // recogemos de las peticiones que hace la propia web de Bazarr.
  let capturedKey = null;
  const origSetHeader = XMLHttpRequest.prototype.setRequestHeader;
  XMLHttpRequest.prototype.setRequestHeader = function (name, value) {
    if (/^x-api-key$/i.test(name) && value && value !== "AUTH_NEEDED") capturedKey = value;
    return origSetHeader.apply(this, arguments);
  };

  async function fetchHistory(kind) {
    const { baseUrl = "" } = env();
    const apiKey = env().apiKey || capturedKey;
    const res = await fetch(`${baseUrl}/api/${kind}/history?start=0&length=-1`, {
      headers: { "X-API-KEY": apiKey || "" },
      credentials: "same-origin",
    });
    if (!res.ok) throw new Error(`${kind}: HTTP ${res.status}`);
    const json = await res.json();
    return json.data || [];
  }

  // Para cada archivo de subtítulos se queda con su última sincronización, y
  // la descarta si después se borró o se descargó otro sub en la misma ruta.
  // Ojo: Bazarr registra la descarga en el mismo segundo que la sincronización
  // pero justo después, así que una descarga con la misma fecha y ruta es parte
  // de la misma operación y no invalida la sincronización.
  function latestSyncs(rows, kind) {
    const done = new Set();
    const newer = new Map(); // ruta → eventos más recientes que la invalidan
    const out = [];
    for (const r of rows) {
      const path = r.subtitles_path || r.subtitlesPath;
      const videoId = kind === "movies" ? r.radarrId : r.sonarrEpisodeId;
      const lang = r.language ? `${r.language.code2}${r.language.forced ? ":f" : ""}${r.language.hi ? ":hi" : ""}` : "";
      const key = path || `${videoId}|${lang}`;
      if (done.has(key)) continue;
      const ts = r.parsed_timestamp || r.parsedTimestamp || r.timestamp;
      const sync = parseSync(r.description);
      if (sync) {
        done.add(key);
        const evs = newer.get(key) || [];
        const stale = evs.some((e) => e.action === 0 || e.ts !== ts);
        if (!stale) out.push({ kind, row: r, sync });
      } else if (REPLACING_ACTIONS.has(r.action)) {
        if (!newer.has(key)) newer.set(key, []);
        newer.get(key).push({ action: r.action, ts });
      }
    }
    return out;
  }

  function itemHtml({ kind, row, sync }) {
    const { baseUrl = "" } = env();
    const href =
      kind === "movies" ? `${baseUrl}/movies/${row.radarrId}` : `${baseUrl}/series/${row.sonarrSeriesId}`;
    const title =
      kind === "movies"
        ? row.title
        : `${row.seriesTitle} · ${row.episode_number || row.episodeNumber || ""} ${row.episodeTitle || ""}`;
    const lang = row.language ? row.language.name || row.language.code2 : "";
    const when = row.timestamp || "";
    const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
    return `<div class="ss-item ${sync.level}">
        <a href="${esc(href)}">${esc(title)}<small>${esc(lang)} · ${esc(when)}</small></a>
        <span class="ss-off">${esc(badgeText(sync))}</span>${doctorSpan(row)}
      </div>`;
  }

  function doctorSpan(row) {
    const doc = doctorFor(row);
    if (!doc) return "";
    const [label, color] = doctorLabel(doc);
    return `<span class="ss-doc" data-c="${color}" title="${doctorTooltip(doc).replace(/"/g, "&quot;")}">⚕ ${label}</span>`;
  }

  let panelItems = null;

  function renderPanelBody(panel) {
    const body = panel.querySelector(".ss-body");
    if (!panelItems) return;
    const want = new Set(
      [...panel.querySelectorAll(".ss-filters input:checked")].map((i) => i.value),
    );
    const list = panelItems.filter((it) => want.has(it.sync.level) && want.has(it.kind));
    if (!list.length) {
      body.innerHTML = `<div class="ss-msg">Nada que mostrar con estos filtros.</div>`;
      return;
    }
    body.innerHTML = list.map(itemHtml).join("");
  }

  async function loadPanel(panel) {
    const body = panel.querySelector(".ss-body");
    body.innerHTML = `<div class="ss-msg">Leyendo el historial…</div>`;
    try {
      const [movies, episodes] = await Promise.all([fetchHistory("movies"), fetchHistory("episodes")]);
      const order = { red: 0, yellow: 1, green: 2 };
      panelItems = [...latestSyncs(movies, "movies"), ...latestSyncs(episodes, "episodes")].sort(
        (a, b) => order[a.sync.level] - order[b.sync.level] || Math.abs(b.sync.offset) - Math.abs(a.sync.offset),
      );
      const counts = { red: 0, yellow: 0, green: 0 };
      panelItems.forEach((it) => counts[it.sync.level]++);
      for (const lvl of Object.keys(counts)) {
        panel.querySelector(`.ss-count-${lvl}`).textContent = counts[lvl];
      }
      renderPanelBody(panel);
    } catch (e) {
      body.innerHTML = `<div class="ss-msg">No he podido leer el historial (${e.message}). ¿Has iniciado sesión en Bazarr?</div>`;
    }
  }

  function buildPanel() {
    const panel = document.createElement("div");
    panel.id = "subsync-panel";
    panel.hidden = true;
    panel.innerHTML = `
      <header><b>Sincronizaciones a revisar</b>
        <button class="ss-reload" title="Recargar">⟳</button>
        <button class="ss-close" title="Cerrar">✕</button></header>
      <div class="ss-filters">
        <label><input type="checkbox" value="red" checked> 🔴 Rojo (<span class="ss-count-red">·</span>)</label>
        <label><input type="checkbox" value="yellow" checked> 🟡 Amarillo (<span class="ss-count-yellow">·</span>)</label>
        <label><input type="checkbox" value="green"> 🟢 Verde (<span class="ss-count-green">·</span>)</label>
        <label><input type="checkbox" value="movies" checked> Películas</label>
        <label><input type="checkbox" value="episodes" checked> Series</label>
      </div>
      <div class="ss-body"></div>`;
    panel.querySelector(".ss-close").onclick = () => (panel.hidden = true);
    panel.querySelector(".ss-reload").onclick = () => loadPanel(panel);
    panel.querySelector(".ss-filters").onchange = () => renderPanelBody(panel);
    panel.addEventListener("click", (e) => {
      // Navegación dentro de la SPA sin recargar la página.
      const a = e.target.closest("a");
      if (!a || e.ctrlKey || e.metaKey || e.button !== 0) return;
      e.preventDefault();
      history.pushState({}, "", a.getAttribute("href"));
      window.dispatchEvent(new PopStateEvent("popstate"));
    });

    const fab = document.createElement("button");
    fab.id = "subsync-fab";
    fab.textContent = "⏱ Syncs";
    fab.title = "Subtítulos cuya sincronización conviene revisar";
    fab.onclick = () => {
      panel.hidden = !panel.hidden;
      if (!panel.hidden && !panelItems) loadPanel(panel);
    };

    document.body.append(panel, fab);
  }

  // ---------------------------------------------------------------------------
  // Arranque
  // ---------------------------------------------------------------------------
  function start() {
    if (!window.Bazarr) return; // no es una página de Bazarr
    addStyles();
    buildPanel();
    loadDoctor();
    setInterval(loadDoctor, 60000);
    new MutationObserver(scheduleDecorate).observe(document.body, {
      childList: true,
      subtree: true,
      characterData: true,
    });
    scheduleDecorate();
  }

  start();
})();

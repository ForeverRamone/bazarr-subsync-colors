"""Bucle principal: lee el historial de Bazarr y comprueba las sincronizaciones sospechosas.

Trabaja solo en ACTIVE_HOURS, salvo lo que se pida a mano desde la web:
- "Comprobar ahora" en una fila: se procesa en cuanto termine lo que esté en marcha.
- "Comprobar pendientes ahora": procesa toda la cola, sin mirar la hora.
- "Detener": corta lo que esté en marcha y vacía las peticiones manuales.
"""
import json
import logging
import os
import threading
import time
from collections import deque
from datetime import datetime

from .bazarr import Bazarr, latest_syncs, level_for
from .store import media_key
from .verify import CANCEL, VERDICTS, Cancelled, verify

log = logging.getLogger("doctor.worker")

WATCH_MINUTES = 30  # cuánto se espera a que Bazarr descargue otro sub tras una acción


def item_result(item):
    r = item.get("result")
    if r is None and item.get("result_json"):
        r = json.loads(item["result_json"])
    return r or {}


class Worker:
    def __init__(self, cfg, store):
        self.cfg = cfg
        self.store = store
        self.bazarr = Bazarr(cfg.bazarr_url, cfg.bazarr_api_key)
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.manual = deque()  # claves pedidas a mano, en orden
        self.run_all = False
        self.next_scan = 0
        self.watch = {}  # media_key → acción en curso esperando sub nuevo de Bazarr
        self.scan_lock = threading.Lock()
        self.state = {"current": None, "current_key": None, "last_scan": None, "last_scan_error": None,
                      "last_scan_summary": None}

    # ------------------------------------------------------- peticiones de la web
    def request_scan(self, delay=0):
        self.next_scan = min(self.next_scan, time.time() + delay)
        self.wake.set()

    def scan_now(self):
        """"Leer Bazarr ahora": se lee en el acto, aunque haya una comprobación en marcha."""
        try:
            self.scan(manual=True)
        except Exception as e:  # noqa: BLE001
            log.warning("No he podido leer Bazarr: %s", e)
            self.state["last_scan_error"] = str(e)
            self.state["last_scan_summary"] = f"Error leyendo Bazarr: {e}"
        return self.state["last_scan_summary"]

    def request_one(self, key):
        if not self.store.get(key):
            return False
        self.store.requeue(key)
        with self.lock:
            if key not in self.manual:
                self.manual.append(key)
        log.info("Petición manual: %s", self.store.get(key)["title"])
        self.wake.set()
        return True

    def request_all(self):
        self.run_all = True
        log.info("Petición manual: comprobar todos los pendientes ahora")
        self.wake.set()

    def stop(self):
        with self.lock:
            self.manual.clear()
        self.run_all = False
        if self.state["current_key"]:
            CANCEL.set()
            log.info("Deteniendo: se corta la comprobación en marcha")
        else:
            log.info("Detenido: cola manual vaciada")
        self.wake.set()

    def public_state(self):
        hour = datetime.now().hour
        with self.lock:
            queue = list(self.manual)
        return {**self.state, "queue": queue, "run_all": self.run_all,
                "in_active_hours": self.cfg.in_active_hours(hour),
                "kinds": self.cfg.kinds, "active_hours": self.cfg.active_hours,
                "watching": [w["title"] for w in self.watch.values()]}

    # ------------------------------------------------------------------ escaneo
    def scan(self, manual=False):
        with self.scan_lock:
            self._scan(manual)

    def _scan(self, manual):
        live = set()
        added = 0
        # Solo los tipos activados en MEDIA_TYPES; los demás salen del informe.
        for kind in self.cfg.kinds:
            syncs = latest_syncs(self.bazarr.history(kind), kind)
            greens = []
            for s in syncs:
                row = s["row"]
                level = level_for(s["offset"], s["fps"], self.cfg)
                lang = row.get("language") or {}
                item = {
                    "key": f"{s['path']}|{s['ts']}",
                    "kind": kind,
                    "media_id": s["media_id"],
                    "series_id": row.get("sonarrSeriesId"),
                    "title": row.get("title") if kind == "movies"
                    else f"{row.get('seriesTitle')} · {row.get('episode_number', '')} {row.get('episodeTitle') or ''}".strip(),
                    "language": lang.get("code2", "") + (":hi" if lang.get("hi") else "") + (":forced" if lang.get("forced") else ""),
                    "history_sub": s["path"],
                    "sync_ts": s["ts"],
                    "bazarr_offset": s["offset"],
                    "bazarr_fps": s["fps"],
                    "level": level,
                    "is_control": 0,
                    "status": "pending",
                    "provider": s.get("provider"),
                    "subs_id": s.get("subs_id"),
                }
                if level == "green":
                    greens.append(item)
                    continue
                live.add(item["key"])
                added += self.store.upsert_seen(item)
            # Grupo de control: los verdes más recientes.
            for item in greens[: self.cfg.control_samples]:
                item["is_control"] = 1
                live.add(item["key"])
                added += self.store.upsert_seen(item)
        self._follow_up_watches()
        removed = self.store.mark_obsolete(live)
        self.state["last_scan"] = time.time()
        self.state["last_scan_error"] = None
        summary = f"Bazarr leído: {added} nuevas, {removed} retiradas, {len(live)} en el informe"
        if self.watch:
            summary += f"; esperando sub nuevo para {len(self.watch)}"
        self.state["last_scan_summary"] = summary
        # Los escaneos de vigilancia (cada minuto) solo se anotan si hay novedades.
        if manual or added or removed or not self.watch:
            log.info("%s%s", summary, " (a petición)" if manual else "")

    def _follow_up_watches(self):
        """Tras descartar o buscar: si Bazarr ya tiene sub nuevo, se comprueba al momento."""
        now = time.time()
        for mkey, w in list(self.watch.items()):
            newer = [i for i in self.store.find_by_media(mkey, exclude_key=w["key"])
                     if (i["first_seen"] or 0) >= w["since"] - 5]
            if newer:
                new = newer[0]
                log.info("Bazarr ya tiene un sub nuevo para %s: se comprueba ahora", new["title"])
                self.store.set_action_note(w["key"], "Bazarr descargó otro sub: ver la fila nueva.")
                self.request_one(new["key"])
                self.watch.pop(mkey, None)
            elif now > w["deadline"]:
                self.store.set_action_note(w["key"], w["note"] + " Bazarr no ha encontrado otro sub (revísalo a mano).")
                log.info("Sin sub nuevo para %s tras %d min", w["title"], WATCH_MINUTES)
                self.watch.pop(mkey, None)

    # --------------------------------------------------------------- acciones
    def available_actions(self, item):
        v = item.get("verdict")
        acts = []
        if v == "fixable":
            acts.append("fix")
        if v in ("other_cut", "disagree", "fixable", "ok", "error") and item.get("sub_path"):
            acts.append("replace")
        if v == "gone":
            acts.append("search")
        return acts

    def perform(self, key, action, auto=False):
        """Ejecuta una acción en Bazarr. Devuelve (ok, mensaje)."""
        item = self.store.get(key)
        if not item:
            return False, "No existe"
        if action not in self.available_actions(item):
            return False, "Esa acción no aplica a este veredicto"
        mkey = media_key(item)
        n = self.store.attempts(mkey)
        if auto and n >= self.cfg.max_attempts:
            self.store.set_action_note(key, f"Límite de {self.cfg.max_attempts} acciones alcanzado: revísalo a mano.")
            log.info("No se actúa sobre %s: límite de %d acciones", item["title"], self.cfg.max_attempts)
            return False, "Límite de acciones alcanzado"

        code2, _, flags = item["language"].partition(":")
        hi, forced = "hi" in flags, "forced" in flags
        sub_bazarr = self.cfg.bazarr_path(item.get("sub_path"))
        who = "Acción automática" if auto else "Acción"
        try:
            if action == "fix":
                r = item_result(item)
                secs = (r["ffsubsync"]["median"] + r["alass"]["median"]) / 2
                self.bazarr.shift_subtitle(item["kind"], item["media_id"], code2, forced, hi, sub_bazarr, secs)
                n = self.store.add_attempt(mkey)
                note = f"Corregido {secs:+.2f} s en Bazarr ({n}/{self.cfg.max_attempts}); comprobando de nuevo."
                self.store.save_action(key, "fix", note)
                log.info("%s: %s desplazado %+.2f s", who, item["title"], secs)
                self.request_one(key)  # se vuelve a comprobar el mismo archivo
                return True, note

            if action == "replace":
                same_file = sub_bazarr and sub_bazarr == item["history_sub"]
                if item.get("provider") and item.get("subs_id") and same_file:
                    self.bazarr.blacklist(item["kind"], item["media_id"], item["series_id"], item["provider"],
                                          item["subs_id"], code2, sub_bazarr)
                    how = f"en lista negra ({item['provider']})"
                else:
                    # Sin proveedor (sub subido a mano o sub distinto del de la sincronización):
                    # se borra y se pide otro. Bazarr podría volver a elegir el mismo.
                    self.bazarr.delete_subtitle(item["kind"], item["media_id"], item["series_id"],
                                                code2, forced, hi, sub_bazarr)
                    self.bazarr.download_subtitle(item["kind"], item["media_id"], item["series_id"], code2, forced, hi)
                    how = "borrado (sin proveedor para la lista negra)"
                n = self.store.add_attempt(mkey)
                note = f"Descartado {n}/{self.cfg.max_attempts}: {how}. Esperando sub nuevo de Bazarr…"
            else:  # search
                self.bazarr.download_subtitle(item["kind"], item["media_id"], item["series_id"], code2, forced, hi)
                n = self.store.add_attempt(mkey)
                note = f"Búsqueda pedida a Bazarr ({n}/{self.cfg.max_attempts}). Esperando sub nuevo…"
        except Exception as e:  # noqa: BLE001
            log.warning("%s fallida en %s: %s", who, item["title"], e)
            self.store.set_action_note(key, f"La acción falló: {e}")
            return False, str(e)

        self.store.save_action(key, action, note, status="acted")
        self.watch[mkey] = {"key": key, "title": item["title"], "note": note, "since": time.time(),
                            "deadline": time.time() + WATCH_MINUTES * 60}
        log.info("%s: %s — %s", who, item["title"], note)
        self.request_scan(delay=45)
        return True, note

    def _auto_action(self, item):
        verdict = self.store.get(item["key"])["verdict"]
        action = self.cfg.auto_actions.get(verdict)
        if action:
            self.perform(item["key"], action, auto=True)

    # ------------------------------------------------------------ comprobación
    def resolve_paths(self, item):
        """Vídeo y subtítulo actuales. Si el sub del historial ya no existe (película
        renombrada o actualizada), se usa el sub actual del mismo idioma según Bazarr."""
        media = (self.bazarr.movie(item["media_id"]) if item["kind"] == "movies"
                 else self.bazarr.episode(item["media_id"]))
        if not media:
            return None, None, "Bazarr ya no tiene este elemento"
        video = self.cfg.local_path(media.get("path"))
        sub = self.cfg.local_path(item["history_sub"])
        note = None
        if not sub or not os.path.isfile(sub):
            code2, _, flags = item["language"].partition(":")
            want_hi, want_forced = "hi" in flags, "forced" in flags
            for s in media.get("subtitles") or []:
                if s.get("path") and s.get("code2") == code2 and bool(s.get("hi")) == want_hi \
                        and bool(s.get("forced")) == want_forced:
                    sub = self.cfg.local_path(s["path"])
                    note = "El sub de la sincronización ya no está; se comprueba el sub actual de ese idioma."
                    break
        return video, sub, note

    def check(self, item):
        self.state["current"] = item["title"]
        self.state["current_key"] = item["key"]
        CANCEL.clear()
        log.info("Comprobando %s (%s, offset de Bazarr %+.2f s)", item["title"], item["level"], item["bazarr_offset"])
        try:
            video, sub, note = self.resolve_paths(item)
            if not video:
                self.store.save_result(item["key"], "gone", None, None, None, note)
                log.info("Resultado %s: ya no está en Bazarr", item["title"])
                return
            if not sub or not os.path.isfile(sub):
                self.store.save_result(item["key"], "gone", video, None, None,
                                       "No hay sub de ese idioma en disco (se borró o cambió el vídeo).")
                log.info("Resultado %s: el subtítulo ya no está en disco", item["title"])
                self._auto_action(item)
                return
            result = verify(self.cfg, video, sub)
            self.store.save_result(item["key"], result["verdict"], video, sub, result, note)
            log.info("Resultado %s: %s", item["title"], VERDICTS.get(result["verdict"], result["verdict"]))
            if result["verdict"] == "ok":
                self.store.reset_attempts(media_key(item))  # resuelto: el contador vuelve a cero
            self._auto_action(item)
        except Cancelled:
            self.store.requeue(item["key"])
            log.info("Cancelado: %s vuelve a la cola", item["title"])
        except Exception as e:  # noqa: BLE001
            log.exception("Fallo comprobando %s", item["title"])
            self.store.save_result(item["key"], "error", None, None, {"error": str(e)}, None)
        finally:
            self.state["current"] = None
            self.state["current_key"] = None
            CANCEL.clear()

    def _next_item(self):
        """Primero lo pedido a mano; luego la cola, si es horario o se pidió todo."""
        while True:
            with self.lock:
                key = self.manual.popleft() if self.manual else None
            if key is None:
                break
            item = self.store.get(key)
            if item and item["status"] == "pending":
                return item
        if self.run_all or self.cfg.in_active_hours(datetime.now().hour):
            item = self.store.next_pending()
            if item:
                return item
            if self.run_all:
                self.run_all = False
                log.info("Cola terminada: no quedan pendientes")
        return None

    # ------------------------------------------------------------------- bucle
    def run_forever(self):
        was_in_hours = None
        while True:
            if time.time() >= self.next_scan:
                try:
                    self.scan()
                except Exception as e:  # noqa: BLE001
                    log.exception("No he podido leer el historial de Bazarr")
                    self.state["last_scan_error"] = str(e)
                # Mientras se espera un sub nuevo tras una acción, se mira cada minuto.
                interval = 60 if self.watch else self.cfg.scan_interval_minutes * 60
                self.next_scan = time.time() + interval

            in_hours = self.cfg.in_active_hours(datetime.now().hour)
            if in_hours != was_in_hours and self.cfg.active_hours.strip():
                log.info("%s del horario automático (%s h)", "Dentro" if in_hours else "Fuera", self.cfg.active_hours)
                was_in_hours = in_hours

            item = self._next_item()
            if item:
                self.check(item)
                continue
            self.wake.wait(timeout=60)
            self.wake.clear()

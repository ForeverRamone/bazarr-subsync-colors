"""Bucle principal: lee el historial de Bazarr y comprueba las sincronizaciones sospechosas.

Trabaja solo en ACTIVE_HOURS, salvo lo que se pida a mano desde la web:
- "Comprobar ahora" en una fila: se procesa en cuanto termine lo que esté en marcha.
- "Comprobar pendientes ahora": procesa toda la cola, sin mirar la hora.
- "Detener": corta lo que esté en marcha y vacía las peticiones manuales.
"""
import logging
import os
import threading
import time
from collections import deque
from datetime import datetime

from .bazarr import Bazarr, latest_syncs, level_for
from .verify import CANCEL, VERDICTS, Cancelled, verify

log = logging.getLogger("doctor.worker")


class Worker:
    def __init__(self, cfg, store):
        self.cfg = cfg
        self.store = store
        self.bazarr = Bazarr(cfg.bazarr_url, cfg.bazarr_api_key)
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.manual = deque()  # claves pedidas a mano, en orden
        self.run_all = False
        self.scan_requested = False
        self.state = {"current": None, "current_key": None, "last_scan": None, "last_scan_error": None,
                      "kinds": cfg.kinds, "active_hours": cfg.active_hours}

    # ------------------------------------------------------- peticiones de la web
    def request_scan(self):
        self.scan_requested = True
        self.wake.set()

    def request_one(self, key):
        if not self.store.get(key):
            return False
        self.store.requeue(key)
        with self.lock:
            if key not in self.manual and key != self.state["current_key"]:
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
                "in_active_hours": self.cfg.in_active_hours(hour)}

    # ------------------------------------------------------------------ escaneo
    def scan(self):
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
        removed = self.store.mark_obsolete(live)
        self.state["last_scan"] = time.time()
        self.state["last_scan_error"] = None
        log.info("Bazarr leído: %d nuevas, %d retiradas, %d en el informe", added, removed, len(live))

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
                return
            result = verify(self.cfg, video, sub)
            self.store.save_result(item["key"], result["verdict"], video, sub, result, note)
            log.info("Resultado %s: %s", item["title"], VERDICTS.get(result["verdict"], result["verdict"]))
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
        next_scan = 0
        was_in_hours = None
        while True:
            if time.time() >= next_scan or self.scan_requested:
                self.scan_requested = False
                try:
                    self.scan()
                except Exception as e:  # noqa: BLE001
                    log.exception("No he podido leer el historial de Bazarr")
                    self.state["last_scan_error"] = str(e)
                next_scan = time.time() + self.cfg.scan_interval_minutes * 60

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

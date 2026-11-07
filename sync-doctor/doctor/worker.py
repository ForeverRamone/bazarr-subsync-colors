"""Bucle principal: lee el historial de Bazarr y comprueba las sincronizaciones sospechosas."""
import logging
import os
import threading
import time
from datetime import datetime

from .bazarr import Bazarr, latest_syncs, level_for
from .verify import verify

log = logging.getLogger("doctor.worker")


class Worker:
    def __init__(self, cfg, store):
        self.cfg = cfg
        self.store = store
        self.bazarr = Bazarr(cfg.bazarr_url, cfg.bazarr_api_key)
        self.wake = threading.Event()
        self.state = {"current": None, "last_scan": None, "last_scan_error": None}

    # ------------------------------------------------------------------ escaneo
    def scan(self):
        live = set()
        added = 0
        for kind in ("movies", "episodes"):
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
        log.info("Escaneo: %d nuevas, %d retiradas, %d vigentes", added, removed, len(live))

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
        log.info("Comprobando %s (%s, offset Bazarr %+.2f s)", item["title"], item["level"], item["bazarr_offset"])
        try:
            video, sub, note = self.resolve_paths(item)
            if not video:
                self.store.save_result(item["key"], "gone", None, None, None, note)
                return
            if not sub or not os.path.isfile(sub):
                self.store.save_result(item["key"], "gone", video, None, None,
                                       "No hay sub de ese idioma en disco (se borró o cambió el vídeo).")
                return
            result = verify(self.cfg, video, sub)
            self.store.save_result(item["key"], result["verdict"], video, sub, result, note)
            log.info("Resultado %s: %s", item["title"], result["verdict"])
        except Exception as e:  # noqa: BLE001
            log.exception("Fallo comprobando %s", item["title"])
            self.store.save_result(item["key"], "error", None, None, {"error": str(e)}, None)
        finally:
            self.state["current"] = None

    # ------------------------------------------------------------------- bucle
    def run_forever(self):
        next_scan = 0
        while True:
            if time.time() >= next_scan:
                try:
                    self.scan()
                except Exception as e:  # noqa: BLE001
                    log.exception("No he podido leer el historial de Bazarr")
                    self.state["last_scan_error"] = str(e)
                next_scan = time.time() + self.cfg.scan_interval_minutes * 60

            if self.cfg.in_active_hours(datetime.now().hour):
                item = self.store.next_pending()
                if item:
                    self.check(item)
                    continue
            self.wake.wait(timeout=60)
            if self.wake.is_set():
                self.wake.clear()
                next_scan = 0

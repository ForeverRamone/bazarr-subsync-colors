"""Estado en SQLite: qué sincronizaciones hay y qué veredicto tienen."""
import json
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    key            TEXT PRIMARY KEY,   -- ruta del sub en el historial + fecha de la sincronización
    kind           TEXT NOT NULL,      -- movies | episodes
    media_id       INTEGER,
    series_id      INTEGER,
    title          TEXT,
    language       TEXT,
    history_sub    TEXT,
    sync_ts        TEXT,
    bazarr_offset  REAL,
    bazarr_fps     REAL,
    level          TEXT,               -- green | yellow | red (según Bazarr)
    is_control     INTEGER DEFAULT 0,
    status         TEXT DEFAULT 'pending',  -- pending | done | skipped
    verdict        TEXT,
    video_path     TEXT,
    sub_path       TEXT,
    result_json    TEXT,
    note           TEXT,
    first_seen     REAL,
    checked_at     REAL
);
CREATE INDEX IF NOT EXISTS items_status ON items(status);
"""

LEVEL_ORDER = {"red": 0, "yellow": 1, "green": 2}


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript(SCHEMA)

    def upsert_seen(self, item):
        """Añade una sincronización nueva; si ya existe no la toca."""
        with self.lock, self.db:
            cur = self.db.execute(
                """INSERT OR IGNORE INTO items
                   (key, kind, media_id, series_id, title, language, history_sub, sync_ts,
                    bazarr_offset, bazarr_fps, level, is_control, status, first_seen)
                   VALUES (:key, :kind, :media_id, :series_id, :title, :language, :history_sub, :sync_ts,
                           :bazarr_offset, :bazarr_fps, :level, :is_control, :status, :first_seen)""",
                {**item, "first_seen": time.time()},
            )
            return cur.rowcount > 0

    def mark_obsolete(self, live_keys):
        """Las que ya no son la última sincronización vigente se quitan del informe."""
        with self.lock, self.db:
            rows = self.db.execute("SELECT key FROM items").fetchall()
            gone = [r["key"] for r in rows if r["key"] not in live_keys]
            self.db.executemany("DELETE FROM items WHERE key = ?", [(k,) for k in gone])
            return len(gone)

    def next_pending(self):
        with self.lock:
            rows = self.db.execute("SELECT * FROM items WHERE status = 'pending'").fetchall()
        if not rows:
            return None
        rows = sorted(rows, key=lambda r: (r["is_control"], LEVEL_ORDER.get(r["level"], 9), -abs(r["bazarr_offset"] or 0)))
        return dict(rows[0])

    def save_result(self, key, verdict, video_path, sub_path, result, note=None, status="done"):
        with self.lock, self.db:
            self.db.execute(
                """UPDATE items SET status = ?, verdict = ?, video_path = ?, sub_path = ?,
                   result_json = ?, note = ?, checked_at = ? WHERE key = ?""",
                (status, verdict, video_path, sub_path, json.dumps(result, ensure_ascii=False), note, time.time(), key),
            )

    def requeue(self, key=None):
        with self.lock, self.db:
            if key:
                self.db.execute("UPDATE items SET status = 'pending' WHERE key = ?", (key,))
            else:
                self.db.execute("UPDATE items SET status = 'pending' WHERE verdict = 'error'")

    def all(self):
        with self.lock:
            rows = [dict(r) for r in self.db.execute("SELECT * FROM items").fetchall()]
        for r in rows:
            r["result"] = json.loads(r.pop("result_json") or "null")
        return rows

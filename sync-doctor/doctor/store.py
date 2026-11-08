"""Estado en SQLite: sincronizaciones, veredictos, acciones y ajustes."""
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
    status         TEXT DEFAULT 'pending',  -- pending | done | acted
    verdict        TEXT,
    video_path     TEXT,
    sub_path       TEXT,
    result_json    TEXT,
    note           TEXT,
    first_seen     REAL,
    checked_at     REAL
);
CREATE INDEX IF NOT EXISTS items_status ON items(status);
CREATE TABLE IF NOT EXISTS attempts (
    media_key  TEXT PRIMARY KEY,       -- tipo|id|idioma
    count      INTEGER DEFAULT 0,
    last_at    REAL
);
CREATE TABLE IF NOT EXISTS settings (
    name   TEXT PRIMARY KEY,
    value  TEXT
);
"""

# Columnas añadidas en la fase 2 (las bases de datos antiguas se amplían solas).
NEW_COLUMNS = {
    "provider": "TEXT",
    "subs_id": "TEXT",
    "action": "TEXT",          # fix | replace | search
    "action_at": "REAL",
    "action_note": "TEXT",
    "source": "TEXT DEFAULT 'history'",  # history | disk (sub sin sincronización en el historial)
    "manual_ok": "INTEGER DEFAULT 0",     # marcado como bien a mano
    "prev_verdict": "TEXT",               # veredicto antes de marcarlo (para deshacer)
    "applied_shift": "REAL",              # desplazamiento aplicado por "Aplicar" (para deshacerlo)
}

# Orden de la cola: rojos, amarillos, los del disco (sin datos) y por último los verdes.
LEVEL_ORDER = {"red": 0, "yellow": 1, "unknown": 2, "green": 3}


def media_key(item):
    """Título + idioma, sin distinguir variantes (es, es:hi, es:forced): al descartar
    un sub, Bazarr puede descargar el sustituto en otra variante del mismo idioma."""
    code2 = (item.get("language") or "").split(":")[0]
    return f"{item['kind']}|{item['media_id']}|{code2}"


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock, self.db:
            self.db.executescript(SCHEMA)
            have = {r["name"] for r in self.db.execute("PRAGMA table_info(items)")}
            for col, typ in NEW_COLUMNS.items():
                if col not in have:
                    self.db.execute(f"ALTER TABLE items ADD COLUMN {col} {typ}")

    # ---------------------------------------------------------------- items
    def upsert_seen(self, item):
        """Añade una sincronización nueva; si ya existe solo completa proveedor/id."""
        with self.lock, self.db:
            cur = self.db.execute(
                """INSERT OR IGNORE INTO items
                   (key, kind, media_id, series_id, title, language, history_sub, sync_ts,
                    bazarr_offset, bazarr_fps, level, is_control, status, first_seen, provider, subs_id, source)
                   VALUES (:key, :kind, :media_id, :series_id, :title, :language, :history_sub, :sync_ts,
                           :bazarr_offset, :bazarr_fps, :level, :is_control, :status, :first_seen,
                           :provider, :subs_id, :source)""",
                {"source": "history", **item, "first_seen": time.time()},
            )
            if cur.rowcount == 0 and item.get("provider"):
                self.db.execute("UPDATE items SET provider = ?, subs_id = ? WHERE key = ? AND provider IS NULL",
                                (item["provider"], item["subs_id"], item["key"]))
            return cur.rowcount > 0

    def upsert_disk(self, item):
        """Sub del disco: alta si es nuevo; si el archivo cambió (fecha de modificación
        distinta de la última comprobada), vuelve a la cola. Devuelve 1 si hay trabajo nuevo."""
        if self.upsert_seen(item):
            return 1
        with self.lock, self.db:
            row = self.db.execute("SELECT sync_ts, status FROM items WHERE key = ?", (item["key"],)).fetchone()
            if row and row["sync_ts"] != item["sync_ts"] and row["status"] != "pending":
                self.db.execute("UPDATE items SET sync_ts = ?, status = 'pending', provider = COALESCE(?, provider), "
                                "subs_id = COALESCE(?, subs_id) WHERE key = ?",
                                (item["sync_ts"], item["provider"], item["subs_id"], item["key"]))
                return 1
        return 0

    def set_applied_shift(self, key, value):
        with self.lock, self.db:
            self.db.execute("UPDATE items SET applied_shift = ? WHERE key = ?", (value, key))

    def set_sync_ts(self, key, value):
        with self.lock, self.db:
            self.db.execute("UPDATE items SET sync_ts = ? WHERE key = ?", (value, key))

    def mark_obsolete(self, live_keys, keep_acted_seconds=24 * 3600):
        """Quita las que ya no son la última sincronización vigente.

        Las que tienen una acción en curso se conservan un tiempo (para ver
        "esperando sub nuevo"), salvo que ya haya una sincronización nueva del
        mismo título e idioma.
        """
        now = time.time()
        with self.lock, self.db:
            rows = [dict(r) for r in self.db.execute("SELECT * FROM items").fetchall()]
            live_media = {media_key(r) for r in rows if r["key"] in live_keys}
            gone = []
            for r in rows:
                if r["key"] in live_keys:
                    continue
                acted_recently = r["action"] and r["action_at"] and now - r["action_at"] < keep_acted_seconds
                if acted_recently and media_key(r) not in live_media:
                    continue
                gone.append(r["key"])
            self.db.executemany("DELETE FROM items WHERE key = ?", [(k,) for k in gone])
            return len(gone)

    def get(self, key):
        with self.lock:
            row = self.db.execute("SELECT * FROM items WHERE key = ?", (key,)).fetchone()
        return dict(row) if row else None

    def find_by_media(self, mkey, exclude_key=None):
        with self.lock:
            rows = [dict(r) for r in self.db.execute("SELECT * FROM items").fetchall()]
        return [r for r in rows if media_key(r) == mkey and r["key"] != exclude_key]

    def next_pending(self):
        with self.lock:
            rows = self.db.execute("SELECT * FROM items WHERE status = 'pending'").fetchall()
        if not rows:
            return None
        rows = sorted(rows, key=lambda r: (r["is_control"], LEVEL_ORDER.get(r["level"], 9), -abs(r["bazarr_offset"] or 0)))
        return dict(rows[0])

    def mark_ok(self, key, on=True):
        """Marca a mano como bien (o deshace la marca, recuperando el veredicto anterior)."""
        with self.lock, self.db:
            if on:
                self.db.execute("""UPDATE items SET prev_verdict = verdict, verdict = 'ok', manual_ok = 1,
                                   action_note = 'Marcado como bien a mano.' WHERE key = ?""", (key,))
            else:
                self.db.execute("""UPDATE items SET verdict = COALESCE(prev_verdict, verdict), manual_ok = 0,
                                   prev_verdict = NULL, action_note = NULL WHERE key = ?""", (key,))

    def save_result(self, key, verdict, video_path, sub_path, result, note=None, status="done"):
        # Una comprobación nueva sustituye a la marca manual.
        with self.lock, self.db:
            self.db.execute(
                """UPDATE items SET status = ?, verdict = ?, video_path = ?, sub_path = ?,
                   result_json = ?, note = ?, checked_at = ?, manual_ok = 0, prev_verdict = NULL WHERE key = ?""",
                (status, verdict, video_path, sub_path, json.dumps(result, ensure_ascii=False), note, time.time(), key),
            )

    def save_action(self, key, action, note, status=None):
        with self.lock, self.db:
            if status:
                self.db.execute("UPDATE items SET action = ?, action_at = ?, action_note = ?, status = ? WHERE key = ?",
                                (action, time.time(), note, status, key))
            else:
                self.db.execute("UPDATE items SET action = ?, action_at = ?, action_note = ? WHERE key = ?",
                                (action, time.time(), note, key))

    def set_action_note(self, key, note):
        with self.lock, self.db:
            self.db.execute("UPDATE items SET action_note = ? WHERE key = ?", (note, key))

    def requeue(self, key=None):
        with self.lock, self.db:
            if key:
                self.db.execute("UPDATE items SET status = 'pending' WHERE key = ?", (key,))
            else:
                self.db.execute("UPDATE items SET status = 'pending' WHERE verdict = 'error'")

    def all(self):
        with self.lock:
            rows = [dict(r) for r in self.db.execute("SELECT * FROM items").fetchall()]
            attempts = {r["media_key"]: r["count"] for r in self.db.execute("SELECT * FROM attempts")}
        for r in rows:
            r["result"] = json.loads(r.pop("result_json") or "null")
            r["attempts"] = attempts.get(media_key(r), 0)
        return rows

    # ------------------------------------------------------------- intentos
    def attempts(self, mkey):
        with self.lock:
            row = self.db.execute("SELECT count FROM attempts WHERE media_key = ?", (mkey,)).fetchone()
        return row["count"] if row else 0

    def add_attempt(self, mkey):
        with self.lock, self.db:
            self.db.execute("""INSERT INTO attempts (media_key, count, last_at) VALUES (?, 1, ?)
                               ON CONFLICT(media_key) DO UPDATE SET count = count + 1, last_at = excluded.last_at""",
                            (mkey, time.time()))
        return self.attempts(mkey)

    def reset_attempts(self, mkey):
        with self.lock, self.db:
            self.db.execute("DELETE FROM attempts WHERE media_key = ?", (mkey,))

    # -------------------------------------------------------------- ajustes
    def load_settings(self):
        with self.lock:
            return {r["name"]: json.loads(r["value"]) for r in self.db.execute("SELECT * FROM settings")}

    def save_settings(self, values):
        with self.lock, self.db:
            self.db.executemany("INSERT OR REPLACE INTO settings (name, value) VALUES (?, ?)",
                                [(k, json.dumps(v)) for k, v in values.items()])

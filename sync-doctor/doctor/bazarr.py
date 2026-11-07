"""Cliente mínimo de la API de Bazarr (solo lectura) y detección de sincronizaciones."""
import json
import re
import urllib.parse
import urllib.request

SYNC_RE = re.compile(
    r"offset of\s+(-?\d+(?:\.\d+)?)\s+seconds and a framerate scale factor of\s+(-?\d+(?:\.\d+)?)",
    re.I,
)
REPLACING_ACTIONS = {0, 1, 2, 3, 4}  # borrado, descarga, manual, mejora, subida


class Bazarr:
    def __init__(self, url, api_key, timeout=60):
        self.url = url
        self.api_key = api_key
        self.timeout = timeout

    def _get(self, path, params=None):
        query = urllib.parse.urlencode(params or [], doseq=True)
        req = urllib.request.Request(
            f"{self.url}/api/{path}" + (f"?{query}" if query else ""),
            headers={"X-API-KEY": self.api_key, "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.load(resp)

    def history(self, kind):
        """kind: 'movies' o 'episodes'. Devuelve todo el historial, del más nuevo al más viejo."""
        return self._get(f"{kind}/history", {"start": 0, "length": -1}).get("data", [])

    def movie(self, radarr_id):
        data = self._get("movies", [("radarrid[]", radarr_id)]).get("data", [])
        return data[0] if data else None

    def episode(self, episode_id):
        data = self._get("episodes", [("episodeid[]", episode_id)]).get("data", [])
        return data[0] if data else None


def parse_sync(description):
    m = SYNC_RE.search(description or "")
    if not m:
        return None
    return float(m.group(1)), float(m.group(2))


def level_for(offset, fps, cfg):
    a = abs(offset)
    level = "green" if a <= cfg.green_max else "yellow" if a <= cfg.yellow_max else "red"
    if level == "green" and abs(fps - 1) >= 0.005:
        level = "yellow"
    return level


def latest_syncs(rows, kind):
    """Última sincronización vigente de cada archivo de subtítulos.

    Se descarta si después se borró o se descargó otro sub en la misma ruta.
    Bazarr registra la descarga en el mismo segundo que la sincronización pero
    justo después: una descarga con la misma fecha y ruta es la misma operación.
    """
    done, newer, out = set(), {}, []
    for r in rows:
        path = r.get("subtitles_path")
        media_id = r.get("radarrId") if kind == "movies" else r.get("sonarrEpisodeId")
        lang = (r.get("language") or {}).get("code2", "")
        key = path or f"{media_id}|{lang}"
        if key in done:
            continue
        ts = r.get("parsed_timestamp") or r.get("timestamp")
        sync = parse_sync(r.get("description"))
        if sync:
            done.add(key)
            evs = newer.get(key, [])
            if not any(e[0] == 0 or e[1] != ts for e in evs):
                out.append({"row": r, "offset": sync[0], "fps": sync[1], "ts": ts, "path": path, "media_id": media_id})
        elif r.get("action") in REPLACING_ACTIONS:
            newer.setdefault(key, []).append((r.get("action"), ts))
    return out

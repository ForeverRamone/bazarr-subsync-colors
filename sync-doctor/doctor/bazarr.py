"""Cliente mínimo de la API de Bazarr (solo lectura) y detección de sincronizaciones."""
import json
import re
import urllib.error
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

    def _request(self, method, path, params=None):
        # Bazarr lee los parámetros con reqparse "values": valen en la URL para cualquier método.
        query = urllib.parse.urlencode(params or [], doseq=True)
        req = urllib.request.Request(
            f"{self.url}/api/{path}" + (f"?{query}" if query else ""),
            method=method,
            headers={"X-API-KEY": self.api_key, "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace").strip()[:200]
            raise RuntimeError(f"Bazarr respondió {e.code} a {method} {path}: {detail}") from None
        return json.loads(body) if body.strip() else None

    def _get(self, path, params=None):
        return self._request("GET", path, params)

    # --------------------------------------------------------- acciones (escriben)
    @staticmethod
    def _ids(kind, media_id, series_id):
        if kind == "movies":
            return [("radarrid", media_id)]
        return [("seriesid", series_id), ("episodeid", media_id)]

    def blacklist(self, kind, media_id, series_id, provider, subs_id, language, subtitles_path):
        """Borra el sub, lo mete en la lista negra y Bazarr busca otro él solo."""
        self._request("POST", f"{kind}/blacklist", self._ids(kind, media_id, series_id) + [
            ("provider", provider), ("subs_id", subs_id), ("language", language),
            ("subtitles_path", subtitles_path)])

    def delete_subtitle(self, kind, media_id, series_id, language, forced, hi, path):
        self._request("DELETE", f"{kind}/subtitles", self._ids(kind, media_id, series_id) + [
            ("language", language), ("forced", str(forced)), ("hi", str(hi)), ("path", path)])

    def download_subtitle(self, kind, media_id, series_id, language, forced, hi):
        """Busca y descarga el mejor sub de ese idioma (en segundo plano, en Bazarr)."""
        self._request("PATCH", f"{kind}/subtitles", self._ids(kind, media_id, series_id) + [
            ("language", language), ("forced", str(forced)), ("hi", str(hi))])

    def shift_subtitle(self, kind, media_id, language, forced, hi, path, seconds):
        """Desplaza el sub con la herramienta "ajustar tiempos" de Bazarr."""
        ms_total = int(round(abs(seconds) * 1000))
        sign = -1 if seconds < 0 else 1
        h, rest = divmod(ms_total, 3_600_000)
        m, rest = divmod(rest, 60_000)
        s, ms = divmod(rest, 1000)
        action = f"shift_offset(h={sign * h},m={sign * m},s={sign * s},ms={sign * ms})"
        self._request("PATCH", "subtitles", [
            ("action", action), ("language", language), ("path", path),
            ("type", "movie" if kind == "movies" else "episode"), ("id", media_id),
            ("forced", str(forced)), ("hi", str(hi))])
        return action

    def history(self, kind):
        """kind: 'movies' o 'episodes'. Devuelve todo el historial, del más nuevo al más viejo."""
        return self._get(f"{kind}/history", {"start": 0, "length": -1}).get("data", [])

    def all_movies(self):
        return self._get("movies", {"start": 0, "length": -1}).get("data", [])

    def all_series(self):
        return self._get("series", {"start": 0, "length": -1}).get("data", [])

    def episodes_of(self, series_ids):
        out = []
        ids = list(series_ids)
        for i in range(0, len(ids), 50):
            out += self._get("episodes", [("seriesid[]", s) for s in ids[i:i + 50]]).get("data", [])
        return out

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
                # La descarga que acompaña a esta sincronización: de ahí sale el
                # proveedor y el id del sub, necesarios para la lista negra.
                dl = next((e for e in evs if e[1] == ts and e[0] in (1, 2, 3)), None)
                out.append({"row": r, "offset": sync[0], "fps": sync[1], "ts": ts, "path": path, "media_id": media_id,
                            "provider": dl[2] if dl else None, "subs_id": dl[3] if dl else None})
        elif r.get("action") in REPLACING_ACTIONS:
            newer.setdefault(key, []).append((r.get("action"), ts, r.get("provider"), r.get("subs_id")))
    return out

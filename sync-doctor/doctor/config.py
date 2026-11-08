"""Ajustes, leídos de variables de entorno."""
import os
from dataclasses import dataclass, field


def _float(name, default):
    return float(os.environ.get(name, default))


def _int(name, default):
    return int(os.environ.get(name, default))


def _path_map(raw):
    """PATH_MAP="/video=Y:;/otra=Z:" → [("/video", "Y:"), ...]"""
    pairs = []
    for part in filter(None, (raw or "").split(";")):
        src, _, dst = part.partition("=")
        if src and dst:
            pairs.append((src.rstrip("/"), dst.rstrip("/\\")))
    return pairs


_KIND_ALIASES = {
    "movies": "movies", "movie": "movies", "peliculas": "movies", "películas": "movies", "cine": "movies",
    "episodes": "episodes", "episode": "episodes", "series": "episodes", "tv": "episodes",
}


def _kinds(raw):
    """MEDIA_TYPES="movies,series" → ["movies", "episodes"] (nombres de la API de Bazarr)."""
    kinds = []
    for part in (raw or "movies,series").replace(";", ",").split(","):
        kind = _KIND_ALIASES.get(part.strip().lower())
        if part.strip() and not kind:
            raise SystemExit(f"MEDIA_TYPES: valor desconocido '{part.strip()}' (usa movies, series o los dos)")
        if kind and kind not in kinds:
            kinds.append(kind)
    return kinds or ["movies", "episodes"]


# Qué acción se permite para cada veredicto.
AUTO_ALLOWED = {
    "other_cut": "replace",   # otra versión → descartar y buscar otro
    "disagree": "replace",    # dudoso → descartar y buscar otro
    "fixable": "fix",         # corregible → aplicar el desplazamiento
    "gone": "search",         # ya no está → buscar sub
}


def _auto(raw):
    """AUTO_ACTIONS="other_cut,fixable" → {"other_cut": "replace", "fixable": "fix"}"""
    out = {}
    for part in filter(None, (p.strip() for p in (raw or "").split(","))):
        if part in AUTO_ALLOWED:
            out[part] = AUTO_ALLOWED[part]
    return out


@dataclass
class Config:
    # Qué revisar: "movies", "series" o "movies,series" (por defecto, los dos).
    kinds: list = field(default_factory=lambda: _kinds(os.environ.get("MEDIA_TYPES", "")))
    bazarr_url: str = field(default_factory=lambda: os.environ.get("BAZARR_URL", "").rstrip("/"))
    bazarr_api_key: str = field(default_factory=lambda: os.environ.get("BAZARR_API_KEY", ""))
    path_map: list = field(default_factory=lambda: _path_map(os.environ.get("PATH_MAP", "")))
    data_dir: str = field(default_factory=lambda: os.environ.get("DATA_DIR", "/data"))
    port: int = field(default_factory=lambda: _int("PORT", 8686))

    scan_interval_minutes: int = field(default_factory=lambda: _int("SCAN_INTERVAL_MINUTES", 60))
    # Horas en las que puede trabajar, p. ej. "1-8" (de 1:00 a 7:59). Vacío = siempre.
    active_hours: str = field(default_factory=lambda: os.environ.get("ACTIVE_HOURS", ""))

    # Mismos umbrales que el userscript.
    green_max: float = field(default_factory=lambda: _float("GREEN_MAX", 1))
    yellow_max: float = field(default_factory=lambda: _float("YELLOW_MAX", 10))
    # Subs verdes que también se comprueban, como grupo de control.
    control_samples: int = field(default_factory=lambda: _int("CONTROL_SAMPLES", 5))

    # Tolerancias del veredicto (segundos / fracción de líneas).
    ok_tolerance: float = field(default_factory=lambda: _float("OK_TOLERANCE", 0.5))
    agree_tolerance: float = field(default_factory=lambda: _float("AGREE_TOLERANCE", 0.5))
    split_threshold: float = field(default_factory=lambda: _float("SPLIT_THRESHOLD", 0.15))

    max_offset_seconds: int = field(default_factory=lambda: _int("MAX_OFFSET_SECONDS", 300))
    ffmpeg: str = field(default_factory=lambda: os.environ.get("FFMPEG_PATH", "ffmpeg"))
    ffsubsync: str = field(default_factory=lambda: os.environ.get("FFSUBSYNC_PATH", "ffsubsync"))
    alass: str = field(default_factory=lambda: os.environ.get("ALASS_PATH", "alass-cli"))
    engine_timeout: int = field(default_factory=lambda: _int("ENGINE_TIMEOUT_SECONDS", 1800))

    # Acciones automáticas tras comprobar (por defecto ninguna). Veredicto → acción.
    auto_actions: dict = field(default_factory=lambda: _auto(os.environ.get("AUTO_ACTIONS", "")))
    # Máximo de acciones por título e idioma (evita vaciar proveedores en bucle).
    max_attempts: int = field(default_factory=lambda: _int("MAX_ATTEMPTS", 3))

    # Ajustes que se pueden cambiar desde la web (se guardan en la base de datos).
    EDITABLE = ("kinds", "active_hours", "auto_actions", "max_attempts", "control_samples")

    def editable(self):
        return {k: getattr(self, k) for k in self.EDITABLE}

    def apply(self, values):
        """Valida y aplica ajustes que vienen de la web o de la base de datos."""
        clean = {}
        if "kinds" in values:
            kinds = [k for k in values["kinds"] if k in ("movies", "episodes")]
            if not kinds:
                raise ValueError("Elige al menos películas o series")
            clean["kinds"] = kinds
        if "active_hours" in values:
            ah = str(values["active_hours"] or "").strip()
            if ah:
                start, _, end = ah.partition("-")
                if not (start.isdigit() and end.isdigit() and 0 <= int(start) <= 24 and 0 <= int(end) <= 24):
                    raise ValueError("Horario no válido: usa por ejemplo 1-8, o déjalo vacío")
            clean["active_hours"] = ah
        if "auto_actions" in values:
            clean["auto_actions"] = {v: a for v, a in (values["auto_actions"] or {}).items()
                                     if AUTO_ALLOWED.get(v) == a}
        if "max_attempts" in values:
            clean["max_attempts"] = max(1, min(10, int(values["max_attempts"])))
        if "control_samples" in values:
            clean["control_samples"] = max(0, min(50, int(values["control_samples"])))
        for k, v in clean.items():
            setattr(self, k, v)
        return clean

    def bazarr_path(self, local_path):
        """Lo contrario de local_path: la ruta tal como la ve Bazarr."""
        if not local_path:
            return local_path
        for src, dst in self.path_map:
            norm = local_path.replace("\\", "/")
            d = dst.replace("\\", "/")
            if norm == d or norm.startswith(d + "/"):
                return src + norm[len(d):]
        return local_path

    def local_path(self, bazarr_path):
        """Convierte una ruta tal como la ve Bazarr en la ruta dentro de este contenedor."""
        if not bazarr_path:
            return bazarr_path
        for src, dst in self.path_map:
            if bazarr_path == src or bazarr_path.startswith(src + "/"):
                return dst + bazarr_path[len(src):]
        return bazarr_path

    def in_active_hours(self, hour):
        if not self.active_hours.strip():
            return True
        start, _, end = self.active_hours.partition("-")
        start, end = int(start), int(end or 24)
        if start <= end:
            return start <= hour < end
        return hour >= start or hour < end  # rango que cruza medianoche, p. ej. 22-6

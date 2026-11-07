"""Comprueba un subtítulo ya sincronizado por Bazarr con dos programas independientes.

No modifica nada: se extrae el audio una vez, se pasan ffsubsync y alass sobre
una copia del subtítulo y se mide cuánto lo moverían. Si los dos dicen que no
hay que moverlo, Bazarr acertó.
"""
import logging
import os
import re
import shutil
import statistics
import subprocess
import tempfile
import time

log = logging.getLogger("doctor.verify")

TIME_RE = re.compile(r"(\d+):(\d{1,2}):(\d{1,2})[,.](\d{1,3})\s*-->\s*(\d+):(\d{1,2}):(\d{1,2})[,.](\d{1,3})")


# ---------------------------------------------------------------------------
# SRT
# ---------------------------------------------------------------------------
def read_text(path):
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def parse_srt(path):
    """Lista de (inicio_en_segundos, texto_normalizado)."""
    blocks = []
    text = read_text(path).replace("\r\n", "\n").replace("\r", "\n")
    for chunk in re.split(r"\n\s*\n", text):
        m = TIME_RE.search(chunk)
        if not m:
            continue
        h, mi, s, ms = m.group(1, 2, 3, 4)
        start = int(h) * 3600 + int(mi) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000
        body = chunk[m.end():]
        key = re.sub(r"<[^>]+>|\{[^}]+\}|\W+", "", body).lower()
        blocks.append((start, key))
    return blocks


def pair_blocks(before, after):
    """Empareja líneas de antes y después de sincronizar (por orden, o por texto si cambió el número)."""
    if len(before) == len(after):
        return [(b[0], a[0]) for b, a in zip(before, after)]
    pending = {}
    for a in after:
        pending.setdefault(a[1], []).append(a[0])
    pairs = []
    for b in before:
        queue = pending.get(b[1])
        if queue:
            pairs.append((b[0], queue.pop(0)))
    return pairs


def measure_shift(srt_before, srt_after, split_gap=1.0):
    """Cuánto ha movido un programa el subtítulo."""
    pairs = pair_blocks(parse_srt(srt_before), parse_srt(srt_after))
    if len(pairs) < 10:
        raise ValueError(f"muy pocas líneas emparejadas ({len(pairs)})")
    deltas = [a - b for b, a in pairs]
    med = statistics.median(deltas)
    split = sum(1 for d in deltas if abs(d - med) > split_gap) / len(deltas)
    # Tramos con desplazamiento distinto (cortes o escenas añadidas).
    segments = 1 + sum(1 for x, y in zip(deltas, deltas[1:]) if abs(x - y) > 0.5)
    # Pendiente del desplazamiento a lo largo de la peli (≈ cambio de framerate).
    ts = [b for b, _ in pairs]
    slope = 0.0
    if len(ts) > 2 and max(ts) > min(ts):
        mt, md = statistics.fmean(ts), statistics.fmean(deltas)
        var = sum((t - mt) ** 2 for t in ts)
        slope = sum((t - mt) * (d - md) for t, d in zip(ts, deltas)) / var if var else 0.0
    return {
        "median": round(med, 3),
        "first": round(deltas[0], 3),
        "last": round(deltas[-1], 3),
        "split_fraction": round(split, 3),
        "segments": segments,
        "slope": round(slope, 5),
        "lines": len(pairs),
    }


# ---------------------------------------------------------------------------
# Programas externos
# ---------------------------------------------------------------------------
def _run(cmd, timeout, env=None):
    cmd = [shutil.which(cmd[0]) or cmd[0], *cmd[1:]]
    t0 = time.monotonic()
    proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout, env=env)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-5:]
        raise RuntimeError(f"{os.path.basename(cmd[0])} terminó con código {proc.returncode}: {' | '.join(tail)}")
    return time.monotonic() - t0


def extract_audio(cfg, video, wav, audio_index=0):
    cmd = [cfg.ffmpeg, "-nostdin", "-y", "-v", "error", "-i", video, "-map", f"0:a:{audio_index}",
           "-vn", "-sn", "-dn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav]
    return _run(cmd, cfg.engine_timeout)


def run_ffsubsync(cfg, reference, srt_in, srt_out):
    cmd = [cfg.ffsubsync, reference, "-i", srt_in, "-o", srt_out,
           "--max-offset-seconds", str(cfg.max_offset_seconds)]
    ff_dir = os.path.dirname(shutil.which(cfg.ffmpeg) or "")
    if ff_dir:
        cmd += ["--ffmpegpath", ff_dir]
    return _run(cmd, cfg.engine_timeout)


def run_alass(cfg, reference, srt_in, srt_out):
    env = dict(os.environ)
    ffmpeg = shutil.which(cfg.ffmpeg)
    if ffmpeg:
        env.setdefault("ALASS_FFMPEG_PATH", ffmpeg)
        probe = shutil.which("ffprobe", path=os.path.dirname(ffmpeg)) or shutil.which("ffprobe")
        if probe:
            env.setdefault("ALASS_FFPROBE_PATH", probe)
    return _run([cfg.alass, reference, srt_in, srt_out], cfg.engine_timeout, env=env)


# ---------------------------------------------------------------------------
# Veredicto
# ---------------------------------------------------------------------------
VERDICTS = {
    "ok": "Bien: los dos programas lo dejan donde está",
    "fixable": "Corregible: los dos coinciden en moverlo",
    "other_cut": "Probablemente de otra versión o montaje",
    "disagree": "Dudoso: los programas no se ponen de acuerdo",
    "gone": "El subtítulo de esa sincronización ya no está",
    "error": "Error al comprobarlo",
    "unsupported": "Formato no soportado",
}


def decide(ff, al, cfg):
    if not ff or not al:
        return "error"
    ff_ok = abs(ff["median"]) <= cfg.ok_tolerance
    al_ok = abs(al["median"]) <= cfg.ok_tolerance
    split = al["split_fraction"] >= cfg.split_threshold
    # alass a veces trocea un sub que está bien; si ffsubsync y la mayoría de
    # líneas de alass dicen "no mover", se da por bueno.
    if ff_ok and al_ok:
        return "ok"
    # "Otra versión" solo si los dos programas ven problema.
    if split and not ff_ok:
        return "other_cut"
    if not split and abs(ff["median"] - al["median"]) <= cfg.agree_tolerance:
        return "fixable"
    return "disagree"


def verify(cfg, video_path, sub_path, audio_index=0):
    """Devuelve dict con verdict, ffsubsync, alass, timings y error (si lo hay)."""
    result = {"verdict": "error", "ffsubsync": None, "alass": None, "timings": {}, "error": None}
    if not sub_path.lower().endswith(".srt"):
        result["verdict"] = "unsupported"
        result["error"] = "Solo se comprueban subtítulos .srt"
        return result
    for p, what in ((video_path, "vídeo"), (sub_path, "subtítulo")):
        if not os.path.isfile(p):
            result["error"] = f"No encuentro el {what}: {p}"
            return result

    with tempfile.TemporaryDirectory(prefix="doctor-") as tmp:
        sub_copy = os.path.join(tmp, "in.srt")
        shutil.copyfile(sub_path, sub_copy)
        wav = os.path.join(tmp, "audio.wav")
        try:
            result["timings"]["audio"] = round(extract_audio(cfg, video_path, wav, audio_index), 1)
        except Exception as e:  # noqa: BLE001
            result["error"] = f"Extrayendo audio: {e}"
            return result

        errors = []
        for name, runner in (("ffsubsync", run_ffsubsync), ("alass", run_alass)):
            out = os.path.join(tmp, f"{name}.srt")
            try:
                result["timings"][name] = round(runner(cfg, wav, sub_copy, out), 1)
                result[name] = measure_shift(sub_copy, out)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{name}: {e}")
                log.warning("%s falló con %s: %s", name, sub_path, e)

    result["verdict"] = decide(result["ffsubsync"], result["alass"], cfg)
    if errors:
        result["error"] = " / ".join(errors)
    return result

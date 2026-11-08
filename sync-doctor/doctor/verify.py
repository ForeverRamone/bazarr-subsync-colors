"""Comprueba un subtítulo ya sincronizado por Bazarr con dos programas independientes.

No modifica nada: se extrae el audio una vez, se pasan ffsubsync y alass sobre
una copia del subtítulo y se mide cuánto lo moverían. Si los dos dicen que no
hay que moverlo, Bazarr acertó.
"""
import json
import logging
import os
import re
import shutil
import statistics
import subprocess
import tempfile
import threading
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
CANCEL = threading.Event()  # "Detener" desde la web


class Cancelled(Exception):
    pass


def _run(cmd, timeout, env=None):
    """Ejecuta un programa externo; se puede cortar con CANCEL."""
    cmd = [shutil.which(cmd[0]) or cmd[0], *cmd[1:]]
    t0 = time.monotonic()
    with tempfile.TemporaryFile() as out:
        proc = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, env=env)
        while True:
            try:
                proc.wait(timeout=1)
                break
            except subprocess.TimeoutExpired:
                if CANCEL.is_set() or time.monotonic() - t0 > timeout:
                    proc.kill()
                    proc.wait()
                    if CANCEL.is_set():
                        raise Cancelled()
                    raise RuntimeError(f"{os.path.basename(cmd[0])} tardó más de {timeout} s")
        if proc.returncode != 0:
            out.seek(0)
            tail = out.read().decode("utf-8", "replace").strip().splitlines()[-5:]
            raise RuntimeError(f"{os.path.basename(cmd[0])} terminó con código {proc.returncode}: {' | '.join(tail)}")
    return time.monotonic() - t0


def extract_audio(cfg, video, wav, audio_index=0, ref_stream=None, ref_srt=None):
    """Extrae el audio y, si se indica, también un subtítulo interno (en la misma pasada)."""
    cmd = [cfg.ffmpeg, "-nostdin", "-y", "-v", "error", "-i", video, "-map", f"0:a:{audio_index}",
           "-vn", "-sn", "-dn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav]
    if ref_stream is not None and ref_srt:
        cmd += ["-map", f"0:{ref_stream}", "-c:s", "srt", ref_srt]
    return _run(cmd, cfg.engine_timeout)


# Subtítulos internos que sirven de referencia (de texto; los de imagen, como PGS, no).
TEXT_SUB_CODECS = {"subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text"}
LANG3 = {"es": ("spa", "esl"), "en": ("eng",), "fr": ("fre", "fra"), "de": ("ger", "deu"), "it": ("ita",),
         "pt": ("por",), "ca": ("cat",)}


def find_text_sub(cfg, video, sub_lang=""):
    """El mejor subtítulo interno de texto (no forzado) para usar de referencia, o None.

    Prioridad: mismo idioma que el sub comprobado, luego inglés, luego cualquiera.
    """
    ffmpeg = shutil.which(cfg.ffmpeg)
    probe = (shutil.which("ffprobe", path=os.path.dirname(ffmpeg)) if ffmpeg else None) or shutil.which("ffprobe")
    if not probe:
        return None
    try:
        out = subprocess.run([probe, "-v", "error", "-select_streams", "s", "-show_entries",
                              "stream=index,codec_name:stream_tags=language,title:stream_disposition=forced",
                              "-of", "json", video], capture_output=True, text=True, timeout=180)
        streams = json.loads(out.stdout or "{}").get("streams", [])
    except Exception as e:  # noqa: BLE001
        log.warning("  No se pudieron leer las pistas del vídeo: %s", e)
        return None
    want = LANG3.get(sub_lang, ())
    best, best_rank = None, 9
    for s in streams:
        tags = s.get("tags") or {}
        title = (tags.get("title") or "").lower()
        if s.get("codec_name") not in TEXT_SUB_CODECS:
            continue
        if (s.get("disposition") or {}).get("forced") or "forced" in title or "forzad" in title:
            continue
        lang = (tags.get("language") or "").lower()
        rank = 0 if lang in want else 1 if lang == "eng" else 2
        if rank < best_rank:
            best, best_rank = {"index": s["index"], "language": lang or "?", "codec": s.get("codec_name")}, rank
    return best


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
    "fixable": "Corregible: hay que moverlo lo mismo en toda la película",
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


def decide_reference(ref, cfg):
    """Veredicto usando el subtítulo interno del vídeo (referencia de confianza)."""
    if ref["split_fraction"] >= cfg.split_threshold:
        return "other_cut"
    if abs(ref["median"]) <= cfg.ok_tolerance:
        return "ok"
    return "fixable"


def verify(cfg, video_path, sub_path, audio_index=0, sub_lang=""):
    """Devuelve dict con verdict, reference, ffsubsync, alass, fix_source, fix_shift, timings y error.

    Si el vídeo trae un subtítulo interno de texto, se usa como referencia principal
    (alass compara sub contra sub: no depende del idioma, solo de los tiempos).
    ffsubsync y alass contra el audio quedan como apoyo, o como única opinión si no hay sub interno.
    """
    result = {"verdict": "error", "reference": None, "ffsubsync": None, "alass": None,
              "fix_source": None, "fix_shift": None, "timings": {}, "error": None}
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
        ref_info = find_text_sub(cfg, video_path, sub_lang)
        ref_srt = os.path.join(tmp, "ref.srt") if ref_info else None
        if ref_info:
            log.info("  Extrayendo audio y sub interno (%s) de %s…", ref_info["language"], os.path.basename(video_path))
        else:
            log.info("  Extrayendo audio de %s (sin sub interno de texto)…", os.path.basename(video_path))
        try:
            result["timings"]["audio"] = round(extract_audio(cfg, video_path, wav, audio_index,
                                                             ref_info and ref_info["index"], ref_srt), 1)
        except Cancelled:
            raise
        except Exception as e:  # noqa: BLE001
            if not ref_info:
                result["error"] = f"Extrayendo audio: {e}"
                log.warning("  No se pudo extraer el audio: %s", e)
                return result
            # Por si falla el sub interno: se reintenta solo con el audio.
            log.warning("  No se pudo extraer el sub interno (%s); sigo solo con el audio", e)
            ref_info = ref_srt = None
            try:
                result["timings"]["audio"] = round(extract_audio(cfg, video_path, wav, audio_index), 1)
            except Cancelled:
                raise
            except Exception as e2:  # noqa: BLE001
                result["error"] = f"Extrayendo audio: {e2}"
                return result
        log.info("  Listo en %.0f s", result["timings"]["audio"])

        errors = []
        if ref_info:
            out = os.path.join(tmp, "ref_out.srt")
            log.info("  Comparando con el sub interno (%s) usando alass…", ref_info["language"])
            try:
                result["timings"]["reference"] = round(run_alass(cfg, ref_srt, sub_copy, out), 1)
                m = measure_shift(sub_copy, out)
                result["reference"] = {**m, "language": ref_info["language"], "codec": ref_info["codec"]}
                log.info("  Según el sub interno lo movería %+.2f s (%d tramo%s)", m["median"], m["segments"],
                         "" if m["segments"] == 1 else "s")
            except Cancelled:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("  La comparación con el sub interno falló: %s", e)
        for name, runner in (("ffsubsync", run_ffsubsync), ("alass", run_alass)):
            out = os.path.join(tmp, f"{name}.srt")
            log.info("  Ejecutando %s…", name)
            try:
                result["timings"][name] = round(runner(cfg, wav, sub_copy, out), 1)
                result[name] = measure_shift(sub_copy, out)
                m = result[name]
                log.info("  %s lo movería %+.2f s (%d tramo%s) en %.0f s", name, m["median"], m["segments"],
                         "" if m["segments"] == 1 else "s", result["timings"][name])
            except Cancelled:
                raise
            except Exception as e:  # noqa: BLE001
                errors.append(f"{name}: {e}")
                log.warning("  %s falló: %s", name, e)

    ff, al, ref = result["ffsubsync"], result["alass"], result["reference"]
    if ref:
        # El sub interno manda: viene con el vídeo y casi siempre está bien.
        result["verdict"] = decide_reference(ref, cfg)
        result["audio_verdict"] = decide(ff, al, cfg) if ff and al else None
        if result["verdict"] == "fixable":
            result["fix_source"], result["fix_shift"] = "reference", ref["median"]
    else:
        result["verdict"] = decide(ff, al, cfg)
        if result["verdict"] == "fixable":
            result["fix_source"], result["fix_shift"] = "audio", (ff["median"] + al["median"]) / 2
    if errors and not ref:
        result["error"] = " / ".join(errors)
    return result

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys
import threading
from typing import Any, Callable

from common import MEDIA_DIR, cli_prefix, log, run

StatusUpdater = Callable[..., None]


def wake_camera(options: dict[str, Any], output: Path) -> Path:
    """Wake the CP2 and save a JPEG that Home Assistant can access."""
    if not bool(options.get("wake_snapshot", True)):
        raise RuntimeError(
            "wake_snapshot è disattivato: impossibile produrre lo screenshot CP2"
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    command = cli_prefix(options, keyed=False) + [
        "--json", "save", "image",
        "--serial", str(options["serial"]),
        "--channel", str(int(options.get("channel", 1))),
        "--output", str(output),
    ]
    result = run(command, timeout=60)
    if result.returncode != 0 or not output.exists() or output.stat().st_size == 0:
        raise RuntimeError("snapshot di risveglio non riuscito")
    log(f"Snapshot CP2 salvato: {output} ({output.stat().st_size} byte)")
    return output


def probe_video(path: Path) -> dict[str, Any]:
    command = [
        "ffprobe", "-hide_banner", "-v", "error",
        "-show_entries", "format=duration,size:stream=codec_name,codec_type,width,height",
        "-of", "json", str(path),
    ]
    log("Comando: " + " ".join(command))
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        timeout=30,
    )
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="" if result.stderr.endswith("\n") else "\n", flush=True)
    if result.returncode != 0:
        raise RuntimeError("FFprobe non riconosce il filmato")
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        preview = (result.stdout or "")[:200].replace("\n", " ")
        raise RuntimeError(f"output FFprobe non valido: {preview!r}") from exc

    streams = payload.get("streams", [])
    valid = any(
        isinstance(stream, dict)
        and stream.get("codec_type") == "video"
        and int(stream.get("width") or 0) > 0
        and int(stream.get("height") or 0) > 0
        for stream in streams
    )
    if not valid:
        raise RuntimeError("flusso HEVC non decifrato correttamente")
    return payload


def remux_to_mp4(source: Path, target: Path) -> None:
    target.unlink(missing_ok=True)
    result = run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
            "-i", str(source), "-map", "0:v:0", "-map", "0:a?",
            "-c", "copy", "-tag:v", "hvc1", "-movflags", "+faststart",
            str(target),
        ],
        timeout=60,
    )
    if result.returncode != 0 or not target.exists() or target.stat().st_size == 0:
        raise RuntimeError("conversione MP4 non riuscita")


def record_job(
    options: dict[str, Any],
    duration: int,
    name: str,
    snapshot_path: Path,
    update_status: StatusUpdater,
    capture_lock: threading.Lock,
) -> None:
    update_status(
        state="recording",
        started_at=datetime.now().isoformat(timespec="seconds"),
        finished_at=None,
        output=None,
        snapshot=str(snapshot_path),
        duration=duration,
        error=None,
    )
    ts_path = MEDIA_DIR / f".{name}_cp2.tmp.ts"
    mp4_path = MEDIA_DIR / f"{name}_cp2.mp4"

    try:
        ts_path.unlink(missing_ok=True)
        mp4_path.unlink(missing_ok=True)
        command = cli_prefix(options, keyed=True) + [
            "stream", "dump",
            "--serial", str(options["serial"]),
            "--channel", str(int(options.get("channel", 1))),
            "--duration", f"{duration}s",
            "--timeout", "25",
            "--format", "mpegts",
            "--output", str(ts_path),
            "--decrypt-video",
            "--decrypt-codec", str(options.get("decrypt_codec", "hevc")),
        ]
        result = run(command, timeout=duration + 75)
        if result.returncode != 0:
            raise RuntimeError(f"stream dump terminato con codice {result.returncode}")
        if not ts_path.exists() or ts_path.stat().st_size == 0:
            raise RuntimeError("stream dump vuoto")

        probe_video(ts_path)
        remux_to_mp4(ts_path, mp4_path)
        ts_path.unlink(missing_ok=True)
        update_status(
            state="completed",
            finished_at=datetime.now().isoformat(timespec="seconds"),
            output=str(mp4_path),
            error=None,
        )
        log(f"Registrazione completata: {mp4_path} ({mp4_path.stat().st_size} byte)")
    except Exception as exc:
        preserved = None
        if ts_path.exists() and ts_path.stat().st_size > 0:
            fallback = MEDIA_DIR / f"{name}_cp2_failed.ts"
            fallback.unlink(missing_ok=True)
            ts_path.replace(fallback)
            preserved = str(fallback)
        update_status(
            state="failed",
            finished_at=datetime.now().isoformat(timespec="seconds"),
            output=preserved,
            error=str(exc),
        )
        log(f"Registrazione fallita: {exc}")
    finally:
        capture_lock.release()


def latest_failed_ts() -> Path | None:
    candidates = sorted(
        MEDIA_DIR.glob("*_cp2_failed.ts"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def recover_job(
    source: Path,
    update_status: StatusUpdater,
    capture_lock: threading.Lock,
) -> None:
    target = source.with_name(source.name.replace("_failed.ts", ".mp4"))
    update_status(
        state="recovering",
        started_at=datetime.now().isoformat(timespec="seconds"),
        finished_at=None,
        output=str(source),
        duration=None,
        error=None,
    )
    try:
        if source.parent.resolve() != MEDIA_DIR.resolve():
            raise RuntimeError("percorso di recupero non consentito")
        if not source.name.endswith("_cp2_failed.ts"):
            raise RuntimeError("il file non è un dump CP2 recuperabile")
        if not source.exists() or source.stat().st_size == 0:
            raise RuntimeError("file TS assente o vuoto")
        probe_video(source)
        remux_to_mp4(source, target)
        update_status(
            state="completed",
            finished_at=datetime.now().isoformat(timespec="seconds"),
            output=str(target),
            error=None,
        )
        log(f"Recupero completato: {target} ({target.stat().st_size} byte)")
    except Exception as exc:
        update_status(
            state="failed",
            finished_at=datetime.now().isoformat(timespec="seconds"),
            output=str(source),
            error=str(exc),
        )
        log(f"Recupero fallito: {exc}")
    finally:
        capture_lock.release()

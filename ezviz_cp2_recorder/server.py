from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from typing import Any
from urllib.parse import urlparse

from common import (
    MEDIA_DIR,
    PORT,
    STATUS_FILE,
    ensure_prerequisites,
    fail,
    load_options,
    log,
    sanitize_name,
)
from media import latest_failed_ts, record_job, recover_job, wake_camera


@dataclass
class JobStatus:
    state: str = "idle"
    started_at: str | None = None
    finished_at: str | None = None
    output: str | None = None
    snapshot: str | None = None
    duration: int | None = None
    error: str | None = None


status_lock = threading.Lock()
capture_lock = threading.Lock()
status = JobStatus()


def load_saved_status() -> JobStatus:
    if not STATUS_FILE.exists():
        return JobStatus()
    try:
        data = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return JobStatus()
        allowed = set(JobStatus.__dataclass_fields__)
        return JobStatus(**{key: value for key, value in data.items() if key in allowed})
    except (OSError, TypeError, json.JSONDecodeError):
        return JobStatus()


def update_status(**changes: Any) -> None:
    global status
    with status_lock:
        data = asdict(status)
        data.update(changes)
        status = JobStatus(**data)
        STATUS_FILE.write_text(
            json.dumps(asdict(status), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


def get_status() -> dict[str, Any]:
    with status_lock:
        return asdict(status)


class Handler(BaseHTTPRequestHandler):
    server_version = "EZVIZCP2Recorder/0.6.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        log(f'HTTP {self.address_string()}: {fmt % args}')

    def send_json(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorized(self) -> bool:
        expected = str(self.server.options.get("api_key", ""))  # type: ignore[attr-defined]
        supplied = self.headers.get("X-API-Key", "")
        return bool(expected) and supplied == expected

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health":
            self.send_json(200, {"ok": True, "service": "ezviz-cp2-recorder"})
            return
        if path == "/status":
            if not self.authorized():
                self.send_json(401, {"ok": False, "error": "unauthorized"})
                return
            self.send_json(200, {"ok": True, **get_status()})
            return
        self.send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path not in {"/capture", "/recover"}:
            self.send_json(404, {"ok": False, "error": "not found"})
            return
        if not self.authorized():
            self.send_json(401, {"ok": False, "error": "unauthorized"})
            return

        if path == "/recover":
            current = get_status()
            output = current.get("output")
            source = (
                Path(output)
                if isinstance(output, str) and output.endswith("_cp2_failed.ts")
                else latest_failed_ts()
            )
            if source is None:
                self.send_json(409, {"ok": False, "error": "no recoverable failed TS"})
                return
            if not capture_lock.acquire(blocking=False):
                self.send_json(409, {"ok": False, "error": "job already running", **current})
                return
            threading.Thread(
                target=recover_job,
                args=(source, update_status, capture_lock),
                daemon=True,
            ).start()
            self.send_json(
                202,
                {"ok": True, "accepted": True, "source": str(source), "status_url": "/status"},
            )
            return

        length = int(self.headers.get("Content-Length", "0") or 0)
        if length > 4096:
            self.send_json(413, {"ok": False, "error": "payload too large"})
            return
        try:
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            self.send_json(400, {"ok": False, "error": "invalid JSON"})
            return

        default_duration = int(self.server.options.get("default_duration", 60))  # type: ignore[attr-defined]
        try:
            duration = int(payload.get("duration", default_duration))
        except (TypeError, ValueError):
            self.send_json(400, {"ok": False, "error": "invalid duration"})
            return
        duration = max(5, min(duration, 120))
        name = sanitize_name(payload.get("name"))

        if not capture_lock.acquire(blocking=False):
            self.send_json(409, {"ok": False, "error": "capture already running", **get_status()})
            return

        snapshot_path = MEDIA_DIR / f"{name}_cp2.jpg"
        expected_output = MEDIA_DIR / f"{name}_cp2.mp4"
        update_status(
            state="starting",
            started_at=datetime.now().isoformat(timespec="seconds"),
            finished_at=None,
            output=str(expected_output),
            snapshot=str(snapshot_path),
            duration=duration,
            error=None,
        )

        try:
            # Return 202 only after the JPEG exists, so Home Assistant can
            # immediately pass the local file to telegram_bot.send_photo.
            wake_camera(self.server.options, snapshot_path)  # type: ignore[attr-defined]
        except Exception as exc:
            capture_lock.release()
            update_status(
                state="failed",
                finished_at=datetime.now().isoformat(timespec="seconds"),
                output=None,
                snapshot=None,
                error=str(exc),
            )
            self.send_json(502, {"ok": False, "error": str(exc)})
            return

        threading.Thread(
            target=record_job,
            args=(
                self.server.options,  # type: ignore[attr-defined]
                duration,
                name,
                snapshot_path,
                update_status,
                capture_lock,
            ),
            daemon=True,
        ).start()
        self.send_json(
            202,
            {
                "ok": True,
                "accepted": True,
                "name": name,
                "duration": duration,
                "snapshot": str(snapshot_path),
                "output": str(expected_output),
                "status_url": "/status",
            },
        )


class RecorderServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        options: dict[str, Any],
    ) -> None:
        super().__init__(address, handler)
        self.options = options


def main() -> None:
    global status
    options = load_options()
    serial = str(options.get("serial", "")).strip()
    api_key = str(options.get("api_key", ""))
    if not serial:
        fail("Inserisci il numero di serie.")
    if len(api_key) < 12:
        fail("Imposta api_key con almeno 12 caratteri.")

    ensure_prerequisites(options)
    status = load_saved_status()
    if status.state in {"starting", "recording", "recovering"}:
        update_status(
            state="interrupted",
            finished_at=datetime.now().isoformat(timespec="seconds"),
            error="App riavviata durante un'operazione",
        )

    server = RecorderServer(("0.0.0.0", PORT), Handler, options)
    log(f"API pronta su http://0.0.0.0:{PORT}")
    log("Endpoint: GET /health, GET /status, POST /capture, POST /recover")
    server.serve_forever()


if __name__ == "__main__":
    main()

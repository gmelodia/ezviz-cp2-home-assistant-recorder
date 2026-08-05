from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

from pyezvizapi.client import EzvizClient

OPTIONS_PATH = Path("/data/options.json")
CONFIG_DIR = Path("/config")
TOKEN_FILE = CONFIG_DIR / "ezviz_token.json"
KEY_FILE = CONFIG_DIR / "cp2_media_key.txt"
STATUS_FILE = CONFIG_DIR / "recorder_status.json"
MEDIA_DIR = Path("/media/intrusioni")
WAKE_FILE = CONFIG_DIR / "cp2-last-wake.jpg"
PORT = 8099

_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def log(message: str) -> None:
    print(f"[ezviz-cp2-recorder] {message}", flush=True)


def fail(message: str) -> None:
    print(f"[ezviz-cp2-recorder] ERRORE: {message}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def load_options() -> dict[str, Any]:
    try:
        data = json.loads(OPTIONS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"File opzioni non trovato: {OPTIONS_PATH}")
    except json.JSONDecodeError as exc:
        fail(f"File opzioni non valido: {exc}")
    if not isinstance(data, dict):
        fail("Il file opzioni deve contenere un oggetto JSON.")
    return data


def run(command: list[str], *, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    password = os.environ.get("EZVIZ_PASSWORD")
    safe = ["***" if password and item == password else item for item in command]
    log("Comando: " + " ".join(safe))
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        timeout=timeout,
    )
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n", flush=True)
    return result


def first_login(options: dict[str, Any]) -> None:
    username = str(options.get("username", "")).strip()
    password = str(options.get("password", ""))
    region = str(options.get("region", "apiieu.ezvizlife.com")).strip()
    if not username or not password:
        fail("Token assente: inserisci username e password nelle opzioni.")

    os.environ["EZVIZ_PASSWORD"] = password
    result = run(
        [
            "pyezvizapi", "-u", username, "-p", password, "-r", region,
            "--token-file", str(TOKEN_FILE), "--save-token", "--json",
            "devices", "status",
        ],
        timeout=90,
    )
    if result.returncode != 0:
        fail("Login EZVIZ non riuscito. Controlla credenziali e regione API.")


def load_token() -> dict[str, Any]:
    try:
        token = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"Impossibile leggere il token EZVIZ: {exc}")
    if not isinstance(token, dict) or not token.get("session_id"):
        fail("Il file ezviz_token.json non contiene una sessione valida.")
    return token


def request_media_key_code(client: EzvizClient, username: str) -> None:
    try:
        response = client.get_2fa_check_code(
            biz_type="DEVICE_ENCRYPTION",
            username=username or None,
            max_retries=1,
        )
    except Exception as exc:
        fail(f"Richiesta del codice MFA non riuscita: {exc}")

    destination = None
    contact = response.get("contact") if isinstance(response, dict) else None
    if isinstance(contact, dict):
        destination = contact.get("fuzzyContact") or contact.get("type")
    suffix = f" a {destination}" if destination else ""
    fail(
        "Codice MFA richiesto"
        f"{suffix}. Disattiva request_mfa_code, inserisci il codice in mfa_code "
        "e riavvia subito l'App. Il codice scade rapidamente."
    )


def provision_media_key(options: dict[str, Any]) -> None:
    if KEY_FILE.exists() and KEY_FILE.stat().st_size > 0:
        os.chmod(KEY_FILE, 0o600)
        return

    token = load_token()
    username = str(options.get("username", "")).strip()
    password = str(options.get("password", ""))
    serial = str(options.get("serial", "")).strip()
    region = str(options.get("region", "apiieu.ezvizlife.com")).strip()
    mfa_code = str(options.get("mfa_code", "")).strip()
    request_code = bool(options.get("request_mfa_code", False))

    client = EzvizClient(
        account=username or None,
        password=password or None,
        url=region,
        token=token,
    )
    try:
        if mfa_code:
            log("Verifico il codice MFA e recupero la chiave video.")
            try:
                key = client.get_cam_key(serial, smscode=mfa_code, max_retries=1)
            except Exception as exc:
                fail(f"Recupero della chiave video non riuscito: {exc}")
            if not key:
                fail("EZVIZ non ha restituito una chiave video valida.")
            KEY_FILE.write_text(str(key), encoding="utf-8")
            os.chmod(KEY_FILE, 0o600)
            log("Chiave video salvata. Svuota mfa_code nelle opzioni e riavvia l'App.")
            return

        if request_code:
            request_media_key_code(client, username or str(token.get("username") or ""))

        fail(
            "Manca la chiave video. Attiva request_mfa_code e avvia una volta l'App "
            "per ricevere il codice di verifica EZVIZ."
        )
    finally:
        close = getattr(client, "close_session", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


def ensure_prerequisites(options: dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    if not TOKEN_FILE.exists() or TOKEN_FILE.stat().st_size == 0:
        first_login(options)
    provision_media_key(options)
    os.chmod(TOKEN_FILE, 0o600)
    os.chmod(KEY_FILE, 0o600)


def cli_prefix(options: dict[str, Any], *, keyed: bool) -> list[str]:
    command = ["python", "-u", "/keyed_cli.py"] if keyed else ["pyezvizapi"]
    command += [
        "--token-file", str(TOKEN_FILE),
        "-r", str(options.get("region", "apiieu.ezvizlife.com")).strip(),
    ]
    if bool(options.get("debug", False)):
        command.append("--debug")
    return command


def sanitize_name(value: str | None) -> str:
    if not value:
        return datetime.now().strftime("%Y%m%d_%H%M%S")
    cleaned = _NAME_RE.sub("_", value.strip()).strip("._")
    return (cleaned or datetime.now().strftime("%Y%m%d_%H%M%S"))[:80]

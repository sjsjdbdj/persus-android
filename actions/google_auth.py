"""
google_auth.py
==============
Helper central de OAuth 2.0 para todos los servicios Google de Mark-XLVIII.

Personal-use: usa client_credentials.json tipo "Desktop".
Flujo "loopback" (RFC 8252 §7.3): abre un servidor HTTP en 127.0.0.1:puerto,
el usuario autoriza en su navegador y Google redirige al localhost con el
código. Es el método oficialmente soportado por Google desde 2022
(el antiguo OOB 'urn:ietf:wg:oauth:2.0:oob' está deprecado y rechaza las
peticiones con 'redirect_uri_mismatch').

IMPORTANTE: en Google Cloud Console (Credentials → OAuth 2.0 Client IDs →
tu cliente tipo Desktop) debes tener registrado el URI
'http://127.0.0.1' (sin puerto específico) para que coincida con el que
usaremos (el puerto se elige libre en cada ejecución).

Tokens por (servicio, cuenta) en config/google_tokens/. Cada token se refresca
automáticamente cuando expira.
"""
from __future__ import annotations

import json
import logging
import socket
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse, parse_qs

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

logger = logging.getLogger("google_auth")

# ─────────────────────────────────────────────────────────────────────────────
# Rutas base
# ─────────────────────────────────────────────────────────────────────────────

BASE_DIR    = Path(__file__).resolve().parent.parent
CONFIG_DIR  = BASE_DIR / "config"
CLIENT_PATH = CONFIG_DIR / "google_oauth_client.json"
TOKENS_DIR  = CONFIG_DIR / "google_tokens"
TOKENS_DIR.mkdir(parents=True, exist_ok=True)

# Anclamos la primera parte del URI a 'http://127.0.0.1' (loopback IPv4).
# Google Console sólo necesita el "scheme://host" sin puerto para clientes
# tipo Desktop: 'http://127.0.0.1'.
REDIRECT_HOST = "127.0.0.1"

# ─────────────────────────────────────────────────────────────────────────────
# Scopes de uso personal
# ─────────────────────────────────────────────────────────────────────────────

SCOPES_BY_SERVICE = {
    "calendar": [
        "https://www.googleapis.com/auth/calendar",
        "https://www.googleapis.com/auth/calendar.events",
    ],
    "tasks": [
        "https://www.googleapis.com/auth/tasks",
    ],
    "gmail": [
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/gmail.readonly",
    ],
    "drive": [
        "https://www.googleapis.com/auth/drive.file",
    ],
    "contacts": [
        "https://www.googleapis.com/auth/contacts",
        "https://www.googleapis.com/auth/contacts.readonly",
        # Necesario para otherContacts.list (contactos con los que has
        # intercambiado email pero no tienes guardados).
        "https://www.googleapis.com/auth/contacts.other.readonly",
    ],
}


def get_scopes(service: str) -> list[str]:
    if service not in SCOPES_BY_SERVICE:
        raise ValueError(
            f"Servicio Google desconocido: {service!r}. "
            f"Disponibles: {list(SCOPES_BY_SERVICE)}"
        )
    return list(SCOPES_BY_SERVICE[service])


# ─────────────────────────────────────────────────────────────────────────────
# Storage helpers
# ─────────────────────────────────────────────────────────────────────────────

def _safe_account(account: str) -> str:
    return (
        account.replace("@", "_at_")
                .replace(".", "_")
                .replace("/", "_")
                .replace("\\", "_")
    )


def token_path(service: str, account: str = "primary") -> Path:
    return TOKENS_DIR / f"{service}__{_safe_account(account)}.json"


def accounts_path(service: str) -> Path:
    p = TOKENS_DIR / f"{service}__accounts.json"
    return p


def load_accounts(service: str) -> list[str]:
    p = accounts_path(service)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [str(x) for x in data]
    except Exception:
        pass
    return []


def save_account(service: str, account: str) -> None:
    accts = load_accounts(service)
    if account not in accts:
        accts.append(account)
        accounts_path(service).write_text(
            json.dumps(accts, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


def token_has_refresh(service: str, account: str = "primary") -> bool:
    p = token_path(service, account)
    if not p.exists():
        return False
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return bool(data.get("refresh_token"))
    except Exception:
        return False


def delete_token(service: str, account: str = "primary") -> None:
    p = token_path(service, account)
    if p.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(p), get_scopes(service))
            try:
                creds.revoke(Request())
            except Exception:
                pass
        except Exception:
            pass
        p.unlink()
    accts = [a for a in load_accounts(service) if a != account]
    accounts_path(service).write_text(
        json.dumps(accts, indent=2), encoding="utf-8"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de redirect_uri y puerto
# ─────────────────────────────────────────────────────────────────────────────

def _load_client_info() -> dict:
    """Lee el client OAuth y devuelve los redirect_uris registrados."""
    if not CLIENT_PATH.exists():
        raise FileNotFoundError(
            f"No se encontró {CLIENT_PATH}.\n"
            "Descarga las credenciales OAuth de Google Cloud Console y "
            "guarda el archivo como config/google_oauth_client.json"
        )
    with CLIENT_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def _pick_free_port() -> int:
    """Pide un puerto libre al SO."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((REDIRECT_HOST, 0))
        return s.getsockname()[1]


def _resolve_redirect_uri() -> str:
    """
    Elige un redirect_uri que (a) figure entre los registrados en el client
    JSON, (b) apunte a 127.0.0.1. Si ninguno encaja avisa al usuario.
    """
    info = _load_client_info()
    registered = (
        info.get("installed", {}).get("redirect_uris")
        or info.get("web", {}).get("redirect_uris")
        or []
    )

    # Coincidencia exacta por prefijo 'http://127.0.0.1' (con o sin puerto).
    loopback_exact = [u for u in registered if u.startswith(f"http://{REDIRECT_HOST}")]
    if loopback_exact:
        # Tomamos el primero y le añadimos un puerto libre (loopback lo permite)
        base = loopback_exact[0]
        if base.endswith(":"):
            port = _pick_free_port()
            return f"{base}{port}"
        # Si ya viene con puerto (raro), lo respetamos si está libre;
        # si no, pedimos uno nuevo.
        port = _pick_free_port()
        return f"http://{REDIRECT_HOST}:{port}"

    # Si no hay ninguno http://127.0.0.1 pero sí hay http://localhost, lo aceptamos
    localhost = [u for u in registered if u.startswith("http://localhost")]
    if localhost:
        port = _pick_free_port()
        return f"http://localhost:{port}"

    raise RuntimeError(
        f"El client {CLIENT_PATH} no tiene registrado un redirect_uri de tipo "
        f"'http://{REDIRECT_HOST}' (o 'http://localhost').\n"
        "Ve a Google Cloud Console → APIs & Services → Credentials → tu OAuth "
        "Client ID (tipo Desktop) → Authorized redirect URIs y agrega exactamente "
        f"'http://{REDIRECT_HOST}'. Luego vuelve a intentar."
    )


# ─────────────────────────────────────────────────────────────────────────────
# OAuth flow (loopback)
# ─────────────────────────────────────────────────────────────────────────────

def _build_flow(scopes: Iterable[str], redirect_uri: str) -> Flow:
    info = _load_client_info()
    # Flow.from_client_config exige la clave de nivel superior ("installed"
    # o "web"); si se le pasa el sub-dict desempaquetado falla con
    # "Client secrets must be for a web or installed app.".
    if "installed" in info:
        client_config = dict(info)
    elif "web" in info:
        client_config = dict(info)
    else:
        # Si el JSON solo trae el sub-dict, lo envolvemos como 'installed'
        client_config = {"installed": dict(info)}

    # Mutamos solo la copia que se pasa a Flow (Flow.from_client_config
    # no acepta redirect_uri como kwarg en algunas versiones; lo añadimos
    # a la sección 'installed' para que esté disponible).
    flow = Flow.from_client_config(
        client_config,
        scopes=list(scopes),
        redirect_uri=redirect_uri,
    )
    return flow


class _LoopbackHandler(BaseHTTPRequestHandler):
    """Handler que captura el '?code=...' que Google devuelve al localhost."""
    captured_code: str | None = None
    captured_error: str | None = None

    def do_GET(self):  # noqa: N802 (BaseHTTPRequestHandler API)
        url = urlparse(self.path)
        qs = parse_qs(url.query)
        if "code" in qs:
            type(self).captured_code = qs["code"][0]
            self._respond("✅ Autorización recibida. Puedes cerrar esta pestaña.")
        elif "error" in qs:
            type(self).captured_error = qs["error"][0]
            self._respond(f"❌ Google devolvió un error: {qs['error'][0]}")
        else:
            self._respond("Esperando código…", status=400)

    def _respond(self, msg: str, status: int = 200):
        body = (
            "<html><body style='font-family:sans-serif;text-align:center;"
            "padding-top:60px;'>"
            f"<h2>Mark-XLVIII</h2><p>{msg}</p></body></html>"
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # Silencia los logs del servidor HTTP por consola
    def log_message(self, format, *args):
        logger.debug("loopback http: " + (format % args))


def _run_loopback_server(port: int, ready: threading.Event) -> HTTPServer:
    server = HTTPServer((REDIRECT_HOST, port), _LoopbackHandler)
    threading.Thread(target=ready.set, daemon=True).start() if False else ready.set()
    return server


def _prompt_authorization(flow: Flow) -> str:
    """
    Flujo loopback: levanta un servidor HTTP local en 127.0.0.1:puerto,
    abre el navegador, y bloquea hasta recibir el '?code=' que Google devuelve.
    """
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",           # fuerza refresh_token siempre
        # include_granted_scopes=False evita que Google acumule scopes de
        # autorizaciones previas en este mismo OAuth Client. Si lo dejamos
        # en True, el primer servicio que autorizas mete su scope, y los
        # siguientes tokens vienen "contaminados" -> rompe por mismatch.
        include_granted_scopes="false",
    )

    parsed = urlparse(flow.redirect_uri)
    port = parsed.port
    if not port:
        raise RuntimeError(f"redirect_uri sin puerto: {flow.redirect_uri}")

    # Reseteamos estado del handler
    _LoopbackHandler.captured_code = None
    _LoopbackHandler.captured_error = None

    server = HTTPServer((REDIRECT_HOST, port), _LoopbackHandler)
    ready = threading.Event()
    ready.set()  # HTTPServer ya está bindeado

    print("\n" + "=" * 70)
    print("  AUTORIZACIÓN DE GOOGLE REQUERIDA")
    print("=" * 70)
    print(f"\nRedirige a:   {flow.redirect_uri}")
    print(f"Abre en tu navegador (se intentará automáticamente):\n")
    print(f"   {auth_url}\n")
    print("Inicia sesión, concede los permisos y vuelve aquí.")
    print("=" * 70)

    # Abre el navegador por defecto; en Windows esto es instantáneo y no bloquea.
    try:
        webbrowser.open(auth_url, new=1, autoraise=True)
    except Exception as e:
        logger.warning("No pude abrir el navegador automáticamente: %s", e)

    # Servimos hasta recibir el código (un único hit basta).
    server.timeout = 1.0
    while not _LoopbackHandler.captured_code and not _LoopbackHandler.captured_error:
        server.handle_request()

    try:
        server.server_close()
    except Exception:
        pass

    if _LoopbackHandler.captured_error:
        raise RuntimeError(f"Google denegó la autorización: {_LoopbackHandler.captured_error}")
    if not _LoopbackHandler.captured_code:
        raise RuntimeError("No se recibió código de autorización del navegador")
    return _LoopbackHandler.captured_code


def get_credentials(
    service: str,
    account: str = "primary",
    *,
    force_relogin: bool = False,
) -> Credentials:
    """
    Devuelve credenciales válidas para el servicio solicitado.
    Reutiliza el token si existe, refresca si expiró, o dispara el flujo OAuth.

    Si el token guardado tiene un set de scopes distinto al que el servicio
    requiere hoy (p.ej. porque actualizaste SCOPES_BY_SERVICE), se borra y
    se vuelve a autorizar limpiamente.
    """
    scopes = get_scopes(service)
    p = token_path(service, account)
    creds: Credentials | None = None

    if not force_relogin and p.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(p), scopes)
        except ValueError as e:
            # El mensaje típico es: 'Scope has changed from ... to ...'
            logger.warning("Token %s con scopes desactualizados, reautorizando: %s", p, e)
            try:
                p.unlink()
            except Exception:
                pass
            creds = None
        except Exception as e:
            logger.warning("No se pudo cargar token %s: %s", p, e)
            creds = None

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            p.write_text(creds.to_json(), encoding="utf-8")
            logger.info("Token refrescado para %s/%s", service, account)
        except Exception as e:
            logger.warning("Refresh falló, se requiere re-login: %s", e)
            creds = None

    if not creds or not creds.valid:
        redirect_uri = _resolve_redirect_uri()
        flow = _build_flow(scopes, redirect_uri)
        code = _prompt_authorization(flow)
        flow.fetch_token(code=code)
        creds = flow.credentials
        p.write_text(creds.to_json(), encoding="utf-8")
        save_account(service, account)
        logger.info("Nuevo token guardado en %s", p)

    return creds


# ─────────────────────────────────────────────────────────────────────────────
# Builder genérico
# ─────────────────────────────────────────────────────────────────────────────

def build_service(service_name: str, version: str, account: str = "primary"):
    from googleapiclient.discovery import build as gbuild
    creds = get_credentials(service_name, account=account)
    return gbuild(service_name, version, credentials=creds, cache_discovery=False)


# Mapeo entre la "key" lógica (la que usan los wrappers de Mark: 'calendar',
# 'tasks', 'gmail', 'drive', 'contacts') y el nombre canónico del servicio
# en la API de Google (p.ej. 'people' para Contacts). El token se guarda y
# se carga siempre por la KEY lógica, no por el nombre canónico, así
# 'contacts' sigue funcionando aunque internamente llamemos 'people' a la API.
_LOGICAL_TO_CANONICAL: dict[str, tuple[str, str]] = {
    "calendar": ("calendar", "v3"),
    "tasks":    ("tasks",    "v1"),
    "gmail":    ("gmail",    "v1"),
    "drive":    ("drive",    "v3"),
    "contacts": ("people",   "v1"),
}


def build_service_for(service_key: str, account: str = "primary"):
    if service_key not in _LOGICAL_TO_CANONICAL:
        raise ValueError(
            f"Servicio no soportado: {service_key!r}. "
            f"Disponibles: {list(_LOGICAL_TO_CANONICAL)}"
        )
    svc_name, ver = _LOGICAL_TO_CANONICAL[service_key]
    # Pasamos service_key (lógica) a get_credentials para que use los scopes
    # y guarde el token bajo la clave correcta ('contacts' y no 'people').
    creds = get_credentials(service_key, account=account)
    from googleapiclient.discovery import build as gbuild
    return gbuild(svc_name, ver, credentials=creds, cache_discovery=False)
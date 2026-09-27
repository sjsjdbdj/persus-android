"""
gmail_manager.py
================
Wrapper de Gmail multi-cuenta para Mark-XLVIII.

Funciones principales:
  • search_messages(query)       – búsqueda libre con sintaxis Gmail
  • list_unread(limit=20)         – no leídos recientes
  • get_message(id)               – metadata (headers) por id
  • send_message(to, subj, body)  – enviar
  • modify_labels(id, add, remove) – marcar leído, archivar, etc.
"""
from __future__ import annotations

import base64
import json
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

from .google_auth import (
    build_service_for,
    token_has_refresh,
    token_path,
    load_accounts,
    save_account,
)


# Mismo esquema que ya consume ui.py: cuenta con dict {email, ...}
ACCOUNTS_FILE = (
    Path(__file__).resolve().parent.parent / "config" / "gmail_accounts.json"
)


def _load_accounts() -> list[dict[str, Any]]:
    if not ACCOUNTS_FILE.exists():
        # Migrar desde formato de google_auth si existe
        from .google_auth import load_accounts as _la
        accts = _la("gmail")
        if accts:
            out = [{"email": a} for a in accts]
            ACCOUNTS_FILE.write_text(
                json.dumps(out, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            return out
        return []
    try:
        data = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def _save_accounts(accounts: list[dict[str, Any]]) -> None:
    ACCOUNTS_FILE.write_text(
        json.dumps(accounts, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _add_account_email(email: str) -> None:
    accounts = _load_accounts()
    if not any(a.get("email") == email for a in accounts):
        accounts.append({"email": email})
        _save_accounts(accounts)
    # asegurar también en google_auth
    save_account("gmail", email)


def _resolve_email() -> str:
    """
    Devuelve el email de la cuenta Gmail activa.
    1. Si ya hay cuentas en gmail_accounts.json, toma la primera.
    2. Si no hay, autoriza con 'me' (Gmail infiere la cuenta del usuario
       autenticado) y luego descubre el email real con users.getProfile.
    """
    accts = _load_accounts()
    if accts:
        return accts[0]["email"]
    # No hay cuentas: autorizar y descubrir el email
    service = build_service_for("gmail", account="primary")
    profile = service.users().getProfile(userId="me").execute()
    email = profile.get("emailAddress", "primary")
    _add_account_email(email)
    # Re-migrar el token a la key del email real para que las próximas
    # llamadas no tengan que reautorizar.
    src = token_path("gmail", "primary")
    dst = token_path("gmail", email)
    if src.exists() and not dst.exists():
        import shutil
        shutil.copy2(src, dst)
    return email


# ─────────────────────────────────────────────────────────────────────────────
# API pública esperada por ui.py
# ─────────────────────────────────────────────────────────────────────────────

def _token_path(email: str) -> Path:
    return token_path("gmail", email)


def _get_gmail_service(email: str):
    """Compatibilidad hacia atrás con ui.py."""
    _add_account_email(email)
    return build_service_for("gmail", account=email)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers internos
# ─────────────────────────────────────────────────────────────────────────────

def _decode_body(data: str) -> str:
    try:
        return base64.urlsafe_b64decode(data + "===").decode("utf-8", errors="replace")
    except Exception:
        return ""


def _extract_headers(msg: dict[str, Any]) -> dict[str, str]:
    return {
        h.get("name", "").lower(): h.get("value", "")
        for h in msg.get("payload", {}).get("headers", [])
    }


# ─────────────────────────────────────────────────────────────────────────────
# API principal
# ─────────────────────────────────────────────────────────────────────────────

def list_unread(*, email: str | None = None, limit: int = 10, days: int = 7) -> list[dict]:
    """
    Devuelve hasta `limit` correos no leídos de los últimos `days` días
    (excluyendo promociones y redes sociales), de la cuenta indicada o de la
    primera cuenta autorizada (autoriza la primera vez si es necesario).
    """
    if email is None:
        email = _resolve_email()
    service = _get_gmail_service(email)
    res = service.users().messages().list(
        userId="me",
        q=f"is:unread newer_than:{days}d -category:promotions -category:social",
        maxResults=limit,
    ).execute()
    return res.get("messages", [])


def search_messages(query: str, *, email: str | None = None, limit: int = 20) -> list[dict]:
    """Búsqueda con sintaxis Gmail estándar."""
    if email is None:
        email = _resolve_email()
    service = _get_gmail_service(email)
    res = service.users().messages().list(
        userId="me", q=query, maxResults=limit,
    ).execute()
    return res.get("messages", [])


def get_message(msg_id: str, *, email: str | None = None, full: bool = False) -> dict:
    """Devuelve headers (y opcionalmente el cuerpo decodificado)."""
    if email is None:
        email = _resolve_email()
    service = _get_gmail_service(email)
    res = service.users().messages().get(
        userId="me", id=msg_id,
        format=("full" if full else "metadata"),
        metadataHeaders=["Subject", "From", "To", "Date"],
    ).execute()
    return res


def message_summary(msg_id: str, *, email: str | None = None) -> dict[str, str]:
    """Devuelve un dict con subject/from/date simplificado."""
    msg = get_message(msg_id, email=email)
    h = _extract_headers(msg)
    return {
        "subject": h.get("subject", "(sin asunto)"),
        "from":    h.get("from", ""),
        "to":      h.get("to", ""),
        "date":    h.get("date", ""),
        "snippet": msg.get("snippet", ""),
    }


def send_message(
    *,
    to: str | list[str],
    subject: str,
    body: str,
    email: str | None = None,
    cc: str | list[str] | None = None,
    html: bool = False,
) -> dict:
    """
    Envía un correo. Devuelve el dict con el id del mensaje enviado.
    """
    if email is None:
        email = _resolve_email()
    service = _get_gmail_service(email)

    msg = MIMEText(body, "html" if html else "plain")
    msg["to"] = ", ".join(to) if isinstance(to, list) else to
    msg["subject"] = subject
    if cc:
        msg["cc"] = ", ".join(cc) if isinstance(cc, list) else cc

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    return service.users().messages().send(
        userId="me", body={"raw": raw}
    ).execute()


def modify_labels(
    msg_id: str,
    *,
    add: list[str] | None = None,
    remove: list[str] | None = None,
    email: str | None = None,
) -> dict:
    """Añade o quita labels (UNREAD, STARRED, INBOX, TRASH, etc.)."""
    if email is None:
        email = _resolve_email()
    service = _get_gmail_service(email)
    body: dict[str, list[str]] = {}
    if add:    body["addLabelIds"]    = list(add)
    if remove: body["removeLabelIds"] = list(remove)
    return service.users().messages().modify(
        userId="me", id=msg_id, body=body
    ).execute()


def mark_read(msg_id: str, *, email: str | None = None) -> dict:
    return modify_labels(msg_id, remove=["UNREAD"], email=email)


def archive(msg_id: str, *, email: str | None = None) -> dict:
    return modify_labels(msg_id, remove=["INBOX"], email=email)


def move_to_trash(msg_id: str, *, email: str | None = None) -> dict:
    return modify_labels(msg_id, add=["TRASH"], remove=["INBOX"], email=email)


def list_labels(*, email: str | None = None) -> list[dict]:
    if email is None:
        email = _resolve_email()
    service = _get_gmail_service(email)
    res = service.users().labels().list(userId="me").execute()
    return res.get("labels", [])


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard helper
# ─────────────────────────────────────────────────────────────────────────────

def summary_for_dashboard(*, per_account: int = 3) -> dict[str, Any]:
    """Resumen compacto para el panel del dashboard (3 primeras ctas)."""
    accounts = _load_accounts()
    out: dict[str, Any] = {
        "accounts": [a["email"] for a in accounts],
        "unread_total": 0,
        "messages": [],
    }
    for a in accounts[:3]:
        email = a["email"]
        try:
            msgs = list_unread(email=email, limit=per_account * 2, days=7)
        except Exception:
            continue
        out["unread_total"] += len(msgs)
        for m in msgs[:per_account]:
            try:
                info = message_summary(m["id"], email=email)
                out["messages"].append({"email": email, **info})
            except Exception:
                continue
    return out
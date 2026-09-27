"""
google_workspace.py
====================
Fachada de alto nivel que Mark-XLVIII expone al LLM (Gemini).

En vez de enseñarle a Gemini 20 funciones distintas, le enseñamos una sola
función llamada `google` con un parámetro `intent` y un `params` libre (dict).
La fachada interpreta `intent` y delega al módulo correcto.

Intenciones soportadas:

CALENDAR
  calendar.today                  → eventos de hoy
  calendar.upcoming               → próximo evento
  calendar.search   query=...     → búsqueda
  calendar.create   title, start, end[, tz]
  calendar.delete   event_id

TASKS
  tasks.list        [list_title]
  tasks.add         title[, list_id][, due_iso][, notes]
  tasks.complete    task_id[, list_id]
  tasks.delete      task_id[, list_id]

GMAIL
  gmail.unread      [account][, limit]
  gmail.search      query[, account]
  gmail.send        to, subject, body[, account]
  gmail.read        msg_id[, account]
  gmail.mark_read   msg_id[, account]
  gmail.archive     msg_id[, account]
  gmail.trash       msg_id[, account]

DRIVE
  drive.list        [query][, folder_id]
  drive.search      text
  drive.upload      local_path[, folder_id]
  drive.download    file_id
  drive.mkdir       name[, parent_id]
  drive.share       file_id, email[, role]
  drive.delete      file_id

CONTACTS
  contacts.search   query
  contacts.list     (all other contacts)
  contacts.get      resource_name
  contacts.create   given_name[, family_name][, email][, phone]
  contacts.delete   resource_name

AUTH / UTIL
  google.status     → qué servicios están conectados
  google.disconnect service[, account]
"""
from __future__ import annotations

from typing import Any

from . import google_auth
from . import google_calendar as _cal
from . import google_tasks    as _tasks
from . import gmail_manager   as _gmail
from . import google_drive    as _drive
from . import google_contacts as _crm

SAFE_INTENTS = {
    # calendar
    "calendar.today", "calendar.upcoming", "calendar.search",
    "calendar.create", "calendar.delete",
    # tasks
    "tasks.list", "tasks.add", "tasks.complete", "tasks.delete",
    # gmail
    "gmail.unread", "gmail.search", "gmail.send",
    "gmail.read", "gmail.mark_read", "gmail.archive", "gmail.trash",
    # drive
    "drive.list", "drive.search", "drive.upload", "drive.download",
    "drive.mkdir", "drive.share", "drive.delete",
    # contacts
    "contacts.search", "contacts.list", "contacts.get",
    "contacts.create", "contacts.delete",
    # auth
    "google.status", "google.disconnect",
}


# ─────────────────────────────────────────────────────────────────────────────
# Despacho principal
# ─────────────────────────────────────────────────────────────────────────────

def google(intent: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Punto único de entrada para todas las operaciones Google.
    `intent` debe estar en SAFE_INTENTS; si no, devuelve {"ok": False, ...}.
    `params` es un dict libre cuyas claves dependen del intent.
    Devuelve un dict serializable que el LLM puede narrar.
    """
    params = params or {}

    if intent not in SAFE_INTENTS:
        return {
            "ok": False,
            "intent": intent,
            "error": (
                f"intent no soportado: {intent!r}. "
                f"Disponibles: {sorted(SAFE_INTENTS)}"
            ),
        }

    try:
        handler = _HANDLERS[intent]
        result = handler(params)
        return {"ok": True, "intent": intent, "result": result}
    except Exception as e:
        return {"ok": False, "intent": intent, "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# Handlers
# ─────────────────────────────────────────────────────────────────────────────

def _h_calendar_today(p):     return _cal.summary_for_dashboard()["today"]
def _h_calendar_upcoming(p):
    e = _cal.upcoming_event()
    return e or "Sin eventos próximos"

def _h_calendar_search(p):
    q = p.get("query") or p.get("q") or ""
    return _cal.list_events(query=q, max_results=int(p.get("max_results", 10)))

def _h_calendar_create(p):
    return _cal.create_event(
        summary=p["summary"],
        start_iso=p["start"],
        end_iso=p["end"],
        timezone_str=p.get("tz", "America/Bogota"),
        description=p.get("description"),
        location=p.get("location"),
        attendees=p.get("attendees"),
    )

def _h_calendar_delete(p):
    _cal.delete_event(p["event_id"])
    return {"deleted": p["event_id"]}


def _h_tasks_list(p):
    title = p.get("list_title")
    if title:
        lid = _tasks.find_tasklist_id(title) or "@default"
    else:
        lid = p.get("list_id", "@default")
    items = _tasks.list_tasks(tasklist_id=lid, max_results=int(p.get("max_results", 50)))
    return {"list_id": lid, "items": items}


def _h_tasks_add(p):
    return _tasks.add_task(
        title=p["title"],
        tasklist_id=p.get("list_id", "@default"),
        notes=p.get("notes"),
        due_iso=p.get("due"),
    )


def _h_tasks_complete(p):
    return _tasks.complete_task(p["task_id"], p.get("list_id", "@default"))


def _h_tasks_delete(p):
    _tasks.delete_task(p["task_id"], p.get("list_id", "@default"))
    return {"deleted": p["task_id"]}


def _h_gmail_unread(p):
    msgs = _gmail.list_unread(
        email=p.get("account"), limit=int(p.get("limit", 10))
    )
    return {"count": len(msgs), "ids": [m["id"] for m in msgs]}


def _h_gmail_search(p):
    msgs = _gmail.search_messages(
        p["query"], email=p.get("account"), limit=int(p.get("limit", 20))
    )
    return {"count": len(msgs), "ids": [m["id"] for m in msgs]}


def _h_gmail_send(p):
    res = _gmail.send_message(
        to=p["to"], subject=p["subject"], body=p["body"],
        email=p.get("account"), html=bool(p.get("html", False)),
    )
    return {"id": res.get("id"), "threadId": res.get("threadId")}


def _h_gmail_read(p):
    return _gmail.message_summary(p["msg_id"], email=p.get("account"))


def _h_gmail_mark_read(p):
    _gmail.mark_read(p["msg_id"], email=p.get("account"))
    return {"marked_read": p["msg_id"]}


def _h_gmail_archive(p):
    _gmail.archive(p["msg_id"], email=p.get("account"))
    return {"archived": p["msg_id"]}


def _h_gmail_trash(p):
    _gmail.move_to_trash(p["msg_id"], email=p.get("account"))
    return {"trashed": p["msg_id"]}


def _h_drive_list(p):
    return _drive.list_files(
        max_results=int(p.get("max_results", 25)),
        query=p.get("query"),
        folder_id=p.get("folder_id"),
    )


def _h_drive_search(p):
    return _drive.search_files(p["text"], max_results=int(p.get("max_results", 25)))


def _h_drive_upload(p):
    res = _drive.upload_file(
        p["local_path"], folder_id=p.get("folder_id"),
        filename=p.get("filename"),
    )
    return {"id": res.get("id"), "name": res.get("name"), "link": res.get("webViewLink")}


def _h_drive_download(p):
    path = _drive.download_file(p["file_id"])
    return {"path": str(path), "size": path.stat().st_size}


def _h_drive_mkdir(p):
    res = _drive.create_folder(p["name"], parent_id=p.get("parent_id"))
    return {"id": res.get("id"), "name": res.get("name"), "link": res.get("webViewLink")}


def _h_drive_share(p):
    perm = _drive.share_file(p["file_id"], email=p["email"], role=p.get("role", "reader"))
    return {"permission_id": perm.get("id")}


def _h_drive_delete(p):
    _drive.delete_file(p["file_id"])
    return {"deleted": p["file_id"]}


def _h_contacts_search(p):
    return _crm.search_contacts(p["query"])


def _h_contacts_list(p):
    return _crm.list_other_contacts()


def _h_contacts_get(p):
    return _crm.get_contact(p["resource_name"])


def _h_contacts_create(p):
    res = _crm.create_contact(
        given_name=p["given_name"],
        family_name=p.get("family_name"),
        email=p.get("email"),
        phone=p.get("phone"),
        organization=p.get("organization"),
    )
    return {"resource_name": res.get("resourceName")}


def _h_contacts_delete(p):
    _crm.delete_contact(p["resource_name"])
    return {"deleted": p["resource_name"]}


def _h_status(p):
    return {
        service: {
            "connected": google_auth.token_has_refresh(service),
            "accounts":  google_auth.load_accounts(service),
        }
        for service in ("calendar", "tasks", "gmail", "drive", "contacts")
    }


def _h_disconnect(p):
    google_auth.delete_token(p["service"], p.get("account", "primary"))
    return {"service": p["service"], "account": p.get("account", "primary"),
            "disconnected": True}


_HANDLERS = {
    # calendar
    "calendar.today":      _h_calendar_today,
    "calendar.upcoming":   _h_calendar_upcoming,
    "calendar.search":     _h_calendar_search,
    "calendar.create":     _h_calendar_create,
    "calendar.delete":     _h_calendar_delete,
    # tasks
    "tasks.list":          _h_tasks_list,
    "tasks.add":           _h_tasks_add,
    "tasks.complete":      _h_tasks_complete,
    "tasks.delete":        _h_tasks_delete,
    # gmail
    "gmail.unread":        _h_gmail_unread,
    "gmail.search":        _h_gmail_search,
    "gmail.send":          _h_gmail_send,
    "gmail.read":          _h_gmail_read,
    "gmail.mark_read":     _h_gmail_mark_read,
    "gmail.archive":       _h_gmail_archive,
    "gmail.trash":         _h_gmail_trash,
    # drive
    "drive.list":          _h_drive_list,
    "drive.search":        _h_drive_search,
    "drive.upload":        _h_drive_upload,
    "drive.download":      _h_drive_download,
    "drive.mkdir":         _h_drive_mkdir,
    "drive.share":         _h_drive_share,
    "drive.delete":        _h_drive_delete,
    # contacts
    "contacts.search":     _h_contacts_search,
    "contacts.list":       _h_contacts_list,
    "contacts.get":        _h_contacts_get,
    "contacts.create":     _h_contacts_create,
    "contacts.delete":     _h_contacts_delete,
    # auth
    "google.status":       _h_status,
    "google.disconnect":   _h_disconnect,
}


# ─────────────────────────────────────────────────────────────────────────────
# Narración amigable (lo que dirá Gemini en voz alta)
# ─────────────────────────────────────────────────────────────────────────────

def narrate(intent: str, payload: dict[str, Any]) -> str:
    """Convierte la respuesta JSON de `google()` en una frase breve."""
    if not payload.get("ok"):
        return f"Hubo un problema: {payload.get('error', 'desconocido')}"
    r = payload.get("result")

    if intent == "calendar.today":
        if isinstance(r, list) and r:
            titles = ", ".join(e.get("title", "?") for e in r[:5])
            return f"Tienes {len(r)} reuniones hoy: {titles}."
        return "Hoy no tienes reuniones."

    if intent == "calendar.upcoming":
        if isinstance(r, dict):
            return f"Próximo: {r.get('summary', 'sin título')}."
        return str(r)

    if intent == "calendar.create":
        return f"Evento '{r.get('summary')}' creado."

    if intent == "tasks.list":
        items = r.get("items", [])
        if not items:
            return "No tienes tareas pendientes."
        titles = ", ".join(t.get("title", "?") for t in items[:5])
        return f"Tienes {len(items)} tareas. Las primeras: {titles}."

    if intent == "tasks.add":
        return f"Tarea '{r.get('title')}' añadida."

    if intent == "tasks.complete":
        return "Tarea completada."

    if intent == "gmail.unread":
        return f"Tienes {r.get('count', 0)} correos sin leer."

    if intent == "gmail.search":
        return f"Encontré {r.get('count', 0)} mensajes."

    if intent == "gmail.send":
        return "Correo enviado."

    if intent == "gmail.mark_read":
        return "Marcado como leído."

    if intent == "gmail.archive":
        return "Archivado."

    if intent == "gmail.trash":
        return "Enviado a la papelera."

    if intent == "gmail.read":
        return f"Asunto: {r.get('subject','(sin asunto)')}. De {r.get('from','desconocido')}."

    if intent == "drive.search":
        if isinstance(r, list) and r:
            names = ", ".join(f.get("name", "?") for f in r[:5])
            return f"Encontré {len(r)} archivos: {names}."
        return "Sin resultados."

    if intent == "drive.list":
        if isinstance(r, list) and r:
            return f"Hay {len(r)} archivos en Drive."
        return "Sin archivos."

    if intent == "drive.upload":
        return f"Subido como '{r.get('name')}'."

    if intent == "drive.download":
        return f"Descargado en {r.get('path')}."

    if intent == "drive.mkdir":
        return f"Carpeta '{r.get('name')}' creada."

    if intent == "drive.share":
        return "Archivo compartido."

    if intent == "contacts.search":
        if isinstance(r, list) and r:
            names = ", ".join(c.get("display_name", "?") for c in r[:5])
            return f"Encontré {len(r)} contactos: {names}."
        return "Sin contactos."

    if intent == "contacts.create":
        return "Contacto creado."

    if intent == "google.status":
        connected = [s for s, v in r.items() if v["connected"]]
        return f"Conectado a: {', '.join(connected) or 'ninguno'}."

    return f"Hecho: {intent}."
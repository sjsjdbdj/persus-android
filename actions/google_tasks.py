"""
google_tasks.py
===============
Wrapper de Google Tasks para Mark-XLVIII.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .google_auth import build_service_for, token_has_refresh, token_path

TOKEN_PATH: Path = token_path("tasks", "primary")


def _get_tasks_service():
    return build_service_for("tasks", account="primary")


# ─────────────────────────────────────────────────────────────────────────────
# Listas
# ─────────────────────────────────────────────────────────────────────────────

def list_tasklists() -> list[dict[str, Any]]:
    svc = _get_tasks_service()
    res = svc.tasklists().list(maxResults=20).execute()
    return res.get("items", [])


def find_tasklist_id(title: str) -> str | None:
    for tl in list_tasklists():
        if tl.get("title", "").lower() == title.lower():
            return tl["id"]
    return None


def create_tasklist(title: str) -> dict[str, Any]:
    svc = _get_tasks_service()
    return svc.tasklists().insert(body={"title": title}).execute()


# ─────────────────────────────────────────────────────────────────────────────
# Tareas
# ─────────────────────────────────────────────────────────────────────────────

def list_tasks(
    *,
    tasklist_id: str = "@default",
    show_completed: bool = False,
    show_hidden: bool = False,
    max_results: int = 50,
    due_min: str | None = None,
) -> list[dict[str, Any]]:
    svc = _get_tasks_service()
    params = {
        "tasklist": tasklist_id,
        "showCompleted": show_completed,
        "showHidden": show_hidden,
        "maxResults": max_results,
    }
    if due_min:
        params["dueMin"] = due_min
    res = svc.tasks().list(**params).execute()
    return res.get("items", [])


def add_task(
    *,
    title: str,
    tasklist_id: str = "@default",
    notes: str | None = None,
    due_iso: str | None = None,
) -> dict[str, Any]:
    svc = _get_tasks_service()
    body: dict[str, Any] = {"title": title}
    if notes:
        body["notes"] = notes
    if due_iso:
        body["due"] = due_iso
    return svc.tasks().insert(tasklist=tasklist_id, body=body).execute()


def complete_task(task_id: str, tasklist_id: str = "@default") -> dict[str, Any]:
    svc = _get_tasks_service()
    return svc.tasks().patch(
        tasklist=tasklist_id,
        task=task_id,
        body={"status": "completed"},
    ).execute()


def delete_task(task_id: str, tasklist_id: str = "@default") -> None:
    svc = _get_tasks_service()
    svc.tasks().delete(tasklist=tasklist_id, task=task_id).execute()


def update_task(
    task_id: str,
    *,
    tasklist_id: str = "@default",
    title: str | None = None,
    notes: str | None = None,
    due_iso: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """Patch parcial de una tarea."""
    svc = _get_tasks_service()
    body: dict[str, Any] = {}
    if title is not None:
        body["title"] = title
    if notes is not None:
        body["notes"] = notes
    if due_iso is not None:
        body["due"] = due_iso
    if status is not None:
        body["status"] = status
    return svc.tasks().patch(
        tasklist=tasklist_id, task=task_id, body=body
    ).execute()


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard helper
# ─────────────────────────────────────────────────────────────────────────────

def summary_for_dashboard(max_items: int = 5) -> dict[str, Any]:
    """Devuelve tareas pendientes a través de todas las listas."""
    out: list[dict[str, Any]] = []
    for tl in list_tasklists():
        try:
            tasks = list_tasks(
                tasklist_id=tl["id"],
                show_completed=False,
                max_results=max(1, max_items - len(out)),
            )
        except Exception:
            continue
        for t in tasks:
            out.append({
                "id": t.get("id"),
                "title": t.get("title", "(sin título)"),
                "list":  tl.get("title", ""),
                "due":   t.get("due"),
                "notes": t.get("notes"),
            })
            if len(out) >= max_items:
                break
        if len(out) >= max_items:
            break
    return {"pending": out, "count": len(out)}
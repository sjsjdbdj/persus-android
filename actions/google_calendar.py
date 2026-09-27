"""
google_calendar.py
==================
Wrapper de Google Calendar para Mark-XLVIII.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .google_auth import build_service_for, token_has_refresh, token_path

# Variables exportadas para compatibilidad con ui.py
TOKEN_PATH: Path = token_path("calendar", "primary")
DEFAULT_TIMEZONE = "America/Bogota"


def _get_calendar_service():
    return build_service_for("calendar", account="primary")


# ─────────────────────────────────────────────────────────────────────────────
# API pública
# ─────────────────────────────────────────────────────────────────────────────

def list_events(
    *,
    time_min: datetime | None = None,
    time_max: datetime | None = None,
    max_results: int = 20,
    query: str | None = None,
) -> list[dict[str, Any]]:
    """Lista eventos entre time_min y time_max (por defecto hoy + 7 días)."""
    svc = _get_calendar_service()
    tz = timezone.utc
    params = {
        "calendarId": "primary",
        "maxResults": max_results,
        "singleEvents": True,
        "orderBy": "startTime",
        "timeMin": (time_min or datetime.now(tz)).isoformat(),
        "timeMax": (time_max or (datetime.now(tz) + timedelta(days=7))).isoformat(),
    }
    if query:
        params["q"] = query

    res = svc.events().list(**params).execute()
    return res.get("items", [])


def today_events() -> list[dict[str, Any]]:
    tz = timezone.utc
    start = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return list_events(time_min=start, time_max=end)


def upcoming_event() -> dict[str, Any] | None:
    events = list_events(max_results=1)
    return events[0] if events else None


def create_event(
    *,
    summary: str,
    start_iso: str,
    end_iso: str,
    timezone_str: str = DEFAULT_TIMEZONE,
    description: str | None = None,
    location: str | None = None,
    attendees: list[str] | None = None,
) -> dict[str, Any]:
    """Crea un evento. Las fechas pueden venir en formato ISO con offset."""
    svc = _get_calendar_service()
    body: dict[str, Any] = {
        "summary": summary,
        "start": {"dateTime": start_iso, "timeZone": timezone_str},
        "end":   {"dateTime": end_iso,   "timeZone": timezone_str},
    }
    if description:
        body["description"] = description
    if location:
        body["location"] = location
    if attendees:
        body["attendees"] = [{"email": a} for a in attendees]
    return svc.events().insert(calendarId="primary", body=body).execute()


def delete_event(event_id: str) -> None:
    svc = _get_calendar_service()
    svc.events().delete(calendarId="primary", eventId=event_id).execute()


def summary_for_dashboard(events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Resumen compacto para el panel del dashboard.

    Si se le pasa `events`, se usan como "today" sin volver a llamar a la API.
    En caso contrario, se calculan hoy + próximo internamente.
    """
    if events is None:
        today = today_events()
        upcoming = list_events(max_results=5)
    else:
        today = events
        upcoming = list_events(max_results=5)

    next_event = upcoming[0] if upcoming else None
    return {
        "today_count": len(today),
        "today": [
            {
                "id": e.get("id"),
                "title": e.get("summary", "Sin título"),
                "start": (e.get("start", {}).get("dateTime")
                          or e.get("start", {}).get("date", "")),
            }
            for e in today
        ],
        "next": next_event,
    }
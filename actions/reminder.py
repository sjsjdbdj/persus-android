import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path

def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

def reminder(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    date_str = parameters.get("date", "").strip()
    time_str = parameters.get("time", "").strip()
    message  = parameters.get("message", "Reminder").strip()

    if not date_str or not time_str:
        return "I need both a date and a time to set a reminder."

    try:
        target_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    except ValueError:
        return "I couldn't parse that date or time. Please use YYYY-MM-DD and HH:MM."

    if target_dt <= datetime.now():
        return "That time has already passed — I can't set a reminder in the past."

    # Save to memory/pending_reminders.json
    reminders_file = _base_dir() / "memory" / "pending_reminders.json"

    try:
        if reminders_file.exists():
            with open(reminders_file, "r", encoding="utf-8") as f:
                reminders = json.load(f)
        else:
            reminders = []

        if not isinstance(reminders, list):
            reminders = []

        new_reminder = {
            "id": str(uuid.uuid4()),
            "datetime_iso": target_dt.isoformat(),
            "message": message
        }
        reminders.append(new_reminder)

        with open(reminders_file, "w", encoding="utf-8") as f:
            json.dump(reminders, f, indent=4)

    except Exception as e:
        print(f"[Reminder] ❌ Failed to save reminder: {e}")
        return "Something went wrong while saving the reminder."

    if player:
        player.write_log(f"[Reminder] ✅ {date_str} {time_str} — {message[:40]}")

    friendly_time = target_dt.strftime("%B %d at %I:%M %p")
    return f"Reminder set for {friendly_time}."

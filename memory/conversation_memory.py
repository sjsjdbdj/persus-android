# memory/conversation_memory.py
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Optional

import sys

def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

HISTORY_PATH = get_base_dir() / "memory" / "conversation_history.json"
_lock = Lock()
DEFAULT_MAX_EXCHANGES = 100
MAX_SUMMARY_LENGTH = 800


def _empty_history() -> dict:
    return {
        "summary": "",
        "exchanges": [],
        "max_exchanges": DEFAULT_MAX_EXCHANGES,
    }


def load_history() -> dict:
    if not HISTORY_PATH.exists():
        return _empty_history()
    with _lock:
        try:
            data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return _empty_history()
            if "exchanges" not in data:
                data["exchanges"] = []
            if "summary" not in data:
                data["summary"] = ""
            if "max_exchanges" not in data:
                data["max_exchanges"] = DEFAULT_MAX_EXCHANGES
            return data
        except Exception as e:
            print(f"[ConversationMemory] ⚠️ Load error: {e}")
            return _empty_history()


def save_history(history: dict) -> None:
    with _lock:
        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        HISTORY_PATH.write_text(
            json.dumps(history, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


def add_exchange(role: str, text: str) -> None:
    """Añade un intercambio (usuario o asistente) al historial."""
    if not text:
        return
    history = load_history()
    max_exchanges = history.get("max_exchanges", DEFAULT_MAX_EXCHANGES)

    exchange = {
        "role": role,
        "text": text[:500],
        "timestamp": datetime.now().isoformat(),
    }
    history["exchanges"].append(exchange)

    if len(history["exchanges"]) > max_exchanges:
        history["exchanges"] = history["exchanges"][-max_exchanges:]

    save_history(history)


def update_summary(summary: str) -> None:
    """Actualiza el resumen de la conversación."""
    history = load_history()
    history["summary"] = summary[:MAX_SUMMARY_LENGTH]
    save_history(history)


def get_conversation_context(limit: int = 20) -> str:
    """
    Devuelve el contexto de la conversación para inyectar en el prompt.
    Incluye el resumen + los últimos N intercambios.
    """
    history = load_history()
    exchanges = history.get("exchanges", [])
    summary = history.get("summary", "")

    if not exchanges and not summary:
        return ""

    lines = []

    if summary:
        lines.append("[CONVERSATION SUMMARY]")
        lines.append(summary)
        lines.append("")

    if exchanges:
        lines.append("[RECENT EXCHANGES]")
        recent = exchanges[-limit:] if limit > 0 else exchanges
        for e in recent:
            role = "User" if e["role"] == "user" else "JARVIS"
            text = e["text"][:300]
            lines.append(f"{role}: {text}")

    return "\n".join(lines)


def summarize_conversation() -> str:
    """
    Genera un resumen de la conversación usando Gemini.
    Se llama periódicamente o cuando se supera cierto tamaño.
    """
    history = load_history()
    exchanges = history.get("exchanges", [])
    if not exchanges:
        return ""

    recent = exchanges[-30:]
    text = "\n".join([f"{e['role']}: {e['text']}" for e in recent])

    try:
        from core.llm_client import call_llm_text
        prompt = f"""Summarize this conversation concisely (max 5 sentences).
Focus on: who the user is, their goals, preferences, projects, and any important context.

Conversation:
{text}

Summary:"""
        summary = call_llm_text(prompt, timeout=30)
        return summary.strip()
    except Exception as e:
        print(f"[ConversationMemory] ⚠️ Summarize failed: {e}")
        return history.get("summary", "")


def auto_summarize_if_needed() -> None:
    """Si hay más de 50 intercambios, genera un resumen y limpia el historial."""
    history = load_history()
    exchanges = history.get("exchanges", [])
    if len(exchanges) < 50:
        return

    print("[ConversationMemory] 🔄 Generando resumen automático...")
    summary = summarize_conversation()

    # Guardar resumen + solo los últimos 20 intercambios
    history["summary"] = summary[:MAX_SUMMARY_LENGTH]
    history["exchanges"] = exchanges[-20:]
    save_history(history)
    print("[ConversationMemory] ✅ Resumen guardado.")


def clear_conversation_history() -> None:
    """Limpia completamente el historial de conversación."""
    save_history(_empty_history())
    print("[ConversationMemory] 🗑️ Historial borrado.")
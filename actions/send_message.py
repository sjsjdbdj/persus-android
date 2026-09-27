import json
import subprocess
import sys
import urllib.parse
from pathlib import Path

def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

def _open_url(url: str) -> bool:
    try:
        # Using termux-open-url as requested for Android/Termux environments
        subprocess.run(["termux-open-url", url], check=True)
        return True
    except Exception as e:
        print(f"[SendMessage] ⚠️ termux-open-url failed: {e}")
        return False

def _send_whatsapp(receiver: str, message: str) -> str:
    # WhatsApp deep link: https://wa.me/<number>?text=<message>
    encoded_msg = urllib.parse.quote(message)
    url = f"https://wa.me/{receiver}?text={encoded_msg}"
    if _open_url(url):
        return f"Message sent to {receiver} via WhatsApp."
    return f"Could not open WhatsApp. Link: {url}"

def _send_telegram(receiver: str, message: str) -> str:
    # Telegram deep link: https://t.me/<username>?text=<message>
    encoded_msg = urllib.parse.quote(message)
    url = f"https://t.me/{receiver}?text={encoded_msg}"
    if _open_url(url):
        return f"Message sent to {receiver} via Telegram."
    return f"Could not open Telegram. Link: {url}"

def send_message(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params       = parameters or {}
    receiver     = params.get("receiver", "").strip()
    message_text = params.get("message_text", "").strip()
    platform     = params.get("platform", "whatsapp").strip().lower()

    if not receiver:
        return "Please specify a recipient."
    if not message_text:
        return "Please specify the message content."

    preview = message_text[:50] + ("…" if len(message_text) > 50 else "")
    print(f"[SendMessage] 📨 {platform} → {receiver}: {preview}")
    if player:
        player.write_log(f"[msg] {platform} → {receiver}")

    try:
        if "whatsapp" in platform or "wp" in platform:
            result = _send_whatsapp(receiver, message_text)
        elif "telegram" in platform or "tg" in platform:
            result = _send_telegram(receiver, message_text)
        else:
            result = f"Unsupported platform: {platform}. Use WhatsApp or Telegram."
    except Exception as e:
        result = f"Could not send message: {e}"

    print(f"[SendMessage] {'✅' if 'sent' in result.lower() else '❌'} {result}")
    if player:
        player.write_log(f"[msg] {result}")

    return result

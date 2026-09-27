import asyncio
import random
import re
import threading
import time
import json
import sys
import traceback
import subprocess
from datetime import datetime
from pathlib import Path

from google import genai
from google.genai import types
from memory.memory_manager import (
    load_memory, update_memory, format_memory_for_prompt,
    save_session_summary, pop_last_session,
)
from memory.conversation_memory import (
    add_exchange,
    get_conversation_context,
    auto_summarize_if_needed,
    clear_conversation_history,
)

from actions.flight_finder     import flight_finder
from actions.weather_report    import weather_action
from actions.send_message      import send_message
from actions.reminder          import reminder
from actions.youtube_video     import youtube_video
from actions.web_search        import web_search as web_search_action
from actions.background_monitor import (
    add_monitor, remove_monitor, list_monitors, check_all as monitor_check_all,
)

def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"
# Using a text-only model for the reduced version
MODEL_NAME      = "gemini-2.0-flash"

def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]

def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are JARVIS, Tony Stark's AI assistant. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool."
        )

TOOL_DECLARATIONS = [
    {
        "name": "web_search",
        "description": (
            "Searches the web. Use for ANY question about current facts, events, prices, "
            "or topics — always prefer this over guessing. "
            "Modes: 'search' (default), 'research' (deep comprehensive answer), 'price' (product cost lookup), 'compare' (side-by-side comparison of items)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query":  {"type": "STRING", "description": "Search query or topic"},
                "mode":   {"type": "STRING", "description": "search | research | price | compare"},
                "items":  {"type": "ARRAY",  "items": {"type": "STRING"}, "description": "Items to compare (compare mode)"},
                "aspect": {"type": "STRING", "description": "Comparison aspect: price | specs | reviews | features"},
            },
            "required": ["query"]
        }
    },
    {
        "name": "weather_report",
        "description": "Gives the weather report to user",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "city": {"type": "STRING", "description": "City name"}
            },
            "required": ["city"]
        }
    },
    {
        "name": "send_message",
        "description": "Sends a text message via WhatsApp, Telegram, or other messaging platform.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "receiver":     {"type": "STRING", "description": "Recipient contact name"},
                "message_text": {"type": "STRING", "description": "The message to send"},
                "platform":     {"type": "STRING", "description": "Platform: WhatsApp, Telegram, etc."}
            },
            "required": ["receiver", "message_text", "platform"]
        }
    },
    {
        "name": "reminder",
        "description": "Sets a timed reminder.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date":    {"type": "STRING", "description": "Date in YYYY-MM-DD format"},
                "time":    {"type": "STRING", "description": "Time in HH:MM format (24h)"},
                "message": {"type": "STRING", "description": "Reminder message text"}
            },
            "required": ["date", "time", "message"]
        }
    },
    {
        "name": "youtube_video",
        "description": (
            "Controls YouTube. Use for: playing videos, summarizing a video's content, "
            "getting video info, or showing trending videos."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "play | summarize | get_info | trending (default: play)"},
                "query":  {"type": "STRING", "description": "Search query for play action"},
                "save":   {"type": "BOOLEAN", "description": "Save summary to Notepad (summarize only)"},
                "region": {"type": "STRING", "description": "Country code for trending e.g. TR, US"},
                "url":    {"type": "STRING", "description": "Video URL for get_info action"},
            },
            "required": []
        }
    },
    {
        "name": "flight_finder",
        "description": "Searches Google Flights and speaks the best options.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "origin":      {"type": "STRING",  "description": "Departure city or airport code"},
                "destination": {"type": "STRING",  "description": "Arrival city or airport code"},
                "date":        {"type": "STRING",  "description": "Departure date (any format)"},
                "return_date": {"type": "STRING",  "description": "Return date for round trips"},
                "passengers":  {"type": "INTEGER", "description": "Number of passengers (default: 1)"},
                "cabin":       {"type": "STRING",  "description": "economy | premium | business | first"},
                "save":        {"type": "BOOLEAN", "description": "Save results to Notepad"},
            },
            "required": ["origin", "destination", "date"]
        }
    },
    {
        "name": "manage_monitor",
        "description": (
            "Add, remove, or list background monitoring topics."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "add | remove | list"},
                "topic": {"type": "STRING", "description": "Topic to monitor or stop monitoring"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "google",
        "description": (
            "THE ONLY tool for ANY Google Workspace operation. "
            "Use this for Calendar, Tasks, Gmail, Drive, Contacts. "
            "Pass `intent` and `params`."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "intent": {"type": "STRING", "description": "The Google operation to perform"},
                "params": {"type": "OBJECT", "description": "Parameters for the intent"},
            },
            "required": ["intent"]
        }
    },
    {
        "name": "save_memory",
        "description": (
            "Save an important personal fact about the user to long-term memory."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {"type": "STRING", "description": "identity | preferences | projects | relationships | wishes | notes"},
                "key":   {"type": "STRING", "description": "Short snake_case key"},
                "value": {"type": "STRING", "description": "Concise value in English"},
            },
            "required": ["category", "key", "value"]
        }
    },
    {
        "name": "clear_conversation_memory",
        "description": (
            "Clears the entire conversation history."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": []
        }
    },
]

class JarvisLite:
    def __init__(self):
        self.client = genai.Client(api_key=_get_api_key())
        self.session_log = []

    def _build_prompt(self):
        memory = load_memory()
        mem_str = format_memory_for_prompt(memory)
        sys_prompt = _load_system_prompt()

        conv_context = get_conversation_context(limit=15)

        parts = []
        if mem_str: parts.append(mem_str)
        if conv_context: parts.append("\n" + conv_context)
        parts.append(sys_prompt)

        return "\n".join(parts)

    async def _execute_tool(self, tool_call):
        name = tool_call.name
        args = dict(tool_call.args or {})
        print(f"[JARVIS] 🔧 {name} {args}")

        try:
            if name == "save_memory":
                update_memory({args.get("category", "notes"): {args.get("key", ""): {"value": args.get("value", "")}}})
                return {"result": "ok", "silent": True}

            elif name == "clear_conversation_memory":
                clear_conversation_history()
                return {"result": "Conversation history cleared, sir."}

            elif name == "web_search":
                return {"result": await asyncio.to_thread(web_search_action, parameters=args)}

            elif name == "weather_report":
                return {"result": await asyncio.to_thread(weather_action, parameters=args)}

            elif name == "send_message":
                return {"result": await asyncio.to_thread(send_message, parameters=args)}

            elif name == "reminder":
                return {"result": await asyncio.to_thread(reminder, parameters=args)}

            elif name == "youtube_video":
                return {"result": await asyncio.to_thread(youtube_video, parameters=args)}

            elif name == "flight_finder":
                return {"result": await asyncio.to_thread(flight_finder, parameters=args)}

            elif name == "manage_monitor":
                action = args.get("action", "list").lower()
                topic = args.get("topic", "")
                if action == "add": return {"result": await asyncio.to_thread(add_monitor, topic)}
                if action == "remove": return {"result": await asyncio.to_thread(remove_monitor, topic)}
                if action == "list": return {"result": "Monitoring: " + ", ".join(await asyncio.to_thread(list_monitors))}
                return {"result": f"Unknown monitor action: {action}"}

            elif name == "google":
                from actions import google_workspace as _gw
                payload = _gw.google(args.get("intent"), args.get("params") or {})
                return {"result": _gw.narrate(args.get("intent"), payload)}

            else:
                return {"result": f"Unknown tool: {name}"}
        except Exception as e:
            return {"result": f"Tool {name} failed: {e}"}

    async def _check_pending_reminders(self):
        reminders_file = BASE_DIR / "memory" / "pending_reminders.json"
        if not reminders_file.exists():
            return

        try:
            with open(reminders_file, "r", encoding="utf-8") as f:
                reminders = json.load(f)

            if not isinstance(reminders, list):
                return

            now = datetime.now()
            due = []
            remaining = []

            for r in reminders:
                if datetime.fromisoformat(r["datetime_iso"]) <= now:
                    due.append(r)
                else:
                    remaining.append(r)

            if due:
                for r in due:
                    msg = r["message"]
                    print(f"\n🔔 RECORDATORIO: {msg}")
                    try:
                        subprocess.run(["termux-notification", "--title", "JARVIS", "--content", msg], check=False)
                    except Exception:
                        pass

                with open(reminders_file, "w", encoding="utf-8") as f:
                    json.dump(remaining, f, indent=4)

        except Exception as e:
            print(f"Error checking reminders: {e}")

    async def chat(self):
        print("\n--- JARVIS LITE (TEXT MODE) ---")
        print("Type 'exit' or 'quit' to stop.\n")

        while True:
            try:
                # Check reminders before asking for input
                await self._check_pending_reminders()

                user_input = input("You: ")
                if user_input.lower() in ("exit", "quit"):
                    break
                if not user_input.strip():
                    continue

                # 1. Send user input to model
                response = self.client.models.generate_content(
                    model=MODEL_NAME,
                    contents=user_input,
                    config=types.GenerateContentConfig(
                        system_instruction=self._build_prompt(),
                        tools=[{"function_declarations": TOOL_DECLARATIONS}]
                    )
                )

                # 2. Handle Tool Calls (Loop for multi-turn tools)
                while response.candidates[0].content.parts:
                    part = response.candidates[0].content.parts[0]
                    if not part.call:
                        break

                    tool_call = part.call
                    tool_result = await self._execute_tool(tool_call)

                    # Feed tool result back to model
                    response = self.client.models.generate_content(
                        model=MODEL_NAME,
                        contents=[
                            {"role": "user", "parts": [{"text": user_input}]},
                            {"role": "model", "parts": [part]},
                            {"role": "function", "parts": [{"function_response": {"name": tool_call.name, "response": tool_result}}]}
                        ],
                        config=types.GenerateContentConfig(
                            system_instruction=self._build_prompt(),
                            tools=[{"function_declarations": TOOL_DECLARATIONS}]
                        )
                    )

                # 3. Output final response
                final_text = response.text
                print(f"Jarvis: {final_text}")
                add_exchange("user", user_input)
                add_exchange("assistant", final_text)

            except Exception as e:
                print(f"Error: {e}")
                traceback.print_exc()

async def main():
    jarvis = JarvisLite()
    await jarvis.chat()

if __name__ == "__main__":
    asyncio.run(main())

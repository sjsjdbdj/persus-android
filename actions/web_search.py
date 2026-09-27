import asyncio
import json
import sys
from pathlib import Path

def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = _get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"


def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _gemini_search(query: str) -> str:
    from google import genai

    client   = genai.Client(api_key=_get_api_key())
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=query,
        config={"tools": [{"google_search": {}}]},
    )

    text = ""
    for part in response.candidates[0].content.parts:
        if hasattr(part, "text") and part.text:
            text += part.text

    text = text.strip()
    if not text:
        raise ValueError("Gemini returned an empty response.")
    return text


def _ddg_search(query: str, max_results: int = 6) -> list[dict]:
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS

    results = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=max_results):
            results.append({
                "title":   r.get("title",  ""),
                "snippet": r.get("body",   ""),
                "url":     r.get("href",   ""),
            })
    return results


def _ddg_news(query: str, max_results: int = 8) -> list[dict]:
    """DDG news search — returns actual articles, not website homepages."""
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS

    results = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.news(query, max_results=max_results):
                results.append({
                    "title":   r.get("title",  ""),
                    "snippet": r.get("body",   ""),
                    "url":     r.get("url",    ""),
                    "source":  r.get("source", ""),
                })
    except Exception as e:
        print(f"[WebSearch] ⚠️ DDG news() failed ({e}) — falling back to text search")
        results = _ddg_search(query, max_results=max_results)
    return results


def _format_ddg(query: str, results: list[dict]) -> str:
    if not results:
        return f"No results found for: {query}"

    lines = [f"Search results for: {query}\n"]
    for i, r in enumerate(results, 1):
        if r.get("title"):   lines.append(f"{i}. {r['title']}")
        if r.get("snippet"): lines.append(f"   {r['snippet']}")
        if r.get("url"):     lines.append(f"   Source: {r['url']}")
        lines.append("")
    return "\n".join(lines).strip()


# ── Parallel search (first-result-wins) ────────────────────────────────────────

async def _gemini_search_async(query: str) -> str:
    """Async wrapper around _gemini_search."""
    return await asyncio.to_thread(_gemini_search, query)


async def _ddg_search_async(query: str, max_results: int = 6) -> str:
    """Async wrapper around _ddg_search + formatting."""
    def _run():
        results = _ddg_search(query, max_results=max_results)
        return _format_ddg(query, results)
    return await asyncio.to_thread(_run)


async def _parallel_search(query: str, max_results: int = 6) -> str:
    """
    Run Gemini and DDG in parallel; return whichever finishes first with content.
    Falls back to the other if the winner returns empty/error.
    """
    gemini_task = asyncio.create_task(_gemini_search_async(query))
    ddg_task    = asyncio.create_task(_ddg_search_async(query, max_results))

    pending = {gemini_task, ddg_task}
    while pending:
        done, pending = await asyncio.wait(
            pending, return_when=asyncio.FIRST_COMPLETED
        )
        for task in done:
            try:
                result = task.result()
                if result and not result.startswith("No results"):
                    # Cancel the loser, return the winner
                    for t in pending:
                        t.cancel()
                    return result
            except Exception:
                continue

    # Both failed — return whatever error info we have
    try:
        return await gemini_task
    except Exception:
        pass
    try:
        return await ddg_task
    except Exception as e:
        return f"Search failed: {e}"


# ── Modes ──────────────────────────────────────────────────────────────────────

def _search(query: str) -> str:
    """Default search — Gemini grounded and DDG run in parallel; first valid wins."""
    try:
        return asyncio.run(_parallel_search(query))
    except RuntimeError:
        # Already in an event loop — fallback to sequential
        try:
            return _gemini_search(query)
        except Exception as e:
            print(f"[WebSearch] ⚠️ Gemini failed ({e}) — trying DDG...")
            results = _ddg_search(query)
            return _format_ddg(query, results)
    except Exception as e:
        print(f"[WebSearch] ⚠️ Parallel search failed ({e}) — sequential fallback")
        try:
            return _gemini_search(query)
        except Exception:
            results = _ddg_search(query)
            return _format_ddg(query, results)


def _research(query: str) -> str:
    """
    Deep dive — asks Gemini for a comprehensive answer with context.
    Falls back to a wider DDG fetch.
    """
    research_query = (
        f"Comprehensive, detailed explanation of: {query}. "
        "Include background context, key facts, current state, and important nuances."
    )
    try:
        return asyncio.run(_parallel_search(research_query, max_results=10))
    except RuntimeError:
        try:
            return _gemini_search(research_query)
        except Exception as e:
            print(f"[WebSearch] ⚠️ Research Gemini failed ({e}) — DDG fallback...")
            results = _ddg_search(query, max_results=10)
            return _format_ddg(query, results)
    except Exception as e:
        print(f"[WebSearch] �️ Parallel research failed ({e}) — sequential fallback")
        try:
            return _gemini_search(research_query)
        except Exception:
            results = _ddg_search(query, max_results=10)
            return _format_ddg(query, results)


def _price(query: str) -> str:
    """Product price lookup — searches for current market prices."""
    price_query = f"current price of {query} — how much does it cost today"
    try:
        return asyncio.run(_parallel_search(price_query, max_results=6))
    except RuntimeError:
        try:
            return _gemini_search(price_query)
        except Exception as e:
            print(f"[WebSearch] ⚠️ Price Gemini failed ({e}) — DDG fallback...")
            results = _ddg_search(f"{query} price buy", max_results=6)
            return _format_ddg(query, results)
    except Exception as e:
        print(f"[WebSearch] ⚠️ Parallel price failed ({e}) — sequential fallback")
        try:
            return _gemini_search(price_query)
        except Exception:
            results = _ddg_search(f"{query} price buy", max_results=6)
            return _format_ddg(query, results)


def _compare(items: list[str], aspect: str) -> str:
    query = (
        f"Compare {', '.join(items)} in terms of {aspect}. "
        "Give specific facts and data."
    )
    try:
        return _gemini_search(query)
    except Exception as e:
        print(f"[WebSearch] ⚠️ Gemini compare failed: {e} — falling back to DDG")

    all_results: dict[str, list] = {}
    for item in items:
        try:
            all_results[item] = _ddg_search(f"{item} {aspect}", max_results=3)
        except Exception:
            all_results[item] = []

    lines = [f"Comparison — {aspect.upper()}", "─" * 40]
    for item in items:
        lines.append(f"\n▸ {item}")
        for r in all_results.get(item, [])[:2]:
            if r.get("snippet"):
                lines.append(f"  • {r['snippet']}")
            if r.get("url"):
                lines.append(f"    {r['url']}")
    return "\n".join(lines)


# ── Public entry point ─────────────────────────────────────────────────────────

def web_search(
    parameters:     dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    query  = params.get("query", "").strip()
    mode   = params.get("mode",  "search").lower().strip()
    items  = params.get("items", [])
    aspect = params.get("aspect", "general").strip() or "general"

    if not query and not items:
        return "Please provide a search query."

    if items and mode not in ("compare",):
        mode = "compare"

    if player:
        player.write_log(f"[Search:{mode}] {query or ', '.join(items)}")

    print(f"[WebSearch] 🔍 mode={mode!r}  query={query!r}")

    try:
        if mode == "compare" and items:
            return _compare(items, aspect)
        if mode == "research":
            return _research(query)
        if mode == "price":
            return _price(query)
        return _search(query)

    except Exception as e:
        print(f"[WebSearch] ❌ All backends failed: {e}")
        return f"Search failed: {e}"
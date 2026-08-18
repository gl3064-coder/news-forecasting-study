from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any

from ..db import get_connection
from .markets import TICKERS, fetch_markets
from .summaries import _call_anthropic, _extract_json, content_hash


PERIODS = ["1d", "1w", "1m", "1y"]

_CACHE: dict[str, dict[str, Any]] = {}
_CACHE_TTL_SECONDS = 60 * 30  # 30 min
_LOCK = threading.Lock()

_STATE_KEY = "chart_summaries"


def _load_persisted() -> dict[str, Any] | None:
    """Return the last successfully-generated payload from sqlite, or None."""
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT value_json FROM app_state WHERE key = ?", (_STATE_KEY,)
            ).fetchone()
    except Exception as exc:
        print(f"[chart_summaries] db load failed: {exc}", flush=True)
        return None
    if not row:
        return None
    try:
        return json.loads(row[0])
    except Exception:
        return None


def _save_persisted(payload: dict[str, Any]) -> None:
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO app_state(key, value_json, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, "
                "updated_at=excluded.updated_at",
                (_STATE_KEY, json.dumps(payload),
                 datetime.now(timezone.utc).isoformat()),
            )
    except Exception as exc:
        print(f"[chart_summaries] db save failed: {exc}", flush=True)


def _cache_key(markets_payload: dict[str, Any], newsletter_summaries: list[dict[str, Any]]) -> str:
    # Hash only what's in the prompt. Was [:20] but the chart prompt only
    # uses newsletter_summaries[:15] (see nl_lines build below). Mismatched
    # slicing meant a newsletter at position 16-20 would invalidate this
    # cache without changing what the LLM actually sees.
    #
    # Round prices to whole units and change_pct to 0.5% buckets so trivial
    # tick-by-tick price noise doesn't invalidate the cache. The CHART STORY
    # doesn't change because NQ ticked from 17234.56 to 17234.61. Only
    # meaningful directional moves should re-trigger Sonnet commentary.
    def _round_price(v) -> str:
        try:
            return str(int(round(float(v))))
        except (TypeError, ValueError):
            return str(v)
    def _round_pct(v) -> str:
        try:
            # Bucket into 0.5% steps — 1.27% and 1.31% hash the same, but
            # 1.27% vs 1.85% triggers a regen.
            return f"{round(float(v) * 2) / 2:.1f}"
        except (TypeError, ValueError):
            return str(v)
    market_sig = "|".join(
        f"{t.get('symbol','?')}:{_round_price(t.get('last'))}:{_round_pct(t.get('change_pct'))}"
        for t in markets_payload.get("tickers", [])
    )
    nl_ids = "|".join(s.get("story_id") or s.get("title", "") for s in newsletter_summaries[:15])
    return content_hash(market_sig, nl_ids)


def _empty_payload() -> dict[str, Any]:
    return {"updated_at": datetime.now(timezone.utc).isoformat(), "summaries": {}}


def get_chart_summaries(
    markets_payload: dict[str, Any] | None = None,
    newsletter_summaries: list[dict[str, Any]] | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """Returns {symbol: {period: {bullets, sources}}} explaining what drives each
    ticker at each timeframe, grounded in newsletter summaries when possible."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return _empty_payload()

    if markets_payload is None:
        markets_payload = fetch_markets()
    if newsletter_summaries is None:
        newsletter_summaries = []

    key = _cache_key(markets_payload, newsletter_summaries)
    now = time.time()
    with _LOCK:
        cached = _CACHE.get(key)
        if cached and not refresh and (now - cached.get("_ts", 0)) < _CACHE_TTL_SECONDS:
            return cached["payload"]

    # Cold in-memory cache (e.g. server just restarted): return the last
    # persisted payload immediately so the user sees commentary without waiting
    # 30-60s for a fresh Sonnet call. The hourly cron and /newsletters/sync
    # regenerate fresh in the background. refresh=True bypasses this and
    # forces a synchronous regeneration.
    if not refresh:
        persisted = _load_persisted()
        if persisted:
            with _LOCK:
                _CACHE[key] = {"_ts": now, "payload": persisted}
            return persisted

    # Build news context — let Claude pick what's relevant per (ticker, period)
    nl_lines: list[str] = []
    source_index: list[str] = []
    for i, item in enumerate(newsletter_summaries[:15]):
        title = item.get("title", "Untitled")
        ref = f'Source {i+1}: "{title}"'
        source_index.append(ref)
        summary = (item.get("summary") or "")[:300]
        nl_lines.append(f"{ref}\n{summary}")

    ticker_lines = [
        f"- {t.get('symbol')} ({t.get('label')}): last {t.get('last','?')}, change "
        f"{t.get('change','?')} ({t.get('change_pct','?')}%)"
        for t in markets_payload.get("tickers", [])
        if not t.get("error")
    ]
    if not ticker_lines:
        return _empty_payload()

    symbols_in_play = [t.get("symbol") for t in markets_payload.get("tickers", []) if not t.get("error")]

    system = (
        "You are explaining market moves for Gavin — NYU Stern freshman who day-trades NQ. "
        "For each ticker, you produce FOUR distinct bullet lists, one per timeframe (1D, 1W, 1M, 1Y), "
        "calibrated to what ACTUALLY drives that timeframe:\n"
        "  • 1D — today's intraday catalysts: specific headlines, data prints, order-flow events\n"
        "  • 1W — this week's themes: 2-3 dominant narratives shaping the last 5 trading days\n"
        "  • 1M — monthly positioning: macro setup, sector rotation, central-bank tone, seasonality\n"
        "  • 1Y — structural backdrop: secular trends, rate-regime, supply/demand, multi-quarter forces\n\n"
        "Rules:\n"
        "  • Bullets, not prose. 2-3 bullets per (ticker, period). Each bullet ONE crisp sentence.\n"
        "  • Cite specific newsletter titles in the 'sources' array when a bullet is grounded in one.\n"
        "  • For 1M/1Y, if no newsletter directly covers it, lean on your pretrained market knowledge "
        "    AND start that bullet with 'General knowledge:' so the user knows it's not source-cited.\n"
        "  • No jargon walls — explain mechanisms in plain English.\n"
        "  • Each (ticker, period) ALSO returns a `plain_english` field — 1 sentence translating "
        "    the bullets into how you'd explain it to a smart non-finance friend. No jargon. "
        "    Empty string if the bullets are already plain English.\n"
        "  • CRITICAL: Respond with ONLY the raw JSON object. Do NOT wrap in ```json fences. "
        "    Do NOT add any prose before or after. Start with { and end with }."
    )

    prompt = (
        "Today's market snapshot:\n"
        + "\n".join(ticker_lines)
        + "\n\nAVAILABLE SOURCES (copy titles EXACTLY into the sources arrays):\n"
        + "\n".join(source_index)
        + "\n\nNEWSLETTER SUMMARIES:\n"
        + "\n\n".join(nl_lines)
        + "\n\nReturn JSON with this exact shape (one entry per ticker symbol above, "
        "each with all four periods):\n"
        "{\n"
        '  "summaries": {\n'
        '    "NQ=F": {\n'
        '      "1d": {"bullets": ["...", "..."], "sources": ["exact newsletter title", ...], "plain_english": "1-sentence non-jargon version"},\n'
        '      "1w": {"bullets": [...], "sources": [...], "plain_english": "..."},\n'
        '      "1m": {"bullets": [...], "sources": [...], "plain_english": "..."},\n'
        '      "1y": {"bullets": [...], "sources": [...], "plain_english": "..."}\n'
        '    },\n'
        '    "ES=F": { ... },\n'
        "    ... one entry for every ticker above ...\n"
        "  }\n"
        "}"
    )

    # Use Haiku for chart commentary — Sonnet's depth isn't needed for "what
    # moved this ticker today" framing. ~3-5x cost reduction per call.
    model = os.getenv("PULSE_SUMMARY_MODEL", "claude-haiku-4-5-20251001")
    try:
        text = _call_anthropic(system, prompt, model=model, max_tokens=12000)
    except Exception as exc:
        print(f"[chart_summaries] anthropic failed: {exc}", flush=True)
        return _empty_payload()
    if not text:
        return _empty_payload()

    parsed = _extract_json(text)
    if not parsed or not isinstance(parsed.get("summaries"), dict):
        print(
            f"[chart_summaries] JSON parse failed; len={len(text)}; "
            f"head={text[:200]!r}; tail={text[-200:]!r}",
            flush=True,
        )
        return _empty_payload()

    cleaned: dict[str, Any] = {}
    for sym in symbols_in_play:
        entry = parsed["summaries"].get(sym)
        if not isinstance(entry, dict):
            continue
        per_period: dict[str, Any] = {}
        for period in PERIODS:
            block = entry.get(period)
            if not isinstance(block, dict):
                per_period[period] = {"bullets": [], "sources": []}
                continue
            bullets = block.get("bullets") or []
            if isinstance(bullets, str):
                bullets = [bullets]
            bullets = [str(b).strip() for b in bullets if b]
            plain_english = block.get("plain_english", "")
            plain_english = str(plain_english).strip() if isinstance(plain_english, str) else ""
            sources = block.get("sources") or []
            if isinstance(sources, str):
                sources = [sources]
            sources = [str(s) for s in sources if s]
            per_period[period] = {"bullets": bullets, "sources": sources, "plain_english": plain_english}
        cleaned[sym] = per_period

    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "summaries": cleaned,
    }
    with _LOCK:
        _CACHE[key] = {"_ts": now, "payload": payload}
    _save_persisted(payload)
    return payload

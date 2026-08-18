from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any

from ..db import get_connection
from .summaries import _call_anthropic, _extract_json, content_hash


_STATE_KEY = "spotlight"


def _load_from_db() -> list[tuple[str, str, str]]:
    """Hydrate the in-memory cache from the app_state table on first access.
    Returns [] if there's nothing stored yet."""
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT value_json FROM app_state WHERE key = ?", (_STATE_KEY,)
            ).fetchone()
    except Exception as exc:
        print(f"[spotlight] db load failed: {exc}", flush=True)
        return []
    if not row:
        return []
    try:
        items = json.loads(row[0])
    except Exception:
        return []
    out: list[tuple[str, str, str]] = []
    for it in items if isinstance(items, list) else []:
        if isinstance(it, dict):
            sym = str(it.get("symbol", "")).strip()
            label = str(it.get("label", "")).strip()
            desc = str(it.get("desc", "")).strip()
            if sym and label:
                out.append((sym, label, desc))
    return out


def _save_to_db(items: list[tuple[str, str, str]]) -> None:
    payload = json.dumps([
        {"symbol": sym, "label": label, "desc": desc} for sym, label, desc in items
    ])
    ts = datetime.now(timezone.utc).isoformat()
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO app_state(key, value_json, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, "
                "updated_at=excluded.updated_at",
                (_STATE_KEY, payload, ts),
            )
    except Exception as exc:
        print(f"[spotlight] db save failed: {exc}", flush=True)


# Candidate pool — Claude chooses from these based on today's coverage.
# (symbol, label, default description)
TICKER_POOL: dict[str, tuple[str, str]] = {
    "BTC-USD":  ("Bitcoin",        "Spot Bitcoin price — leading crypto and risk-on/off barometer."),
    "ETH-USD":  ("Ethereum",       "Spot Ethereum price — second-largest crypto, smart-contract platform."),
    "NVDA":     ("Nvidia",         "Largest designer of AI-accelerator GPUs — the AI bellwether stock."),
    "SMH":      ("Semiconductors", "VanEck Semiconductor ETF — broad chip-industry tracker (NVDA, TSM, AVGO, etc.)."),
    "TLT":      ("Long Bonds",     "iShares 20+ Year Treasury Bond ETF — duration play on long rates."),
    "^VIX":     ("VIX",            "CBOE Volatility Index — implied 30-day S&P 500 volatility ('fear gauge')."),
    "USO":      ("Oil ETF",        "United States Oil Fund — retail-accessible WTI crude exposure."),
    "EURUSD=X": ("EUR/USD",        "Euro priced in dollars — primary FX cross, ECB vs Fed differential."),
    "USDJPY=X": ("USD/JPY",        "Dollar priced in yen — sensitive to BOJ policy + US yield spreads."),
    "USDCNY=X": ("USD/CNY",        "Dollar priced in offshore yuan — China policy + trade signal."),
    "META":     ("Meta",           "Meta Platforms — Instagram + Facebook + WhatsApp ad-driven mega-cap."),
    "GOOGL":    ("Alphabet",       "Alphabet — Google search and ads, plus AI infrastructure."),
    "TSLA":     ("Tesla",          "Tesla — EVs, energy, autonomy; high-beta retail favorite."),
    "AAPL":     ("Apple",          "Apple — largest consumer-tech mega-cap, services + hardware."),
    "MSFT":     ("Microsoft",      "Microsoft — Azure cloud, M365, OpenAI partnership."),
    "AMZN":     ("Amazon",         "Amazon — e-commerce + AWS cloud."),
    "XLE":      ("Energy Sector",  "Energy Select Sector SPDR — large US oil & gas equities."),
    "XLF":      ("Financials",     "Financial Select Sector SPDR — banks, insurance, asset managers."),
    "XLK":      ("Tech Sector",    "Technology Select Sector SPDR — broad US tech equities."),
    "XLV":      ("Healthcare",     "Health Care Select Sector SPDR — pharma, devices, providers."),
    "GLD":      ("Gold ETF",       "SPDR Gold Shares — retail-accessible gold exposure."),
    "SLV":      ("Silver",         "iShares Silver Trust — precious metal with industrial demand."),
    "^RUT":     ("Russell 2000",   "Small-cap US equity index — domestic-economy sensitive."),
    "HG=F":     ("Copper",         "Copper futures — 'Dr. Copper', industrial demand bellwether."),
    "NG=F":     ("Natural Gas",    "Henry Hub natural gas futures — heating + power generation."),
}

MAX_SPOTLIGHT = 3

_SPOTLIGHT: list[tuple[str, str, str]] | None = None  # None = not yet hydrated
_LAST_CONTENT_HASH: str | None = None  # hash of newsletter universe that triggered last successful pick
_LOCK = threading.Lock()


def _spotlight_content_hash(newsletter_summaries: list[dict[str, Any]]) -> str:
    """Hash of the newsletter universe — if unchanged, no need to re-ask
    Claude which tickers matter. Mirrors the content-hash pattern used by
    chart_summaries + briefing."""
    nl_ids = "|".join(
        (s.get("story_id") or s.get("title", ""))
        for s in newsletter_summaries[:20]
    )
    return content_hash(nl_ids)


def get_current_spotlight() -> list[tuple[str, str, str]]:
    """Returns the current spotlight, hydrating from db on first call after
    process start. Survives server restarts since the picks are persisted."""
    global _SPOTLIGHT
    with _LOCK:
        if _SPOTLIGHT is None:
            _SPOTLIGHT = _load_from_db()
        return list(_SPOTLIGHT)


def refresh_spotlight(newsletter_summaries: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    """Asks Claude to pick up to MAX_SPOTLIGHT tickers from TICKER_POOL
    that are directly relevant to today's newsletter coverage.

    Content-hash gated: if the newsletter universe is identical to the one
    that produced the current pick, we skip the LLM call entirely and reuse
    the cached spotlight. Saves ~$0.05 per skipped call × hourly cron.
    """
    global _SPOTLIGHT, _LAST_CONTENT_HASH

    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key or not newsletter_summaries:
        return get_current_spotlight()

    # Content-hash cache — skip the Sonnet call when newsletters are unchanged.
    # _SPOTLIGHT is hydrated lazily via get_current_spotlight, so we trigger
    # that path on cold-start to populate it before short-circuiting.
    current = get_current_spotlight()
    new_hash = _spotlight_content_hash(newsletter_summaries)
    if new_hash == _LAST_CONTENT_HASH and current:
        return current

    pool_lines = [f"  {sym}: {label} — {desc}" for sym, (label, desc) in TICKER_POOL.items()]
    nl_lines = [
        f'- "{item.get("title","?")}": {(item.get("summary") or "")[:220]}'
        for item in newsletter_summaries[:20]
    ]

    system = (
        "You are curating which extra tickers to spotlight on a financial dashboard "
        "based on today's actual news coverage. Be selective — only pick tickers whose "
        "price action is directly influenced by stories in the newsletters. "
        "Skip if nothing else really matters today. Respond with ONLY valid JSON."
    )
    prompt = (
        "Already always-visible on the dashboard (do NOT pick these — they're shown by default):\n"
        "  NQ=F (Nasdaq 100), ES=F (S&P 500), CL=F (WTI Crude), GC=F (Gold), ^TNX (10Y Yield), DX-Y.NYB (Dollar Index)\n\n"
        "Today's newsletter coverage:\n" + "\n".join(nl_lines) +
        "\n\nAVAILABLE EXTRA TICKERS to choose from:\n" + "\n".join(pool_lines) +
        f"\n\nReturn up to {MAX_SPOTLIGHT} tickers MOST directly relevant to today's news. "
        "If only 1 or 2 are clearly relevant, return only that many. If nothing else really matters, return [].\n\n"
        'Return JSON: {"spotlight": [{"symbol": "EXACT SYMBOL FROM LIST", "reason": "1 short sentence linking it to specific news"}, ...]}'
    )

    try:
        # Use Haiku — picking 3 tickers from a list of 25 is well within Haiku's
        # reasoning. ~5x cost reduction per call vs Sonnet.
        text = _call_anthropic(
            system, prompt,
            model=os.getenv("PULSE_SUMMARY_MODEL", "claude-haiku-4-5-20251001"),
            max_tokens=900,
        )
    except Exception as exc:
        print(f"[spotlight] anthropic failed: {exc}", flush=True)
        return get_current_spotlight()
    if not text:
        return get_current_spotlight()

    parsed = _extract_json(text)
    if not parsed or not isinstance(parsed.get("spotlight"), list):
        print("[spotlight] JSON parse failed", flush=True)
        return get_current_spotlight()

    chosen: list[tuple[str, str, str]] = []
    for entry in parsed["spotlight"][:MAX_SPOTLIGHT]:
        if not isinstance(entry, dict):
            continue
        sym = str(entry.get("symbol", "")).strip()
        if sym not in TICKER_POOL:
            continue
        if any(c[0] == sym for c in chosen):  # no dupes
            continue
        label, base_desc = TICKER_POOL[sym]
        reason = str(entry.get("reason", "")).strip()
        # Prefer the LLM's reason as the description — it ties to today's news
        final_desc = reason if reason else base_desc
        chosen.append((sym, label, final_desc))

    with _LOCK:
        _SPOTLIGHT = chosen
    _save_to_db(chosen)
    # Record the content hash AFTER successful pick so next hour's cron with
    # identical newsletter universe skips the Sonnet call.
    _LAST_CONTENT_HASH = new_hash
    print(f"[spotlight] chose {len(chosen)} ticker(s): {[c[0] for c in chosen]} (persisted)", flush=True)
    return chosen

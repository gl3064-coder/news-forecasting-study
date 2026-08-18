from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any

import yfinance as yf


# Always-visible core. (symbol, display label, one-line description)
CORE_TICKERS: list[tuple[str, str, str]] = [
    ("NQ=F",     "Nasdaq 100",   "Futures on the 100 largest non-financial Nasdaq companies — tech-heavy US equity benchmark."),
    ("ES=F",     "S&P 500",      "Futures on the S&P 500 — the broadest gauge of US large-cap stocks."),
    ("CL=F",     "WTI Crude",    "West Texas Intermediate crude oil futures — the US benchmark for oil prices."),
    ("GC=F",     "Gold",         "COMEX gold futures — the global price benchmark for the safe-haven metal."),
    ("^TNX",     "10Y Yield",    "The 10-year US Treasury yield — anchor long rate that re-prices discount rates across the economy."),
    ("DX-Y.NYB", "Dollar Index", "Measures the US dollar against a basket of six major currencies (EUR, JPY, GBP, CAD, SEK, CHF)."),
]

# Backwards-compat alias — old call sites still work
TICKERS = CORE_TICKERS


def get_active_tickers() -> list[tuple[str, str, str]]:
    """Returns the current ticker set: always-visible core + dynamic spotlight."""
    # Lazy import to avoid circular dependency (spotlight imports from summaries)
    from .spotlight import get_current_spotlight
    return CORE_TICKERS + get_current_spotlight()


def get_core_symbols() -> set[str]:
    return {sym for sym, _, _ in CORE_TICKERS}

# Period key → (yfinance period, yfinance interval)
PERIOD_MAP: dict[str, tuple[str, str]] = {
    "1d": ("1d", "15m"),
    "1w": ("5d", "1h"),
    "1m": ("1mo", "1d"),
    "1y": ("1y", "1wk"),
}
DEFAULT_PERIOD = "1d"

_CACHE: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_CACHE_TTL_SECONDS = 300  # 5 min — yfinance rate-limits if hammered
_LOCK = threading.Lock()


def _label_for(symbol: str) -> str:
    for sym, lbl, _ in get_active_tickers():
        if sym == symbol:
            return lbl
    return symbol


def _fetch_one(symbol: str, label: str, period_key: str) -> dict[str, Any]:
    """Pull OHLC for one symbol at the given period. Returns frontend-ready dict."""
    yf_period, yf_interval = PERIOD_MAP.get(period_key, PERIOD_MAP[DEFAULT_PERIOD])
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period=yf_period, interval=yf_interval, auto_adjust=False)
        if hist.empty:
            # Fallback: wider window if intraday is empty (e.g., on weekends/holidays)
            hist = ticker.history(period="5d", interval="1d", auto_adjust=False)
        if hist.empty:
            return {"symbol": symbol, "label": label, "period": period_key, "error": "no data"}

        closes = [float(v) for v in hist["Close"].tolist() if v == v]
        timestamps = [t.isoformat() for t in hist.index.to_pydatetime()]
        last = closes[-1]
        first = closes[0]
        change = last - first
        change_pct = (change / first * 100) if first else 0.0

        return {
            "symbol": symbol,
            "label": label,
            "period": period_key,
            "last": round(last, 2),
            "change": round(change, 2),
            "change_pct": round(change_pct, 2),
            "series": [
                {"t": t, "c": round(c, 2)}
                for t, c in zip(timestamps, closes)
            ],
        }
    except Exception as exc:
        return {"symbol": symbol, "label": label, "period": period_key, "error": str(exc)[:120]}


def fetch_one_ticker(symbol: str, period: str = DEFAULT_PERIOD, force: bool = False) -> dict[str, Any]:
    """Single-symbol fetch with custom period. Used by per-card time-horizon switching."""
    if period not in PERIOD_MAP:
        period = DEFAULT_PERIOD
    cache_key = (symbol, period)
    now = time.time()
    with _LOCK:
        cached = _CACHE.get(cache_key)
        if cached and not force and (now - cached[0]) < _CACHE_TTL_SECONDS:
            return cached[1]
    data = _fetch_one(symbol, _label_for(symbol), period)
    with _LOCK:
        _CACHE[cache_key] = (now, data)
    return data


def fetch_markets(force: bool = False, period: str = DEFAULT_PERIOD) -> dict[str, Any]:
    """All configured tickers at one period. Cached for _CACHE_TTL_SECONDS."""
    if period not in PERIOD_MAP:
        period = DEFAULT_PERIOD
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "period": period,
        "tickers": [fetch_one_ticker(sym, period=period, force=force) for sym, _, _ in get_active_tickers()],
    }

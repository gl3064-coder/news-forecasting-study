import logging
import threading
import time
import os
import base64
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _bootstrap_secret_files() -> None:
    """Materialize secret files (credentials.json, token.json) from base64 env vars.
    Lets cloud deploys store secrets in the platform's secrets manager instead of git."""
    pairs = [
        ("GMAIL_CREDENTIALS_B64", os.getenv("GMAIL_CREDENTIALS_FILE", "credentials.json")),
        ("GMAIL_TOKEN_B64", os.getenv("GMAIL_TOKEN_FILE", "token.json")),
    ]
    for env_key, file_path in pairs:
        b64 = os.getenv(env_key, "").strip()
        if not b64:
            continue
        p = Path(file_path)
        if p.exists():
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(base64.b64decode(b64))


_bootstrap_secret_files()


def time_ago(pub_date_str: str) -> str:
    try:
        dt = datetime.fromisoformat(pub_date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        diff = datetime.now(timezone.utc) - dt
        seconds = int(diff.total_seconds())
        if seconds < 3600:
            m = max(1, seconds // 60)
            return f"{m} minute{'s' if m != 1 else ''} ago"
        elif seconds < 86400:
            h = seconds // 3600
            return f"{h} hour{'s' if h != 1 else ''} ago"
        else:
            d = seconds // 86400
            return f"{d} day{'s' if d != 1 else ''} ago"
    except Exception:
        return pub_date_str or "Unknown time"


def clean_source(icon: str, source: str) -> str:
    # Avoid "NYT NYT Newsletter" — if source already starts with icon, skip icon
    if source.upper().startswith(icon.upper()):
        return source
    return f"{icon} {source}"

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from .db import init_db
import os

from .services.briefing import get_or_build_briefing
from .services.gmail import auto_label_newsletters, get_connection, load_newsletters, purge_old_newsletters, send_email, sync_newsletters, target_label
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .services.rss import load_dashboard_data
from .services.markets import fetch_markets, fetch_one_ticker, get_active_tickers, get_core_symbols
from .services.chart_summaries import get_chart_summaries
from .services.spotlight import refresh_spotlight
from .services.summaries import (
    get_latest_cached_analysis,
    get_or_build_overarching_analysis,
    summarize_stories,
    summary_runtime_status,
)
from .services import cron_health
from .services import dossiers as dossier_service


app = FastAPI(title="Pulse Backend")
init_db()


def wrap_jargon_in_html(html_str: str, jargon: list) -> str:
    """Wrap each jargon term with a hover-tooltip span (custom CSS popover via
    `data-tip`, not the browser-native `title` attribute — feels responsive +
    matches Pulse's editorial aesthetic).

    **Idempotent:** if the input already contains `<span class="jargon">…</span>`
    wrappers, text inside them is skipped — so calling this function twice on
    the same string doesn't produce nested spans (which were the source of the
    "<span class=" tooltip-content bug).

    Module-level so the markets route + home route can both call it.
    """
    import re as _re
    if not jargon or not html_str:
        return html_str
    valid = [
        (j["match"], j["plain"])
        for j in jargon
        if isinstance(j, dict) and j.get("match") and j.get("plain")
    ]
    if not valid:
        return html_str
    # Longest match first so "yield curve" wins over "yield"
    valid.sort(key=lambda x: len(x[0]), reverse=True)
    used: set[str] = set()
    parts = _re.split(r"(<[^>]+>)", html_str)

    # Track nesting depth inside `<span class="jargon">…</span>`. Any text
    # chunk while depth > 0 is skipped (it's already wrapped).
    in_jargon_depth = 0
    _jargon_open = _re.compile(r'^<span\b[^>]*\bclass="[^"]*\bjargon\b[^"]*"', _re.IGNORECASE)

    for i, chunk in enumerate(parts):
        if not chunk:
            continue
        if chunk.startswith("<"):
            # Tag — track open/close of jargon spans, don't wrap
            if _jargon_open.match(chunk):
                in_jargon_depth += 1
            elif chunk.lower().startswith("</span>") and in_jargon_depth > 0:
                in_jargon_depth -= 1
            continue
        if in_jargon_depth > 0:
            # Text inside an existing jargon span — leave alone
            continue
        for match, plain in valid:
            key = match.lower()
            if key in used:
                continue
            pattern = _re.compile(r"\b" + _re.escape(match) + r"\b", _re.IGNORECASE)
            m = pattern.search(chunk)
            if m:
                start, end = m.start(), m.end()
                original = chunk[start:end]
                tip_safe = (
                    plain.replace('"', '&quot;')
                         .replace("**", "")
                         .replace("<", "&lt;")
                         .replace(">", "&gt;")
                )
                chunk = (
                    chunk[:start]
                    + f'<span class="jargon" data-tip="{tip_safe}">{original}</span>'
                    + chunk[end:]
                )
                parts[i] = chunk
                used.add(key)
    return "".join(parts)


# Consecutive sync failures before the one-time alert email fires. 2 means a
# lone transient error (network blip) stays quiet, but a persistent breakage
# alerts within two cron ticks.
_SYNC_ALERT_THRESHOLD = 2


def _scheduled_sync() -> None:
    # Every step is caught so one failure never crashes the scheduler — but
    # each failure is LOUD. The 2026-07-14 wrong-Gmail-account incident hid
    # behind bare `except: pass` here for hours; never again.
    try:
        purge_old_newsletters(days=7)
    except Exception as exc:
        print(f"[cron] purge_old_newsletters failed: {exc}", flush=True)
    try:
        auto_label_newsletters()
    except Exception as exc:
        print(f"[cron] auto_label_newsletters failed: {exc}", flush=True)
    try:
        sync_newsletters(force=False)
        cron_health.record_sync_success()
    except Exception as exc:
        print(f"[cron] sync_newsletters failed: {exc}", flush=True)
        state = cron_health.record_sync_failure(str(exc))
        # One email exactly at the threshold crossing: a single blip stays
        # quiet, repeats don't spam (counter keeps climbing past 2), and a
        # recovery resets the counter so the next breakage alerts again.
        # Best-effort: if Gmail is so broken that send fails too, /health
        # and the [cron] logs remain the signal.
        if state["consecutive_failures"] == _SYNC_ALERT_THRESHOLD:
            try:
                send_email(
                    subject="Pulse alert: newsletter sync is failing",
                    html_body=(
                        f"<p>Newsletter sync has failed {state['consecutive_failures']} "
                        f"times in a row. Newsletters will go stale until this is fixed.</p>"
                        f"<p>Last error: <code>{state['last_error']}</code></p>"
                        f"<p>Check /health on the dashboard for current status. "
                        f"A successful manual Sync clears this warning.</p>"
                    ),
                )
                print("[cron] sync-failure alert email sent", flush=True)
            except Exception as mail_exc:
                print(f"[cron] sync-failure alert email failed: {mail_exc}", flush=True)
    # Run the LLM chain (summaries, briefing, spotlight, chart commentary,
    # dossier snapshots) so the dashboard auto-updates without a manual Sync.
    # Daemon thread so the cron tick returns immediately.
    try:
        threading.Thread(target=_background_summarize, daemon=True).start()
    except Exception as exc:
        print(f"[cron] failed to launch background summarize: {exc}", flush=True)


# Throttled cron in America/New_York:
#   Mon-Fri at 8/11/14/17 ET  — covers market open/mid/close + a morning catch
#   Sat-Sun at 8 ET only      — catches weekend morning newsletters; no need
#                                to keep polling during quiet weekend hours
# Manual ↻ Sync from the dashboard still works anytime regardless of schedule.
_scheduler = BackgroundScheduler(timezone="America/New_York")
_scheduler.add_job(
    _scheduled_sync,
    "cron",
    day_of_week="mon-fri",
    hour="8,11,14,17",
    minute=0,
    id="weekday_chain",
)
_scheduler.add_job(
    _scheduled_sync,
    "cron",
    day_of_week="sat,sun",
    hour=9,
    minute=0,
    id="weekend_morning",
)


def _scheduled_program_watch() -> None:
    """Weekly program-deadline watch. Daemon thread so the cron tick returns."""
    def _run() -> None:
        try:
            from .services.deadlines import run_and_alert
            run_and_alert()
        except Exception as exc:
            print(f"[cron] program watch failed: {exc}", flush=True)
    threading.Thread(target=_run, daemon=True).start()


_scheduler.add_job(
    _scheduled_program_watch,
    "cron",
    day_of_week="mon",
    hour=9,
    minute=0,
    id="program_watch",
)
_scheduler.start()


def _next_scheduled_refresh_iso() -> str:
    """Returns ISO timestamp of the next scheduled chain run, or '' if no jobs.
    Used by the dashboard top-bar to show a 'Next refresh: ...' countdown."""
    try:
        next_times = [j.next_run_time for j in _scheduler.get_jobs() if j.next_run_time]
        if not next_times:
            return ""
        return min(next_times).isoformat()
    except Exception:
        return ""


def _startup_refresh() -> None:
    """Fire one full sync + LLM chain shortly after server boot so the dashboard
    is fresh immediately on restart, without waiting up to 1 hour for the cron.
    Daemon thread with a short delay so the FastAPI app finishes booting first
    and the initial /health / /api/markets requests serve fast.
    """
    import time as _time
    _time.sleep(5)
    print("[startup] firing initial sync + LLM chain", flush=True)
    try:
        _scheduled_sync()
    except Exception as exc:
        print(f"[startup] initial sync failed: {exc}", flush=True)


# Guard lets the test suite import app.main without firing a real sync run
# 5 seconds later (which would race the cron-health assertions).
if not os.getenv("PULSE_SKIP_STARTUP_REFRESH"):
    threading.Thread(target=_startup_refresh, daemon=True).start()


@app.get("/health")
def health() -> dict[str, Any]:
    from app import vault as _vault
    vault_state: dict[str, Any] = {"enabled": _vault.sync.enabled()}
    last = _vault.sync.last_result()
    if last:
        vault_state["last_sync"] = last.pulled_at.isoformat()
        vault_state["last_sync_success"] = last.success
        vault_state["last_sync_sha"] = last.sha[:7] if last.sha else ""
        if last.error:
            vault_state["last_sync_error"] = last.error
    vault_state["notes_loaded"] = len(_vault.index.titles())
    # Newsletter-sync health: a broken cron sync (wrong token, missing label,
    # Gmail API outage) flips status to "degraded" so the dashboard or a
    # monitoring check sees it instead of newsletters silently going stale.
    sync_state: dict[str, Any] = cron_health.sync_health()
    status = "ok"
    failures = sync_state.get("consecutive_failures", 0)
    if failures > 0:
        status = "degraded"
        sync_state["warning"] = (
            f"newsletter sync failing ({failures} consecutive "
            f"failure{'s' if failures != 1 else ''}): {sync_state.get('last_error', '')}"
        )
    return {"status": status, "vault": vault_state, "sync": sync_state}


@app.post("/api/vault/refresh")
def vault_refresh() -> dict[str, Any]:
    """Manual trigger to pull the vault and rebuild the index. Returns the
    sync result + post-rebuild note count. Useful when you've pushed new
    notes and don't want to wait for the next hourly sync."""
    from app import vault as _vault
    if not _vault.sync.enabled():
        return {"success": False, "error": "vault disabled (PULSE_VAULT_REPO_URL unset)"}
    result = _vault.sync.pull()
    if result.success:
        _vault.index.rebuild()
    return {
        "success": result.success,
        "sha": result.sha[:7] if result.sha else "",
        "pulled_at": result.pulled_at.isoformat(),
        "error": result.error,
        "notes_loaded": len(_vault.index.titles()),
    }


@app.get("/dashboard")
def dashboard(refresh_briefing: bool = False, refresh_summaries: bool = False) -> dict:
    newsletters = load_newsletters()
    rss_stories = load_dashboard_data()
    briefing = get_or_build_briefing(newsletters, rss_stories, refresh=refresh_briefing)
    newsletter_summaries = summarize_stories(newsletters, refresh=refresh_summaries)
    overarching_analysis = get_or_build_overarching_analysis(
        newsletter_summaries, rss_stories, refresh=refresh_briefing or refresh_summaries
    )
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "stories": newsletters + rss_stories,
        "newsletters": newsletters,
        "rss": rss_stories,
        "briefing": briefing,
        "newsletter_summaries": newsletter_summaries,
        "overarching_analysis": overarching_analysis,
    }


@app.get("/newsletters/label")
@app.post("/newsletters/label")
def label_newsletters_route() -> dict:
    return auto_label_newsletters()


def _background_summarize() -> None:
    try:
        print("[bg] starting background summarize", flush=True)
        # Refresh the vault first so all downstream LLM calls see the same
        # vault snapshot. Failure is logged but does not stop the chain.
        try:
            from app import vault as _vault
            if _vault.sync.enabled():
                pull_result = _vault.sync.pull()
                if pull_result.success:
                    _vault.index.rebuild()
                    print(f"[bg] vault refreshed @ {pull_result.sha[:7]} "
                          f"({len(_vault.index.titles())} notes)", flush=True)
                else:
                    print(f"[bg] vault pull failed, using prior index: {pull_result.error}", flush=True)
            # If disabled, no log line — silent no-op so dev environments
            # without a vault repo aren't noisy.
        except Exception as exc:
            print(f"[bg] vault refresh ERROR (continuing): {exc}", flush=True)
        newsletters = load_newsletters()
        rss_stories = load_dashboard_data()
        newsletter_summaries = summarize_stories(newsletters, refresh=False)
        print("[bg] story summaries done, running analysis", flush=True)
        # refresh=False so the content-hash cache actually fires — if newsletters
        # haven't changed since the last run, no Sonnet call is made. Was True
        # before, which bypassed the cache every hour and burned ~$0.21/run on
        # identical inputs.
        get_or_build_overarching_analysis(newsletter_summaries, rss_stories, refresh=False)
        print("[bg] overarching analysis done", flush=True)
        try:
            refresh_spotlight(newsletter_summaries)
            print("[bg] spotlight refreshed", flush=True)
        except Exception as exc:
            print(f"[bg] spotlight ERROR: {exc}", flush=True)
        try:
            # refresh=False so chart-summaries cache check kicks in — was True
            # before, costing ~$0.25/run × hourly cron even when market data +
            # newsletters were unchanged.
            get_chart_summaries(
                markets_payload=fetch_markets(),
                newsletter_summaries=newsletter_summaries,
                refresh=False,
            )
            print("[bg] chart summaries done", flush=True)
        except Exception as exc:
            print(f"[bg] chart summaries ERROR: {exc}", flush=True)
        try:
            for e in dossier_service.list_followed():
                dossier_service.maybe_refresh_snapshot(e["id"])
            print("[bg] dossier snapshots checked", flush=True)
        except Exception as exc:
            print(f"[bg] dossier refresh ERROR: {exc}", flush=True)
    except Exception as exc:
        import traceback
        print(f"[bg] ERROR: {exc}", flush=True)
        traceback.print_exc()


@app.get("/newsletters/sync")
@app.post("/newsletters/sync")
def sync_newsletters_route(force: bool = False) -> dict:
    # Manual sync feeds the same health counter as the cron — so fixing the
    # underlying issue and hitting ↻ Sync clears the /health warning.
    try:
        result = sync_newsletters(force=force)
    except Exception as exc:
        cron_health.record_sync_failure(str(exc))
        raise
    cron_health.record_sync_success()
    threading.Thread(target=_background_summarize, daemon=True).start()
    result["label"] = target_label()
    result["status"] = "synced — summaries updating in background"
    return result


@app.get("/newsletters/status")
def newsletters_status() -> dict:
    newsletters = load_newsletters(limit=5)
    return {
        "label": target_label(),
        "count": len(load_newsletters(limit=500)),
        "latest": newsletters,
        "summary_runtime": summary_runtime_status(),
    }


@app.post("/api/program-watch/run")
def program_watch_run(dry_run: bool = False) -> dict:
    """Manual trigger. dry_run=true runs the watch without sending email —
    returns what WOULD be alerted. Used for live verification."""
    from .services.deadlines import run_watch, run_and_alert
    if dry_run:
        return {"dry_run": True, **run_watch(advance_snapshots=False)}
    return run_and_alert()


@app.get("/api/program-watch/status")
def program_watch_status() -> dict:
    """Read-only view of the watch table for sanity checks."""
    from .db import get_connection
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT name, url, last_checked_at, last_status, last_error FROM program_watch ORDER BY name"
        ).fetchall()
    return {"programs": [dict(r) for r in rows]}


@app.get("/debug/model-test")
def debug_model_test() -> dict:
    """Make a tiny test call to the model API and surface any error."""
    import os, requests as _requests
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    results = {}

    if anthropic_key:
        try:
            r = _requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": anthropic_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": os.getenv("PULSE_SUMMARY_MODEL", "claude-haiku-4-5-20251001"), "max_tokens": 30, "messages": [{"role": "user", "content": "Say OK"}]},
                timeout=20,
            )
            results["anthropic"] = {"status": r.status_code, "ok": r.ok, "body": r.json()}
        except Exception as e:
            results["anthropic"] = {"error": str(e)}
    else:
        results["anthropic"] = {"skipped": "no key"}

    if openai_key:
        try:
            r = _requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
                json={"model": os.getenv("PULSE_OPENAI_MODEL", "gpt-4o-mini"), "max_tokens": 10, "messages": [{"role": "user", "content": "Say OK"}]},
                timeout=20,
            )
            results["openai"] = {"status": r.status_code, "ok": r.ok, "body": r.json()}
        except Exception as e:
            results["openai"] = {"error": str(e)}
    else:
        results["openai"] = {"skipped": "no key"}

    return results


@app.get("/debug/summaries")
def debug_summaries() -> dict:
    newsletters = load_newsletters(limit=5)
    summaries = summarize_stories(newsletters, refresh=False)
    return {
        "runtime": summary_runtime_status(),
        "engines": [item.get("engine_label", item.get("engine", "unknown")) for item in summaries],
        "titles": [item.get("title", "Untitled") for item in summaries],
        "tags": [item.get("tags", []) for item in summaries],
    }


import re as _re
def _src_str(val) -> str:
    if isinstance(val, list):
        parts = []
        for s in val:
            if not s or isinstance(s, dict):
                continue
            cleaned = _re.sub(r'^Source\s+\d+:\s*"?', '', str(s)).rstrip('"').strip()
            if cleaned:
                parts.append(f"<span class='src-badge'>{cleaned}</span>")
        return " ".join(parts)
    if isinstance(val, dict):
        return ""
    # Strip prefix from plain strings too
    s = str(val) if val else ""
    return _re.sub(r'Source\s+\d+:\s*"?', '', s).rstrip('"').strip()


@app.get("/api/markets")
def markets_route(force: bool = False, period: str = "1d") -> dict:
    return fetch_markets(force=force, period=period)


@app.get("/api/markets/summaries")
def market_summaries_route(refresh: bool = False) -> dict:
    newsletters = load_newsletters(limit=50)
    newsletter_summaries = summarize_stories(newsletters)
    payload = get_chart_summaries(
        markets_payload=fetch_markets(),
        newsletter_summaries=newsletter_summaries,
        refresh=refresh,
    )
    # Post-process: wrap any finance jargon in the bullets + plain_english
    # paragraphs with hover-tooltip spans, using the same vocab list the
    # briefing was built on so terminology is consistent across the dashboard.
    try:
        # Borrow the briefing's vocab as the jargon dictionary
        from .services.summaries import get_latest_cached_analysis
        latest = get_latest_cached_analysis() or {}
        chart_jargon = [
            {"match": v.get("term", ""), "plain": v.get("plain_english") or v.get("definition", "")}
            for v in (latest.get("vocab") or [])
            if isinstance(v, dict) and v.get("term") and (v.get("plain_english") or v.get("definition"))
        ]
        if chart_jargon:
            summaries = payload.get("summaries") or {}
            for sym, periods in summaries.items():
                if not isinstance(periods, dict):
                    continue
                for period, block in periods.items():
                    if not isinstance(block, dict):
                        continue
                    block["bullets"] = [
                        wrap_jargon_in_html(b, chart_jargon)
                        for b in block.get("bullets", [])
                    ]
                    if block.get("plain_english"):
                        block["plain_english"] = wrap_jargon_in_html(block["plain_english"], chart_jargon)
    except Exception as exc:
        print(f"[markets/summaries] jargon wrap failed: {exc}", flush=True)
    return payload


@app.get("/api/markets/{symbol:path}")
def market_one_route(symbol: str, period: str = "1d", force: bool = False) -> dict:
    return fetch_one_ticker(symbol, period=period, force=force)


@app.get("/api/dossiers")
def list_dossiers_route() -> dict:
    return {"followed": dossier_service.list_followed()}


@app.get("/api/dossiers/discover")
def discover_dossiers_route() -> dict:
    return {"candidates": dossier_service.list_discover()}


@app.get("/api/dossiers/{entity_id}")
def get_dossier_route(entity_id: int, offset: int = 0, limit: int = 20) -> dict:
    from .db import get_connection as _get_connection
    with _get_connection() as conn:
        row = conn.execute(
            "SELECT id, kind, key, name, followed FROM entities WHERE id=?",
            (entity_id,),
        ).fetchone()
    if not row:
        from fastapi import HTTPException
        raise HTTPException(404, "entity not found")
    # Snapshot only needs to be computed on the first page; subsequent
    # "Show more" requests skip the LLM regen path entirely.
    if offset == 0:
        snapshot = dossier_service.get_snapshot(entity_id)
        if snapshot is None:
            # Don't block the GET on a Sonnet call — the follow_route already
            # kicks off snapshot regen in a background thread. Returning null
            # lets the frontend show a "Analyzing…" state and poll until ready,
            # so the user sees instant navigation instead of a 5-10s freeze.
            pass
        elif "jargon" not in snapshot:
            # Lazy upgrade: snapshot was generated before the `jargon` field
            # existed. Regenerate once so multi-word phrase tooltips work in
            # this dossier's bull/bear/overview text. Use `not in` rather
            # than a truthy check so a legitimate empty list from Sonnet
            # ("no jargon worth highlighting") doesn't fire a wasted regen
            # on every dossier GET.
            try:
                fresh = dossier_service.regenerate_snapshot(entity_id)
                if fresh:
                    snapshot = fresh
            except Exception as exc:
                print(f"[dossiers/{entity_id}] lazy jargon upgrade failed: {exc}", flush=True)
    else:
        snapshot = None
    total = dossier_service.count_mentions(entity_id)
    mentions = dossier_service.list_mentions(entity_id, limit=limit, offset=offset)

    # Wrap jargon hover-tooltips in snapshot text + mention bullets — combines
    # the briefing's vocab list with ALL per-newsletter jargon lists so a
    # dossier whose snapshot uses different jargon than the briefing still
    # gets hover tooltips. Dedupe by lowercased `match` so we don't double-wrap.
    try:
        from .services.summaries import get_latest_cached_analysis
        from .db import get_connection as _gc
        import json as _json
        latest = get_latest_cached_analysis() or {}
        combined: dict[str, str] = {}
        # SNAPSHOT-LEVEL jargon FIRST (this dossier's own phrases, e.g. "a
        # model-sharing agreement with the U.S. Commerce Department" that
        # only Sonnet's snapshot text contains).
        if isinstance(snapshot, dict):
            for j in (snapshot.get("jargon") or []):
                if isinstance(j, dict) and j.get("match") and j.get("plain"):
                    k = j["match"].lower().strip()
                    if k and k not in combined:
                        combined[k] = j["plain"]
        # Then briefing-level vocab (shared across the dashboard)
        for v in (latest.get("vocab") or []):
            if isinstance(v, dict) and v.get("term") and (v.get("plain_english") or v.get("definition")):
                k = v["term"].lower().strip()
                if k and k not in combined:
                    combined[k] = (v.get("plain_english") or v.get("definition"))
        # Then pull jargon from every story summary
        with _gc() as _conn:
            _story_rows = _conn.execute(
                "SELECT payload_json FROM story_summaries WHERE engine NOT IN ('heuristic', '')"
            ).fetchall()
        for _sr in _story_rows:
            try:
                payload = _json.loads(_sr[0])
            except Exception:
                continue
            for j in payload.get("jargon") or []:
                if not isinstance(j, dict):
                    continue
                m = (j.get("match") or "").strip()
                p = (j.get("plain") or "").strip()
                if m and p and m.lower() not in combined:
                    combined[m.lower()] = p
        dossier_jargon = [{"match": m, "plain": p} for m, p in combined.items()]
        if dossier_jargon:
            if snapshot:
                for f in ("overview", "plain_english", "bull_thesis", "bear_thesis"):
                    if isinstance(snapshot.get(f), str) and snapshot[f]:
                        snapshot[f] = wrap_jargon_in_html(snapshot[f], dossier_jargon)
            for m in mentions:
                if isinstance(m.get("bullets"), list):
                    m["bullets"] = [
                        wrap_jargon_in_html(b, dossier_jargon) if isinstance(b, str) else b
                        for b in m["bullets"]
                    ]
                if isinstance(m.get("quote"), str) and m["quote"]:
                    m["quote"] = wrap_jargon_in_html(m["quote"], dossier_jargon)
    except Exception as exc:
        print(f"[dossiers/{entity_id}] jargon wrap failed: {exc}", flush=True)

    return {
        "entity": {"id": row[0], "kind": row[1], "key": row[2],
                   "name": row[3], "followed": bool(row[4])},
        "snapshot": snapshot,
        "mentions": mentions,
        "total_mentions": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + limit < total,
    }


@app.post("/api/dossiers/follow")
def follow_route(payload: dict) -> dict:
    kind = (payload.get("kind") or "").strip()
    key = (payload.get("key") or "").strip()
    name = (payload.get("name") or key).strip()
    if kind not in {"company", "sector", "concept"} or not key:
        from fastapi import HTTPException
        raise HTTPException(400, "kind must be company/sector/concept and key required")
    eid = dossier_service.upsert_entity(kind, key, name)
    dossier_service.follow(eid)
    # Backfill + snapshot are slow (substring scan + Haiku per match + Sonnet).
    # Run them in a background thread so the click navigates to the new dossier
    # page immediately. The frontend polls /api/dossiers/{id} until snapshot
    # lands, so the page populates without a manual refresh.
    aliases = (name, key)
    def _follow_warmup(entity_id: int, _aliases: tuple[str, ...]) -> None:
        try:
            dossier_service.backfill_mentions_for_entity(entity_id, aliases=_aliases)
        except Exception as exc:
            print(f"[follow {entity_id}] backfill failed: {exc}", flush=True)
        try:
            dossier_service.regenerate_snapshot(entity_id)
        except Exception as exc:
            print(f"[follow {entity_id}] snapshot regen failed: {exc}", flush=True)
    threading.Thread(target=_follow_warmup, args=(eid, aliases), daemon=True).start()
    return {"id": eid, "kind": kind, "key": key, "name": name, "status": "follow_pending"}


@app.post("/api/dossiers/{entity_id}/unfollow")
def unfollow_route(entity_id: int) -> dict:
    dossier_service.unfollow(entity_id)
    return {"id": entity_id, "followed": False}


@app.post("/api/dossiers/{entity_id}/refresh")
def refresh_dossier_route(entity_id: int) -> dict:
    snapshot = dossier_service.regenerate_snapshot(entity_id)
    return {"id": entity_id, "snapshot": snapshot}


@app.post("/admin/dossiers/backfill-bullets")
def backfill_bullets_route(limit: int | None = None) -> dict:
    """One-time migration: populate bullets_json on legacy mention rows.
    Idempotent — only touches rows where bullets_json IS NULL.
    """
    return dossier_service.backfill_bullets_for_existing_mentions(limit=limit)


@app.post("/admin/dossiers/reprocess-empties")
def reprocess_empties_route(limit: int | None = None) -> dict:
    """Sonnet re-pass over mentions where Haiku returned '[]' (passing
    reference). Idempotent — leaves rows '[]' if Sonnet also returns empty.
    """
    return dossier_service.reprocess_empty_bullets_with_sonnet(limit=limit)


@app.post("/admin/dossiers/prune-titleless-archived")
def prune_titleless_archived_route() -> dict:
    """Delete legacy mentions that can only render as "(archived newsletter)"
    — newsletter row purged AND no persisted subject. Regenerates snapshots
    for any affected followed entity so the bull/bear theses don't keep
    citing rows that no longer exist.
    """
    result = dossier_service.delete_titleless_archived_mentions()
    refreshed: list[dict] = []
    for entry in result["by_entity"]:
        if dossier_service.is_followed(entry["entity_id"]):
            try:
                dossier_service.regenerate_snapshot(entry["entity_id"])
                refreshed.append({"key": entry["key"], "name": entry["name"]})
            except Exception as exc:
                print(f"[dossiers] snapshot regen failed for {entry['key']}: {exc}", flush=True)
    result["snapshots_refreshed"] = refreshed
    return result


@app.post("/admin/jargon/backfill")
def jargon_backfill_route(limit: int | None = None, force: bool = False) -> dict:
    """Backfill jargon (term + plain-English definition) on story_summaries.
    Default: only fills rows missing jargon. Pass `?force=true` to re-extract
    everything — use after a prompt change to refresh phrase-level extraction.
    """
    from .services.summaries import backfill_jargon_for_existing_summaries
    return backfill_jargon_for_existing_summaries(limit=limit, force=force)


@app.post("/admin/dossiers/dedupe")
def dedupe_route() -> dict:
    """One-shot cleanup: purge test-leak mentions, dedupe (entity, newsletter)
    pairs, create UNIQUE INDEX. Idempotent — safe to re-run.
    """
    return dossier_service.dedupe_mentions_and_purge_test_data()


@app.post("/admin/dossiers/dedupe-case-entities")
def dedupe_case_entities_route() -> dict:
    """Collapse case-collision entities (e.g. 'OpenAI' and 'openai' both existing).
    Reassigns mentions to the winner row, deletes losers. Idempotent.
    """
    return dossier_service.dedupe_case_collision_entities()


@app.post("/admin/dossiers/dedupe-same-name")
def dedupe_same_name_route() -> dict:
    """Collapse same-name / different-key entities (e.g. Cerebras = CEREBRAS +
    CREBR + CBRS, Adani Group = ADANIGROUP + adani_group). Caused by the LLM
    inventing multiple keys for the same conceptual entity. Idempotent.
    """
    return dossier_service.dedupe_same_name_entities()


@app.post("/admin/dossiers/prune-empties")
def prune_empties_route() -> dict:
    """Delete mention rows whose bullets_json = '[]' and regenerate the
    snapshot for every affected followed entity so the bull/bear theses
    reflect the cleaned mention set.
    """
    result = dossier_service.prune_empty_mentions()
    refreshed: list[dict] = []
    for entry in result["by_entity"]:
        if dossier_service.is_followed(entry["entity_id"]):
            try:
                dossier_service.regenerate_snapshot(entry["entity_id"])
                refreshed.append({"key": entry["key"], "name": entry["name"]})
            except Exception as exc:
                print(f"[dossiers] snapshot regen failed for {entry['key']}: {exc}", flush=True)
    result["snapshots_refreshed"] = refreshed
    return result


@app.get("/api/analysis-ts")
def analysis_ts() -> dict:
    with get_connection() as conn:
        row = conn.execute("SELECT updated_at FROM briefings ORDER BY updated_at DESC LIMIT 1").fetchone()
    return {"ts": row["updated_at"] if row else ""}


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    newsletters = load_newsletters(limit=50)
    # Cache-only summary fetch — home() MUST never block on Haiku. Anything
    # without a cached summary falls back to the cheap heuristic. The hourly
    # cron + the startup hook + the manual Sync button all populate the cache;
    # the page-render path stays interactive.
    from .services.summaries import (
        load_cached_story_summary, story_cache_key, heuristic_story_summary,
    )
    newsletter_summaries = []
    for story in newsletters:
        sid, h = story_cache_key(story)
        cached = load_cached_story_summary(sid, h)
        if cached and cached.get("engine") not in ("heuristic", None, ""):
            newsletter_summaries.append(cached)
        else:
            newsletter_summaries.append(heuristic_story_summary(story))
    overarching_analysis = get_latest_cached_analysis()
    if not overarching_analysis:
        try:
            overarching_analysis = get_or_build_overarching_analysis(newsletter_summaries, [])
        except Exception as exc:
            print(f"[home] briefing build failed, rendering stub: {exc}", flush=True)
            overarching_analysis = {
                "title": "Pulse Daily Analysis",
                "tldr": "Briefing is rebuilding — refresh in a moment.",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
    def _to_text(v) -> str:
        if isinstance(v, list):
            return " ".join(str(i) for i in v if i and not isinstance(i, dict))
        if isinstance(v, dict):
            return " ".join(str(x) for x in v.values() if x)
        return str(v) if v else ""
    for _f in ("what_happened", "why_markets_move", "watch_today", "bull_case", "bear_case", "nq_game_plan", "stern_angle", "tldr"):
        v = overarching_analysis.get(_f)
        if not isinstance(v, str):
            overarching_analysis[_f] = _to_text(v) if v else ""
    # Pre-build a lookup of vault atomic-note one-liners by lowercased title.
    # Used by md_inline below to populate the data-tip on vault-ref spans
    # so hover/tap shows the actual definition from the user's vault, not
    # just a generic "From your vault" label.
    from app import vault as _vault_for_tips
    vault_tips_dict = {t.title.lower(): t.one_liner for t in _vault_for_tips.index.titles()}

    def md_inline(s: str) -> str:
        # **bold** → <strong>bold</strong>, leave other text alone.
        # [[Wikilink]] → subtle styled span with data-tip from the vault note.
        # Reuses the same tooltip CSS + tap-to-toggle JS as .jargon so hover
        # (desktop) or tap (touch) reveals the atomic-note one-liner.
        # Handles [[Term]] and [[Term|Alias]] forms.
        s = _re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        def _render_vault_ref(m):
            term = m.group(1)
            alias = m.group(2)
            display = alias or term
            tip = vault_tips_dict.get(term.lower(), "From your Obsidian vault.")
            # Strip [[wikilinks]] from inside the tooltip text — they'd render
            # as literal '[[Discount Rate]]' chars inside the popover otherwise.
            # The atomic-note one-liner often links to sibling concepts.
            tip = _re.sub(r"\[\[([^\]|]+?)(?:\|([^\]]+?))?\]\]",
                          lambda mm: mm.group(2) or mm.group(1), tip)
            tip_safe = (
                tip.replace("&", "&amp;")
                   .replace('"', "&quot;")
                   .replace("<", "&lt;")
                   .replace(">", "&gt;")
            )
            return f'<span class="vault-ref" data-tip="{tip_safe}">{display}</span>'
        s = _re.sub(r"\[\[([^\]|]+?)(?:\|([^\]]+?))?\]\]", _render_vault_ref, s)
        return s

    wrap_jargon = wrap_jargon_in_html  # local alias for clarity inside home()

    def strip_leading_bullet(s: str) -> str:
        s = s.strip()
        for marker in ("- ", "• ", "* "):
            if s.startswith(marker):
                return s[len(marker):].strip()
        return s

    def render_summary_body(s: str) -> str:
        s = s.strip()
        lines = [ln.strip() for ln in s.splitlines() if ln.strip()]
        is_bullets = len(lines) >= 2 and all(ln.startswith(("- ", "• ", "* ")) for ln in lines)
        if is_bullets:
            items = "".join(f"<li>{md_inline(ln[2:].strip())}</li>" for ln in lines)
            return f"<ul class='summary-bullets'>{items}</ul>"
        # Single-line case: strip any stray leading bullet marker so we don't render a lone "-"
        return f"<p>{md_inline(strip_leading_bullet(s))}</p>"

    newsletter_cards = []
    for story, story_summary in zip(newsletters, newsletter_summaries):
        summary = story_summary["summary"].strip()
        plain_english = story_summary.get("plain_english", "").strip()
        main_points = story_summary.get("main_points", [])
        why_it_matters = story_summary.get("why_it_matters", "").strip()
        market_impact = story_summary.get("market_impact", "").strip()
        framing = story_summary.get("framing", "").strip()
        plain_why = story_summary.get("plain_english_why_it_matters", "").strip()
        plain_impact = story_summary.get("plain_english_market_impact", "").strip()
        plain_framing = story_summary.get("plain_english_framing", "").strip()
        jargon_list = story_summary.get("jargon") or []
        engine_label = story_summary.get("engine_label", story_summary.get("engine", "unknown"))

        def _field_plain(text: str) -> str:
            """Indented single-line Plain Language sub-text under a field, no callout box."""
            return (
                f"<div class='field-plain'>↳ <span class='field-plain-label'>Plain language:</span> {md_inline(text)}</div>"
                if text else ""
            )

        def _wrap(html_str: str) -> str:
            return wrap_jargon(html_str, jargon_list)

        details = []
        if main_points:
            details.append("<div class='why'><strong>Main points:</strong><ul>" + "".join(f"<li>{_wrap(md_inline(strip_leading_bullet(str(point))))}</li>" for point in main_points) + "</ul></div>")
        if why_it_matters and why_it_matters not in summary:
            details.append("<div class='desc'><strong>Why it matters:</strong> " + _wrap(md_inline(why_it_matters)) + _field_plain(plain_why) + "</div>")
        if market_impact and market_impact not in summary and market_impact != why_it_matters:
            details.append("<div class='desc'><strong>Market impact:</strong> " + _wrap(md_inline(market_impact)) + _field_plain(plain_impact) + "</div>")
        if framing and framing not in summary and framing != why_it_matters and framing != market_impact:
            details.append("<div class='desc'><strong>Source framing:</strong> " + _wrap(md_inline(framing)) + _field_plain(plain_framing) + "</div>")
        tags = story_summary.get("tags", [])
        source_key = story["sourceIcon"].upper()
        tag_data = " ".join(tags + [source_key])
        tags_html = "".join(f'<span class="tag tag-{t.lower().replace("/","-")}">{t}</span>' for t in tags)
        summary_html = _wrap(render_summary_body(summary))
        plain_html = (
            f"<div class='plain-english'><span class='pe-label'>Plain English</span> {md_inline(plain_english)}</div>"
            if plain_english else ""
        )
        newsletter_cards.append(
            f"""
            <article class="card newsletter" data-tags="{tag_data}">
              <div class="meta">
                <span class="pill">{clean_source(story["sourceIcon"], story["source"])}</span>
                <span class="cat">newsletter</span>
                {tags_html}
              </div>
              <h2><a href="https://mail.google.com/mail/u/gl3064@stern.nyu.edu/#all/{story.get('emailId', '')}" target="_blank" rel="noreferrer">{story["title"]}</a></h2>
              {summary_html}
              {plain_html}
              {"<details class='expand'><summary class='expand-btn'>More</summary><div class='expand-body'>" + "".join(details) + "</div></details>" if details else ""}
              <div class="date">{time_ago(story.get("pubDate", ""))}</div>
            </article>
            """
        )

    newsletter_html = (
        "\n".join(newsletter_cards)
        if newsletter_cards
        else "<p>No Gmail newsletters synced yet.</p>"
    )

    # Briefing-level jargon = the vocab list. Each vocab entry already has a
    # term + contextual plain_english definition, which is exactly what
    # wrap_jargon expects. Adapt {term, plain_english} → {match, plain}.
    briefing_jargon = [
        {"match": v.get("term", ""), "plain": v.get("plain_english") or v.get("definition", "")}
        for v in (overarching_analysis.get("vocab") or [])
        if isinstance(v, dict) and v.get("term") and (v.get("plain_english") or v.get("definition"))
    ]

    def render_briefing_block(css_class: str, label: str, text: str, sources_html: str, plain_english: str = "") -> str:
        if not text:
            return ""
        body = wrap_jargon_in_html(render_summary_body(text), briefing_jargon)
        src = (
            f"<div class='block-src'>Sources: {_src_str(sources_html)}</div>"
            if sources_html else ""
        )
        pe = (
            f"<div class='plain-english block-plain'><span class='pe-label'>Plain English</span> {md_inline(plain_english)}</div>"
            if plain_english else ""
        )
        return f"<div class='analysis-block {css_class}'><strong>{label}</strong>{body}{pe}{src}</div>"

    what_html = render_briefing_block("what", "What happened",
        overarching_analysis.get("what_happened", ""),
        overarching_analysis.get("what_happened_sources", ""),
        overarching_analysis.get("plain_english_what_happened", ""))
    mkts_html = render_briefing_block("mkts", "Why markets move",
        overarching_analysis.get("why_markets_move", ""),
        overarching_analysis.get("why_markets_move_sources", ""),
        overarching_analysis.get("plain_english_why_markets_move", ""))
    watch_html = render_briefing_block("watch", "Watch today",
        overarching_analysis.get("watch_today", ""),
        overarching_analysis.get("watch_today_sources", ""),
        overarching_analysis.get("plain_english_watch_today", ""))
    bull_html = render_briefing_block("bull", "Bull case",
        overarching_analysis.get("bull_case", ""),
        overarching_analysis.get("bull_case_sources", ""),
        overarching_analysis.get("plain_english_bull_case", ""))
    bear_html = render_briefing_block("bear", "Bear case",
        overarching_analysis.get("bear_case", ""),
        overarching_analysis.get("bear_case_sources", ""),
        overarching_analysis.get("plain_english_bear_case", ""))
    bull_bear_html = (
        f"<div class='analysis-grid'>{bull_html}{bear_html}</div>"
        if overarching_analysis.get("bull_case") or overarching_analysis.get("bear_case")
        else ""
    )
    nq_html = render_briefing_block("nq", "NQ game plan",
        overarching_analysis.get("nq_game_plan", ""),
        overarching_analysis.get("nq_game_plan_sources", ""),
        overarching_analysis.get("plain_english_nq_game_plan", ""))
    stern_html = render_briefing_block("stern", "Stern angle",
        overarching_analysis.get("stern_angle", ""), "",
        overarching_analysis.get("plain_english_stern_angle", ""))

    plain_tldr = (overarching_analysis.get("plain_english_tldr") or "").strip()
    plain_tldr_html = (
        f"<div class='plain-english plain-tldr'><span class='pe-label'>Plain English</span> {md_inline(plain_tldr)}</div>"
        if plain_tldr else ""
    )

    # Markets section — placeholder cards; JS hydrates with live yfinance data + AI summaries
    period_btns = "".join(
        f"<button class='mkt-period{ ' active' if p == '1d' else ''}' data-period='{p}'>{lbl}</button>"
        for p, lbl in [("1d", "1D"), ("1w", "1W"), ("1m", "1M"), ("1y", "1Y")]
    )
    active_tickers = get_active_tickers()
    core_syms = get_core_symbols()
    markets_cards = "".join(
        f"""<div class='mkt-card{" spotlight" if sym not in core_syms else ""}' data-symbol='{sym}' data-period='1d'>
              {"<div class='mkt-spotlight-eyebrow'>On Watch</div>" if sym not in core_syms else ""}
              <div class='mkt-head'>
                <span class='mkt-label'>{label}</span>
                <span class='mkt-symbol'>{sym}</span>
              </div>
              <div class='mkt-desc'>{desc}</div>
              <div class='mkt-price-row'>
                <span class='mkt-price'>—</span>
                <span class='mkt-change'>loading</span>
              </div>
              <div class='mkt-chart-wrap'><canvas class='mkt-chart'></canvas></div>
              <div class='mkt-period-row'>{period_btns}</div>
              <div class='mkt-summary' data-symbol='{sym}'>
                <span class='mkt-summary-loading'>Loading commentary…</span>
              </div>
            </div>"""
        for sym, label, desc in active_tickers
    )
    markets_html = f"""
      <div class='markets-grid' id='marketsGrid'>{markets_cards}</div>
    """

    # Editorial dateline for the brand header. Anchored to America/New_York
    # so the date is correct from the user's perspective regardless of the
    # server's UTC clock. Was naive datetime.now() before, which showed
    # tomorrow's date for any user east of UTC after ~8pm ET.
    from zoneinfo import ZoneInfo
    _now_local = datetime.now(ZoneInfo("America/New_York"))
    dateline = _now_local.strftime("%A, %B ") + str(_now_local.day) + _now_local.strftime(", %Y")

    vocab = overarching_analysis.get("vocab", []) or []
    def _cap(t: str) -> str:
        t = (t or "").strip()
        return t[:1].upper() + t[1:] if t else t
    if vocab:
        def _vocab_html(v: dict) -> str:
            term = md_inline(_cap(v.get('term', '')))
            definition = md_inline(v.get('definition', ''))
            pe = (v.get('plain_english') or '').strip()
            # New shape (your spec): the right column holds the definition prose
            # AND, if a plain-English version exists, a single bulleted sub-line
            # beneath it. No separate purple box — just a coherent definition
            # block with the "Plain Language:" line color-keyed to the rest of
            # the dashboard's plain-English callouts.
            pe_html = (
                f"<div class='vocab-plain-line'><span class='vocab-plain-bullet'>*</span> "
                f"<span class='vocab-plain-label'>Plain Language:</span> {md_inline(pe)}</div>"
                if pe else ""
            )
            return f"<div class='vocab-item'><span class='vocab-term'>{term}</span><div class='vocab-def'>{definition}{pe_html}</div></div>"
        vocab_items = "".join(
            _vocab_html(v)
            for v in vocab if isinstance(v, dict)
        )
        vocab_html = f"<details class='vocab vocab-top' open><summary class='vocab-summary'>Vocab bank ({len(vocab)})</summary><div class='vocab-body'>{vocab_items}</div></details>"
    else:
        vocab_html = ""

    # Build filter options and analysis sources
    all_tags: list[str] = []
    sources_present: set[str] = set()
    analysis_sources: list[str] = []
    for s, ss in zip(newsletters, newsletter_summaries):
        all_tags.extend(ss.get("tags", []))
        sources_present.add(s["sourceIcon"].upper())
        analysis_sources.append(f'<a href="{s["link"]}" target="_blank" rel="noreferrer">{clean_source(s["sourceIcon"], s["source"])}: {s["title"]}</a>')
    unique_tags = sorted(set(all_tags))
    source_filters = sorted(sources_present)
    filter_buttons = "".join(
        f'<button class="fbtn" data-filter="{t}">{t}</button>'
        for t in source_filters + unique_tags
    )
    filter_bar_html = f'<button class="fbtn active" data-filter="ALL">All</button>{filter_buttons}'
    sources_html = "".join(f"<li>{src}</li>" for src in analysis_sources)
    next_refresh_iso = _next_scheduled_refresh_iso()
    return f"""
    <!doctype html>
    <html lang="en">
      <head>
        <meta charset="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>Pulse</title>
        <link rel="preconnect" href="https://fonts.googleapis.com">
        <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
        <link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght,SOFT@9..144,400;9..144,500;9..144,600;9..144,700&family=Manrope:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
        <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
        <style>
          :root {{
            --bg-base: #08090d;
            --bg-grain: rgba(255,255,255,0.012);
            --surface-1: #11131a;
            --surface-2: #181b24;
            --surface-3: #20232e;
            --border-hairline: rgba(244,237,224,0.06);
            --border-soft:     rgba(244,237,224,0.10);
            --border-strong:   rgba(244,237,224,0.18);
            --ink-100: #f4ede0;
            --ink-80:  #d3cdc0;
            --ink-60:  #a39d92;
            --ink-40:  #6e6a62;
            --ink-30:  #4d4a45;
            --gold:        #c9a35d;
            --gold-strong: #d9b975;
            --gold-soft:   rgba(201,163,93,0.12);
            --gold-line:   rgba(201,163,93,0.30);
            --teal:        #6ec3b0;
            --teal-soft:   rgba(110,195,176,0.10);
            --up:          #7fbf7f;
            --up-soft:     rgba(127,191,127,0.12);
            --down:        #c75048;
            --down-soft:   rgba(199,80,72,0.12);
            --blue:        #6e9bc3;
            --blue-soft:   rgba(110,155,195,0.10);
            --purple:      #a995c3;
            --purple-soft: rgba(169,149,195,0.10);
            --orange:      #d99654;
            --orange-soft: rgba(217,150,84,0.10);
            --serif: 'Fraunces', 'Iowan Old Style', Georgia, serif;
            --sans:  'Manrope', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            --mono:  'JetBrains Mono', 'SF Mono', Menlo, monospace;
          }}
          * {{ box-sizing: border-box; }}
          html, body {{ background: var(--bg-base); }}
          body {{
            margin: 0;
            font-family: var(--sans);
            color: var(--ink-100);
            font-size: 15px;
            line-height: 1.5;
            -webkit-font-smoothing: antialiased;
            text-rendering: optimizeLegibility;
            font-feature-settings: 'kern', 'ss01';
            /* faint noise grain for paper-like depth */
            background-image:
              radial-gradient(1200px 600px at 80% -10%, rgba(201,163,93,0.04), transparent 60%),
              radial-gradient(900px 500px at -10% 30%, rgba(110,195,176,0.03), transparent 60%),
              url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='200' height='200'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 0 0 0 0.04 0'/></filter><rect width='100%25' height='100%25' filter='url(%23n)'/></svg>");
            background-attachment: fixed;
          }}
          main {{
            max-width: 1040px;
            margin: 0 auto;
            padding: 48px 28px 64px;
          }}
          h1, h2, h3, .briefing h2, .card h2 {{
            font-family: var(--serif);
            font-feature-settings: 'ss01', 'kern';
          }}
          h1 {{
            margin: 0;
            font-size: 56px;
            line-height: 0.95;
            font-weight: 500;
            letter-spacing: -0.02em;
            color: var(--ink-100);
            font-variation-settings: 'opsz' 144, 'SOFT' 30;
          }}
          h1 .dot {{
            display: inline-block;
            width: 10px;
            height: 10px;
            border-radius: 50%;
            background: var(--gold);
            margin-left: 6px;
            vertical-align: 20%;
            box-shadow: 0 0 18px rgba(201,163,93,0.7);
          }}
          .sub {{
            color: var(--ink-60);
            font-size: 13px;
            margin: 6px 0 0;
            letter-spacing: 0.01em;
          }}
          .grid {{
            display: grid;
            gap: 16px;
          }}
          .expand {{
            margin-bottom: 12px;
          }}
          .expand[open] .expand-btn {{
            background: var(--teal-soft);
            color: var(--teal);
            border-color: rgba(110,195,176,0.35);
          }}
          .expand-btn {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 6px 12px;
            border-radius: 999px;
            cursor: pointer;
            list-style: none;
            font-family: var(--sans);
            font-size: 11.5px;
            font-weight: 500;
            background: transparent;
            border: 1px solid var(--border-soft);
            color: var(--ink-60);
            user-select: none;
            transition: all 0.18s ease;
          }}
          .expand-btn:hover {{
            color: var(--ink-100);
            border-color: var(--border-strong);
          }}
          .expand-btn::-webkit-details-marker {{
            display: none;
          }}
          .expand-body {{
            margin-top: 14px;
          }}
          .briefing {{
            position: relative;
            background:
              linear-gradient(180deg, rgba(201,163,93,0.04), rgba(201,163,93,0.01)),
              var(--surface-1);
            border: 1px solid var(--border-hairline);
            border-radius: 18px;
            padding: 28px 28px 24px;
            margin-bottom: 32px;
            box-shadow:
              0 1px 0 rgba(255,255,255,0.03) inset,
              0 30px 60px -30px rgba(0,0,0,0.55);
          }}
          .briefing::before {{
            content: '';
            position: absolute;
            top: 0; left: 28px; right: 28px;
            height: 2px;
            background: linear-gradient(90deg, var(--gold), transparent 70%);
            border-radius: 18px 18px 0 0;
          }}
          .briefing h2 {{
            margin: 0 0 14px;
            font-size: 30px;
            line-height: 1.1;
            color: var(--ink-100);
            font-weight: 500;
            letter-spacing: -0.015em;
            font-variation-settings: 'opsz' 96, 'SOFT' 50;
          }}
          .briefing p {{
            margin-bottom: 10px;
          }}
          .chips {{
            display: flex;
            gap: 6px;
            flex-wrap: wrap;
            margin: 10px 0 14px;
          }}
          .chip {{
            font-family: var(--mono);
            font-size: 11px;
            padding: 3px 8px;
            border-radius: 4px;
            background: var(--gold-soft);
            color: var(--gold-strong);
            letter-spacing: 0.02em;
          }}
          ul {{
            margin: 8px 0 0;
            padding-left: 20px;
            color: var(--ink-80);
          }}
          li {{
            margin-bottom: 8px;
            line-height: 1.55;
          }}
          .card {{
            background: var(--surface-1);
            border: 1px solid var(--border-hairline);
            border-radius: 14px;
            padding: 22px 24px;
            transition: border-color 0.2s ease, transform 0.2s ease;
          }}
          .card:hover {{
            border-color: var(--border-soft);
          }}
          .newsletter {{
            border-color: var(--border-hairline);
            background: var(--surface-1);
            position: relative;
          }}
          .newsletter::before {{
            content: '';
            position: absolute;
            left: 0; top: 22px; bottom: 22px;
            width: 2px;
            background: var(--gold-line);
            border-radius: 0 2px 2px 0;
            opacity: 0.4;
          }}
          h3 {{
            margin: 36px 0 18px;
            font-family: var(--mono);
            color: var(--ink-60);
            letter-spacing: 0.18em;
            text-transform: uppercase;
            font-size: 10.5px;
            font-weight: 500;
            display: flex;
            align-items: center;
            gap: 12px;
          }}
          h3::after {{
            content: '';
            flex: 1;
            height: 1px;
            background: var(--border-hairline);
          }}
          .meta {{
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
            align-items: center;
            margin-bottom: 12px;
          }}
          .pill, .cat {{
            font-family: var(--mono);
            font-size: 10px;
            padding: 3px 8px;
            border-radius: 3px;
            background: var(--gold-soft);
            color: var(--gold-strong);
            letter-spacing: 0.08em;
            text-transform: uppercase;
            font-weight: 500;
          }}
          .engine {{
            font-family: var(--mono);
            font-size: 10px;
            padding: 3px 8px;
            border-radius: 3px;
            background: rgba(244,237,224,0.04);
            color: var(--ink-40);
            text-transform: lowercase;
          }}
          .cat {{
            background: var(--blue-soft);
            color: var(--blue);
          }}
          h2 {{
            margin: 0 0 12px;
            font-size: 22px;
            line-height: 1.22;
            font-weight: 500;
            color: var(--ink-100);
            letter-spacing: -0.01em;
            font-variation-settings: 'opsz' 32, 'SOFT' 50;
          }}
          a {{
            color: var(--ink-100);
            text-decoration: none;
            transition: color 0.18s ease;
          }}
          a:hover {{
            color: var(--gold-strong);
          }}
          p {{
            margin: 0 0 12px;
            color: var(--ink-80);
            line-height: 1.6;
          }}
          .desc {{
            margin-bottom: 12px;
            color: var(--ink-60);
            line-height: 1.55;
            font-size: 14px;
          }}
          .why {{
            margin-bottom: 14px;
            padding: 12px 14px;
            border-radius: 10px;
            background: var(--blue-soft);
            border-left: 2px solid rgba(110,155,195,0.45);
            color: var(--ink-80);
            line-height: 1.55;
            font-size: 14px;
          }}
          .why strong {{
            font-family: var(--mono);
            font-size: 10.5px;
            text-transform: uppercase;
            letter-spacing: 0.12em;
            color: var(--blue);
            display: block;
            margin-bottom: 6px;
            font-weight: 500;
          }}
          .summary-bullets {{
            margin: 0 0 14px;
            padding-left: 22px;
            color: var(--ink-80);
            line-height: 1.6;
          }}
          .summary-bullets li {{
            margin-bottom: 8px;
          }}
          .summary-bullets li::marker {{
            color: var(--gold);
          }}
          .plain-english {{
            margin: 0 0 14px;
            padding: 12px 14px;
            border-radius: 10px;
            background: var(--purple-soft);
            border-left: 2px solid rgba(169,149,195,0.45);
            color: var(--ink-80);
            font-size: 14px;
            line-height: 1.55;
          }}
          /* Inside an analysis block — indented single line, no callout box. */
          .analysis-block .block-plain {{
            display: block;
            margin: 8px 0 0 14px;
            padding: 0;
            background: transparent;
            border-left: none;
            color: var(--ink-60);
            font-size: 13px;
            line-height: 1.5;
            font-style: italic;
          }}
          .analysis-block .block-plain .pe-label {{
            color: var(--ink-80);
            font-style: normal;
            font-weight: 500;
            background: transparent;
            padding: 0;
            margin-right: 4px;
            font-size: 10px;
          }}
          .analysis-block .block-plain .pe-label::before {{ content: "↳ "; }}
          /* Same indented style for chart commentary plain-language line. */
          .mkt-summary-plain {{
            display: block;
            margin: 6px 0 4px 14px;
            padding: 0;
            background: transparent;
            border-left: none;
            color: var(--ink-60);
            font-size: 12px;
            line-height: 1.45;
            font-style: italic;
          }}
          .mkt-summary-plain .pe-label {{
            color: var(--ink-80);
            font-style: normal;
            font-weight: 500;
            background: transparent;
            padding: 0;
            margin-right: 4px;
            font-size: 9px;
          }}
          .mkt-summary-plain .pe-label::before {{ content: "↳ "; }}
          /* Per-field Plain Language under a card's why/impact/framing field. */
          .field-plain {{
            margin: 4px 0 0 14px;
            color: var(--ink-60);
            font-size: 12.5px;
            line-height: 1.5;
            font-style: italic;
          }}
          .field-plain-label {{
            color: var(--ink-80);
            font-style: normal;
            font-weight: 500;
          }}
          /* Jargon hover-tooltip — custom popover rendered via CSS `::after` from
             the `data-tip` attribute, so the explanation appears immediately on
             hover (vs the ~500ms browser-native title delay) and matches Pulse's
             editorial palette. */
          .jargon {{
            position: relative;
            border-bottom: 1px dotted var(--gold-strong);
            /* cursor:pointer (not :help) so iOS Safari treats the span as a
               tappable target. iOS swallows click events on spans with
               cursor:help, blocking the tap-to-toggle handler.
               user-select: none + tap-highlight-color: transparent suppress
               iOS's default text-selection-on-tap and gray flash overlay —
               taps now fire as clean click events instead of selecting text. */
            cursor: pointer;
            -webkit-user-select: none;
            user-select: none;
            -webkit-tap-highlight-color: transparent;
            -webkit-touch-callout: none;
            background: rgba(212, 166, 71, 0.08);
            padding: 0 2px;
            border-radius: 2px;
            transition: background 0.15s;
          }}
          .jargon:hover {{
            background: rgba(212, 166, 71, 0.22);
            border-bottom-style: solid;
          }}
          .jargon::after,
          .vault-ref::after {{
            content: attr(data-tip);
            position: absolute;
            bottom: calc(100% + 8px);
            left: 50%;
            transform: translateX(-50%);
            /* Solid dark-purple backing so text reads cleanly over busy page
               content; purple-soft as a subtle gradient overlay for the
               editorial palette. Together → ~98% effective opacity. */
            background:
              linear-gradient(rgba(169, 149, 195, 0.18), rgba(169, 149, 195, 0.18)),
              rgba(28, 22, 38, 0.98);
            color: var(--ink-100);
            padding: 11px 15px;
            border-radius: 8px;
            border-left: 3px solid rgba(169, 149, 195, 0.85);
            box-shadow: 0 10px 28px rgba(0, 0, 0, 0.6);
            font-family: var(--sans);
            font-size: 12.5px;
            font-style: normal;
            font-weight: 400;
            line-height: 1.55;
            width: max-content;
            max-width: 340px;
            white-space: normal;
            text-align: left;
            opacity: 0;
            pointer-events: none;
            transition: opacity 0.18s ease-out, transform 0.18s ease-out;
            z-index: 100;
          }}
          .jargon:hover::after,
          .vault-ref:hover::after {{
            opacity: 1;
            transform: translateX(-50%) translateY(-2px);
          }}
          /* Arrow pointing down from the tooltip to the term. */
          .jargon::before,
          .vault-ref::before {{
            content: "";
            position: absolute;
            bottom: calc(100% + 2px);
            left: 50%;
            transform: translateX(-50%);
            border-left: 6px solid transparent;
            border-right: 6px solid transparent;
            border-top: 6px solid rgba(28, 22, 38, 0.98);
            opacity: 0;
            pointer-events: none;
            transition: opacity 0.18s ease-out;
            z-index: 101;
          }}
          .jargon:hover::before,
          .vault-ref:hover::before {{ opacity: 1; }}
          /* Tap-to-toggle support for touch devices — JS adds .is-open on tap.
             Mirrors the :hover state so tap and hover produce identical UX. */
          .jargon.is-open {{
            background: rgba(212, 166, 71, 0.22);
            border-bottom-style: solid;
          }}
          .jargon.is-open::after,
          .vault-ref.is-open::after {{
            opacity: 1;
            transform: translateX(-50%) translateY(-2px);
          }}
          .jargon.is-open::before,
          .vault-ref.is-open::before {{ opacity: 1; }}
          /* Touch tooltip — singleton element appended to <body> by JS so
             no ancestor transform/filter can break position:fixed (a known
             CSS spec issue that was hiding the ::after tooltip on mobile).
             Only used on touch devices; desktop keeps the existing :hover
             ::after popover. */
          #mobile-tip {{
            position: fixed;
            left: 12px;
            right: 12px;
            bottom: max(20px, env(safe-area-inset-bottom, 16px));
            background:
              linear-gradient(rgba(169, 149, 195, 0.18), rgba(169, 149, 195, 0.18)),
              rgba(28, 22, 38, 0.98);
            color: var(--ink-100);
            padding: 14px 16px;
            border-radius: 10px;
            border-left: 3px solid rgba(169, 149, 195, 0.85);
            box-shadow: 0 10px 28px rgba(0, 0, 0, 0.6);
            font-family: var(--sans);
            font-size: 14px;
            line-height: 1.55;
            text-align: left;
            z-index: 10000;
            opacity: 0;
            pointer-events: none;
            transform: translateY(8px);
            transition: opacity 0.18s ease-out, transform 0.18s ease-out;
          }}
          #mobile-tip.is-visible {{
            opacity: 1;
            transform: translateY(0);
          }}
          /* Vault references — when the LLM weaves a concept from the user's
             Obsidian vault into the briefing, md_inline strips the [[...]]
             wiki syntax and wraps the term in this span. Subtle gold dotted
             underline + italic marks it as "this is your knowledge" without
             being visually loud. */
          .vault-ref {{
            position: relative;  /* required so ::after tooltip anchors here */
            font-style: italic;
            color: var(--ink-100);
            border-bottom: 1px dotted rgba(212, 166, 71, 0.4);
            padding-bottom: 0;
            /* cursor:pointer (not :help) so iOS Safari fires click events
               that bubble to the tap-to-toggle document handler.
               user-select: none suppresses iOS's text-selection-on-tap so
               the click handler actually receives the event. */
            cursor: pointer;
            -webkit-user-select: none;
            user-select: none;
            -webkit-tap-highlight-color: transparent;
            -webkit-touch-callout: none;
          }}
          /* New vocab plain-language line: indented sub-bullet inside the
             definition column, color-keyed to the dashboard's plain-English
             palette (purple). Compact, doesn't bloat the vocab card. */
          .vocab-plain-line {{
            margin-top: 8px;
            padding-left: 12px;
            border-left: 2px solid rgba(169, 149, 195, 0.45);
            color: var(--ink-70, var(--ink-80));
            font-size: 12.5px;
            line-height: 1.55;
          }}
          .vocab-plain-bullet {{
            color: rgba(169, 149, 195, 0.85);
            font-weight: 600;
            margin-right: 4px;
          }}
          .vocab-plain-label {{
            color: rgba(169, 149, 195, 1);
            font-weight: 600;
            letter-spacing: 0.02em;
            margin-right: 4px;
          }}
          .pe-label {{
            display: inline-block;
            margin-right: 10px;
            padding: 2px 8px;
            border-radius: 3px;
            background: rgba(169,149,195,0.18);
            color: var(--purple);
            font-family: var(--mono);
            font-size: 10px;
            font-weight: 500;
            text-transform: uppercase;
            letter-spacing: 0.12em;
            vertical-align: 1px;
          }}
          .date {{
            font-family: var(--mono);
            font-size: 11px;
            color: var(--ink-40);
            letter-spacing: 0.02em;
          }}
          .tldr {{
            font-family: var(--serif);
            font-size: 19px;
            line-height: 1.45;
            color: var(--ink-100);
            margin-bottom: 18px;
            font-weight: 400;
            letter-spacing: -0.005em;
            font-variation-settings: 'opsz' 32, 'SOFT' 30;
          }}
          .analysis-block {{
            margin-bottom: 14px;
            padding: 16px 18px;
            border-radius: 10px;
            background: var(--surface-2);
            border: 1px solid var(--border-hairline);
          }}
          .analysis-block strong {{
            display: block;
            font-family: var(--mono);
            font-size: 10.5px;
            text-transform: uppercase;
            letter-spacing: 0.16em;
            color: var(--ink-60);
            margin-bottom: 10px;
            font-weight: 500;
          }}
          .analysis-block p {{
            margin: 0;
            font-size: 14.5px;
            line-height: 1.6;
            color: var(--ink-80);
          }}
          .analysis-block ul.summary-bullets {{
            margin: 0;
            padding-left: 20px;
            font-size: 14.5px;
            line-height: 1.6;
            color: var(--ink-80);
          }}
          .analysis-block ul.summary-bullets li {{
            margin-bottom: 6px;
          }}
          .plain-tldr {{
            margin: -4px 0 16px;
            font-size: 14.5px;
          }}
          .vocab {{
            margin: 0 0 20px;
            padding: 14px 18px;
            border-radius: 10px;
            background: var(--gold-soft);
            border: 1px solid var(--gold-line);
          }}
          .vocab-summary {{
            cursor: pointer;
            list-style: none;
            font-family: var(--mono);
            font-size: 10.5px;
            text-transform: uppercase;
            letter-spacing: 0.16em;
            color: var(--gold-strong);
            font-weight: 500;
            display: flex;
            align-items: center;
            gap: 8px;
          }}
          .vocab-summary::before {{
            content: '§';
            color: var(--gold);
            font-family: var(--serif);
            font-style: italic;
            font-size: 13px;
            font-weight: 400;
            letter-spacing: 0;
          }}
          .vocab-summary::-webkit-details-marker {{ display: none; }}
          .vocab-body {{
            margin-top: 14px;
            display: grid;
            gap: 10px;
            padding-top: 12px;
            border-top: 1px solid var(--gold-line);
          }}
          .vocab-item {{
            display: grid;
            grid-template-columns: 180px 1fr;
            gap: 16px;
            font-size: 13.5px;
            line-height: 1.5;
          }}
          .vocab-term {{
            color: var(--gold-strong);
            font-weight: 600;
            font-feature-settings: 'kern', 'liga', 'ss01';
          }}
          .vocab-def {{
            color: var(--ink-80);
          }}
          .markets-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 16px;
            margin-bottom: 32px;
          }}
          .dossiers-shell {{
            display: grid;
            grid-template-columns: 220px 1fr;
            gap: 28px;
            margin-bottom: 32px;
          }}
          .dossiers-side {{
            border-right: 1px solid var(--border-hairline);
            padding-right: 18px;
          }}
          .dossiers-side-header {{
            font-family: var(--mono);
            font-size: 10px;
            color: var(--ink-60);
            letter-spacing: 0.18em;
            text-transform: uppercase;
            margin-bottom: 10px;
          }}
          .dossiers-list {{
            list-style: none;
            margin: 0;
            padding: 0;
          }}
          .d-item {{
            position: relative;
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 8px 10px 8px 14px;
            margin-bottom: 2px;
            border-radius: 6px;
            cursor: pointer;
            font-family: var(--serif);
            color: var(--ink-80);
            font-size: 13.5px;
            transition: background 0.15s ease, color 0.15s ease;
          }}
          .d-item:hover {{
            background: var(--surface-2);
            color: var(--ink-100);
          }}
          .d-item.active {{
            background: var(--gold-soft);
            color: var(--ink-100);
          }}
          .d-rail {{
            position: absolute;
            left: 0;
            top: 6px;
            bottom: 6px;
            width: 2px;
            border-radius: 1px;
          }}
          .d-name {{
            flex: 1;
            min-width: 0;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
          }}
          .d-count {{
            font-family: var(--mono);
            font-size: 10.5px;
            color: var(--ink-40);
          }}
          .dossiers-add {{
            margin-top: 18px;
            display: grid;
            gap: 6px;
          }}
          .dossiers-add input,
          .dossiers-add select {{
            font-family: var(--sans);
            font-size: 12.5px;
            padding: 6px 10px;
            background: var(--surface-2);
            border: 1px solid var(--border-hairline);
            border-radius: 6px;
            color: var(--ink-100);
          }}
          .dossiers-add button {{
            font-family: var(--mono);
            font-size: 11px;
            padding: 7px;
            background: transparent;
            border: 1px solid var(--gold-line);
            border-radius: 6px;
            color: var(--gold-strong);
            cursor: pointer;
            letter-spacing: 0.1em;
            text-transform: uppercase;
          }}
          .dossiers-add button:hover {{ background: var(--gold-soft); }}
          .dossiers-page {{
            min-height: 300px;
            position: relative;
            padding-top: 18px;
          }}
          .dossiers-page::before {{
            content: '';
            position: absolute;
            top: 0; left: 0; right: 0;
            height: 2px;
            border-radius: 2px;
            background: var(--gold);
          }}
          .dossiers-page.kind-sector::before {{ background: var(--teal); }}
          .dossiers-page.kind-concept::before {{ background: var(--purple); }}
          .dossiers-page.kind-concept .d-title {{
            font-style: italic;
          }}
          .dossiers-empty {{
            color: var(--ink-40);
            font-style: italic;
            padding: 40px 0;
            text-align: center;
          }}
          .d-head {{
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 18px;
            gap: 16px;
          }}
          .d-title {{
            font-family: var(--serif);
            font-size: 30px;
            color: var(--ink-100);
            margin: 0;
            font-weight: 500;
            letter-spacing: -0.01em;
          }}
          .d-kind {{
            font-family: var(--mono);
            font-size: 10.5px;
            letter-spacing: 0.14em;
            text-transform: uppercase;
          }}
          .d-unfollow {{
            font-family: var(--mono);
            font-size: 10.5px;
            padding: 8px 14px;
            background: transparent;
            border: 1px solid var(--border-soft);
            border-radius: 999px;
            color: var(--ink-60);
            cursor: pointer;
            letter-spacing: 0.08em;
            text-transform: uppercase;
          }}
          .d-unfollow:hover {{
            color: var(--down);
            border-color: rgba(199,80,72,0.45);
          }}
          .d-overview {{
            font-family: var(--serif);
            font-size: 16px;
            line-height: 1.55;
            color: var(--ink-100);
            margin: 0 0 16px;
          }}
          .d-plain {{ margin: 0 0 16px; }}
          .d-analyzing {{
            font-family: var(--mono);
            font-size: 11px;
            color: var(--ink-60);
            letter-spacing: 0.12em;
            text-transform: uppercase;
            background: rgba(201, 163, 93, 0.08);
            border-left: 2px solid #c9a35d;
            padding: 10px 14px;
            margin: 0 0 16px;
            border-radius: 2px;
          }}
          .d-analyzing-dots {{
            color: #c9a35d;
            animation: d-dots-pulse 1.4s ease-in-out infinite;
          }}
          @keyframes d-dots-pulse {{
            0%, 100% {{ opacity: 0.35; }}
            50% {{ opacity: 1; }}
          }}
          .d-mentions-label {{
            font-family: var(--mono);
            font-size: 10px;
            color: var(--ink-60);
            letter-spacing: 0.18em;
            text-transform: uppercase;
            margin: 18px 0 10px;
          }}
          .d-mentions {{
            list-style: none;
            margin: 0;
            padding: 0;
            border-left: 2px solid var(--border-hairline);
          }}
          .d-showmore {{
            display: block;
            margin: 16px 0 8px 0;
            padding: 10px 18px;
            background: var(--surface-2);
            color: var(--ink-80);
            border: 1px solid var(--border-hairline);
            border-radius: 4px;
            font-family: var(--mono);
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            cursor: pointer;
            transition: background 0.15s, color 0.15s;
          }}
          .d-showmore:hover:not(:disabled) {{
            background: var(--gold-soft);
            color: var(--ink-100);
            border-color: var(--gold-strong);
          }}
          .d-showmore:disabled {{ opacity: 0.5; cursor: wait; }}
          .d-mention {{
            padding: 8px 0 8px 14px;
            border-bottom: 1px solid var(--border-hairline);
          }}
          .d-mention:last-child {{ border-bottom: none; }}
          .d-mention-head {{
            display: flex;
            align-items: baseline;
            gap: 10px;
            margin-bottom: 4px;
            flex-wrap: wrap;
          }}
          .d-mention-date {{
            font-family: var(--mono);
            font-size: 10px;
            color: var(--ink-40);
            flex-shrink: 0;
          }}
          .d-mention-title {{
            font-family: var(--serif);
            font-size: 14px;
            color: var(--ink-100);
            font-weight: 500;
            text-decoration: none;
            flex: 1;
            min-width: 0;
          }}
          .d-mention-title:hover {{
            color: var(--gold-strong);
          }}
          .d-mention-title.archived {{
            color: var(--ink-40);
            font-style: italic;
          }}
          .d-mention-archived-tag {{
            font-family: var(--mono);
            font-size: 9px;
            color: var(--ink-40);
            text-transform: uppercase;
            letter-spacing: 0.1em;
            padding: 1px 5px;
            border-radius: 3px;
            background: var(--surface-2);
            font-style: normal;
            margin-left: 4px;
            vertical-align: 1px;
          }}
          .d-mention-src {{
            font-family: var(--mono);
            font-size: 9.5px;
            color: var(--ink-60);
            text-transform: uppercase;
            letter-spacing: 0.1em;
            padding: 2px 6px;
            border-radius: 3px;
            background: var(--surface-2);
          }}
          .d-mention-quote {{
            font-size: 12.5px;
            color: var(--ink-60);
            line-height: 1.5;
            padding-left: 0;
          }}
          .d-mention-bullets {{
            list-style: none;
            margin: 0;
            padding: 0;
          }}
          .d-mention-bullets li {{
            font-size: 12.5px;
            color: var(--ink-80);
            line-height: 1.5;
            padding: 2px 0 2px 14px;
            position: relative;
          }}
          .d-mention-bullets li::before {{
            content: "";
            position: absolute;
            left: 2px;
            top: 0.7em;
            width: 4px;
            height: 4px;
            border-radius: 50%;
            background: var(--gold-strong);
          }}
          @media (max-width: 767px) {{
            /* Force the grid OFF entirely at phone tier — switching to block
               makes the .dossiers-side stack ABOVE .dossiers-page with no
               column constraint, so the dossier content uses full viewport
               width instead of being squeezed into a 180px-wide right column. */
            .dossiers-shell {{
              display: block !important;
              gap: 0 !important;
            }}
            .dossiers-side {{
              display: block;
              width: 100%;
              border-right: none;
              border-bottom: 1px solid var(--border-hairline);
              padding: 0 0 14px 0;
              margin-bottom: 16px;
            }}
            .dossiers-page {{
              width: 100%;
              padding-left: 0;
              padding-right: 0;
            }}
            .dossiers-list {{
              display: flex;
              gap: 6px;
              overflow-x: auto;
              padding-bottom: 4px;
              scrollbar-width: thin;
            }}
            .d-item {{
              flex: 0 0 auto;
              white-space: nowrap;
              padding: 8px 14px 8px 18px;
              border-radius: 999px;
              background: var(--surface-2);
            }}
            .d-rail {{ top: 8px; bottom: 8px; left: 6px; }}
            .d-count {{ margin-left: 4px; }}
            /* Stack the follow-form vertically so the input gets full width
               instead of being squeezed alongside a select + button at 375px. */
            .dossiers-add {{ grid-template-columns: 1fr; gap: 8px; }}
            .dossiers-add input,
            .dossiers-add select,
            .dossiers-add button {{ width: 100%; font-size: 14px; padding: 10px; }}
            .dossiers-side {{ margin-bottom: 8px; }}
            .dossiers-side-header {{ font-size: 11px; margin-bottom: 8px; }}
            .dossiers-page {{ padding-top: 20px; }}
            .d-head {{
              flex-direction: column;
              gap: 8px;
              margin-bottom: 16px;
            }}
            /* Bump dossier text sizes on phone — desktop sizes (~10-12px for
               metadata, 14px for titles) look uncomfortably small on a 375px
               screen alongside the briefing's larger type. */
            .d-title {{ font-size: 26px; line-height: 1.15; }}
            .d-mention {{ padding: 12px 0 12px 14px; }}
            .d-mention-date {{ font-size: 11px; }}
            .d-mention-title {{ font-size: 15px; line-height: 1.4; }}
            .d-mention-src {{ font-size: 10.5px; letter-spacing: 0.08em; }}
            .d-mention-quote {{ font-size: 14px; line-height: 1.55; }}
            .d-mention-bullets li {{ font-size: 14px; line-height: 1.55; padding: 4px 0 4px 14px; }}
            .d-mentions-label {{ font-size: 11px; }}
            .d-overview {{ font-size: 16px; line-height: 1.6; }}
            .d-kind {{ font-size: 11px; }}
          }}
          .mkt-card {{
            background: var(--surface-1);
            border: 1px solid var(--border-hairline);
            border-radius: 14px;
            padding: 18px 20px 20px;
            display: flex;
            flex-direction: column;
            gap: 8px;
            transition: border-color 0.2s ease;
          }}
          .mkt-card:hover {{
            border-color: var(--border-soft);
          }}
          .mkt-card.spotlight {{
            border-color: var(--gold-line);
            background:
              linear-gradient(180deg, rgba(201,163,93,0.04), transparent 60%),
              var(--surface-1);
          }}
          .mkt-card.spotlight:hover {{
            border-color: rgba(201,163,93,0.45);
          }}
          .mkt-spotlight-eyebrow {{
            font-family: var(--mono);
            font-size: 9.5px;
            text-transform: uppercase;
            letter-spacing: 0.22em;
            color: var(--gold-strong);
            font-weight: 500;
            margin-bottom: 8px;
            display: inline-block;
            padding: 2px 8px;
            background: var(--gold-soft);
            border-radius: 3px;
            align-self: flex-start;
          }}
          .mkt-head {{
            display: flex;
            justify-content: space-between;
            align-items: baseline;
            gap: 8px;
          }}
          .mkt-label {{
            font-family: var(--serif);
            font-weight: 500;
            color: var(--ink-100);
            font-size: 17px;
            letter-spacing: -0.005em;
            font-variation-settings: 'opsz' 32, 'SOFT' 40;
          }}
          .mkt-symbol {{
            font-family: var(--mono);
            font-size: 10.5px;
            color: var(--ink-40);
            letter-spacing: 0.04em;
          }}
          .mkt-desc {{
            font-family: var(--serif);
            font-style: italic;
            font-size: 12.5px;
            line-height: 1.4;
            color: var(--ink-60);
            margin: -2px 0 4px;
            font-variation-settings: 'opsz' 14, 'SOFT' 50;
          }}
          .mkt-price-row {{
            display: flex;
            justify-content: space-between;
            align-items: baseline;
            gap: 8px;
            margin-top: 2px;
          }}
          .mkt-price {{
            font-family: var(--mono);
            font-size: 22px;
            font-weight: 500;
            color: var(--ink-100);
            font-variant-numeric: tabular-nums;
            letter-spacing: -0.01em;
          }}
          .mkt-change {{
            font-family: var(--mono);
            font-size: 12px;
            font-variant-numeric: tabular-nums;
            font-weight: 500;
          }}
          .mkt-change.up {{ color: var(--up); }}
          .mkt-change.down {{ color: var(--down); }}
          .mkt-change.neutral {{ color: var(--ink-40); }}
          .mkt-chart-wrap {{
            position: relative;
            height: 84px;
            margin-top: 6px;
          }}
          .mkt-period-row {{
            display: flex;
            gap: 2px;
            margin-top: 8px;
            padding: 3px;
            background: var(--surface-2);
            border-radius: 8px;
          }}
          .mkt-period {{
            flex: 1;
            padding: 5px 6px;
            border-radius: 5px;
            border: none;
            background: transparent;
            color: var(--ink-60);
            font-family: var(--mono);
            font-size: 10.5px;
            font-weight: 500;
            letter-spacing: 0.04em;
            cursor: pointer;
            transition: all 0.15s ease;
          }}
          .mkt-period:hover {{ color: var(--ink-100); }}
          .mkt-period.active {{
            background: var(--gold-soft);
            color: var(--gold-strong);
          }}
          .mkt-summary {{
            margin-top: 14px;
            padding-top: 14px;
            border-top: 1px solid var(--border-hairline);
            font-size: 13px;
            line-height: 1.5;
            color: var(--ink-80);
          }}
          .mkt-summary-loading {{ color: var(--ink-40); font-style: italic; }}
          .mkt-summary-header {{
            font-family: var(--mono);
            font-size: 10px;
            text-transform: uppercase;
            letter-spacing: 0.16em;
            color: var(--gold-strong);
            font-weight: 500;
            margin-bottom: 8px;
          }}
          .mkt-summary-bullets {{
            margin: 0 0 8px;
            padding-left: 18px;
            color: var(--ink-80);
          }}
          .mkt-summary-bullets li {{
            margin-bottom: 5px;
            line-height: 1.5;
          }}
          .mkt-summary-bullets li::marker {{ color: var(--gold); }}
          .mkt-summary-sources {{
            margin-top: 10px;
            padding-top: 8px;
            border-top: 1px dashed var(--border-hairline);
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            gap: 5px;
            font-family: var(--mono);
            font-size: 10px;
            color: var(--ink-40);
            letter-spacing: 0.05em;
            text-transform: uppercase;
          }}
          .mkt-summary-sources .src-badge {{
            font-family: var(--sans);
            font-size: 11px;
            padding: 2px 7px;
            text-transform: none;
            letter-spacing: 0;
          }}
          .tab-bar {{
            display: flex;
            gap: 0;
            margin: 32px 0 28px;
            border-bottom: 1px solid var(--border-hairline);
            position: relative;
          }}
          .tab-btn {{
            background: transparent;
            border: none;
            border-bottom: 2px solid transparent;
            color: var(--ink-60);
            font-family: var(--serif);
            font-size: 18px;
            font-weight: 500;
            padding: 14px 4px;
            margin-right: 32px;
            cursor: pointer;
            margin-bottom: -1px;
            transition: color 0.2s ease, border-color 0.2s ease;
            letter-spacing: -0.005em;
            font-variation-settings: 'opsz' 32, 'SOFT' 40;
            position: relative;
          }}
          .tab-btn:hover {{ color: var(--ink-100); }}
          .tab-btn.active {{
            color: var(--ink-100);
            border-bottom-color: var(--gold);
          }}
          .tab-btn.active::after {{
            content: '';
            position: absolute;
            left: 50%;
            bottom: -6px;
            width: 5px;
            height: 5px;
            background: var(--gold);
            border-radius: 50%;
            transform: translateX(-50%);
          }}
          .tab-panel {{ display: none; }}
          .tab-panel.active {{ display: block; animation: fadeUp 0.35s ease both; }}
          @keyframes fadeUp {{
            from {{ opacity: 0; transform: translateY(6px); }}
            to   {{ opacity: 1; transform: translateY(0); }}
          }}
          @media (max-width: 767px) {{
            main {{ padding: 28px 18px 48px; }}
            h1 {{ font-size: 42px; }}
            .briefing {{ padding: 22px 20px 18px; }}
            .briefing::before {{ left: 20px; right: 20px; }}
            .briefing h2 {{ font-size: 24px; }}
            .tldr {{ font-size: 17px; }}
            .analysis-grid {{ grid-template-columns: 1fr !important; }}
            .vocab-item {{
              grid-template-columns: 1fr;
              gap: 3px;
            }}
            .vocab-term {{ font-size: 14.5px; }}
            .tab-bar {{
              overflow-x: auto;
              scrollbar-width: none;
              -ms-overflow-style: none;
              flex-wrap: nowrap;
            }}
            .tab-bar::-webkit-scrollbar {{ display: none; }}
            .tab-btn {{
              font-size: 16px;
              margin-right: 24px;
              padding: 12px 2px;
              min-height: 44px;
              white-space: nowrap;
            }}
            .card {{ padding: 18px 18px; }}
            .markets-grid {{ grid-template-columns: 1fr; gap: 14px; }}
            .markets-grid canvas {{
              width: 100% !important;
              max-width: 100% !important;
              height: auto !important;
            }}
            .mkt-card {{
              padding: 14px;
              overflow: hidden;
            }}
            .mkt-timeframe {{
              flex-wrap: wrap;
              gap: 6px;
            }}
            #syncBtn {{
              padding: 9px 14px;
              font-size: 12px;
            }}
            .fbtn {{
              padding: 7px 12px;
              font-size: 12px;
            }}
            .expand-btn {{
              padding: 7px 14px;
            }}
            .pill, .cat, .tag {{
              font-size: 10.5px;
            }}
            /* On phone, anchor jargon/vault-ref tooltips to the viewport
               bottom so they never get cut off when the term is near a
               screen edge. Arrows hidden because they'd point at nothing. */
            .jargon::after,
            .vault-ref::after {{
              position: fixed !important;
              left: 12px !important;
              right: 12px !important;
              bottom: 16px !important;
              top: auto !important;
              transform: none !important;
              max-width: none !important;
              width: auto !important;
            }}
            .jargon:hover::after,
            .jargon.is-open::after,
            .vault-ref:hover::after,
            .vault-ref.is-open::after {{
              transform: none !important;
            }}
            .jargon::before,
            .vault-ref::before {{ display: none; }}
          }}

          /* Landscape-phone catch — iPhone Pro Max landscape is 1170×430,
             which would otherwise inherit desktop styles. Short viewport
             height + coarse pointer is a reliable phone signature regardless
             of orientation. Applies the same overrides as the phone tier
             above by re-declaring the key ones inline. */
          @media (max-height: 500px) and (pointer: coarse) {{
            main {{ padding: 16px 18px 32px; }}
            .top-bar-actions {{ flex-direction: column; align-items: flex-end; gap: 6px; }}
            .next-refresh {{ font-size: 10px; }}
            .dossiers-shell {{ grid-template-columns: 1fr; gap: 16px; }}
            .dossiers-side {{
              border-right: none;
              border-bottom: 1px solid var(--border-hairline);
              padding: 0 0 14px 0;
            }}
            .dossiers-list {{
              display: flex;
              gap: 6px;
              overflow-x: auto;
              padding-bottom: 4px;
            }}
            .d-item {{
              flex: 0 0 auto;
              white-space: nowrap;
              padding: 8px 14px;
              border-radius: 999px;
              background: var(--surface-2);
            }}
            .dossiers-add {{ grid-template-columns: 1fr; gap: 8px; }}
            .analysis-grid {{ grid-template-columns: 1fr !important; }}
            .markets-grid {{ grid-template-columns: 1fr; gap: 14px; }}
          }}

          @media (max-width: 1023px) and (min-width: 768px) {{
            main {{ padding: 28px 24px 48px; max-width: 100%; }}
            .briefing {{ padding: 26px 24px 22px; }}
            .analysis-grid {{ grid-template-columns: 1fr !important; }}
            .markets-grid {{ grid-template-columns: 1fr; gap: 16px; }}
            .markets-grid canvas {{
              width: 100% !important;
              max-width: 100% !important;
              height: auto !important;
            }}
          }}

          .analysis-grid {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 12px;
            margin-bottom: 14px;
          }}
          .analysis-block.bull {{
            border-left: 2px solid rgba(127,191,127,0.55);
            background: var(--up-soft);
          }}
          .analysis-block.bull strong {{ color: var(--up); }}
          .analysis-block.bear {{
            border-left: 2px solid rgba(199,80,72,0.55);
            background: var(--down-soft);
          }}
          .analysis-block.bear strong {{ color: var(--down); }}
          .analysis-block.nq {{
            border-left: 2px solid var(--gold-line);
            background: var(--gold-soft);
          }}
          .analysis-block.nq strong {{ color: var(--gold-strong); }}
          .analysis-block.stern {{
            border-left: 2px solid rgba(110,155,195,0.55);
            background: var(--blue-soft);
          }}
          .analysis-block.stern strong {{ color: var(--blue); }}
          .analysis-block.what {{
            border-left: 2px solid var(--border-strong);
            background: var(--surface-2);
          }}
          .analysis-block.what strong {{ color: var(--ink-60); }}
          .analysis-block.mkts {{
            border-left: 2px solid rgba(110,195,176,0.55);
            background: var(--teal-soft);
          }}
          .analysis-block.mkts strong {{ color: var(--teal); }}
          .analysis-block.watch {{
            border-left: 2px solid rgba(217,150,84,0.55);
            background: var(--orange-soft);
          }}
          .analysis-block.watch strong {{ color: var(--orange); }}
          .filter-bar {{
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            margin-bottom: 24px;
          }}
          .fbtn {{
            padding: 6px 14px;
            border-radius: 999px;
            border: 1px solid var(--border-soft);
            background: transparent;
            color: var(--ink-60);
            font-family: var(--sans);
            font-size: 12px;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.18s ease;
          }}
          .fbtn:hover {{
            color: var(--ink-100);
            border-color: var(--border-strong);
          }}
          .fbtn.active {{
            background: var(--surface-2);
            border-color: var(--border-strong);
            color: var(--ink-100);
          }}
          .fbtn[data-filter="ALL"] {{ border-color: rgba(232,197,71,0.4); }}
          .fbtn[data-filter="ALL"].active {{ background: rgba(232,197,71,0.15); color: #e8c547; border-color: #e8c547; }}
          .fbtn[data-filter="NYT"],.fbtn[data-filter="WSJ"] {{ border-color: rgba(148,163,184,0.3); color: rgba(148,163,184,0.8); }}
          .fbtn[data-filter="NYT"].active,.fbtn[data-filter="WSJ"].active {{ background: rgba(148,163,184,0.15); color: #94a3b8; border-color: #94a3b8; }}
          .fbtn[data-filter="AI/Tech"]  {{ border-color: rgba(56,189,248,0.3);   color: #38bdf8; }}
          .fbtn[data-filter="AI/Tech"].active  {{ background: rgba(56,189,248,0.15); border-color: #38bdf8; }}
          .fbtn[data-filter="Geopolitical"] {{ border-color: rgba(251,146,60,0.3); color: #fb923c; }}
          .fbtn[data-filter="Geopolitical"].active {{ background: rgba(251,146,60,0.15); border-color: #fb923c; }}
          .fbtn[data-filter="Macro"]    {{ border-color: rgba(167,139,250,0.3);  color: #a78bfa; }}
          .fbtn[data-filter="Macro"].active    {{ background: rgba(167,139,250,0.15); border-color: #a78bfa; }}
          .fbtn[data-filter="Finance"]  {{ border-color: rgba(52,211,153,0.3);   color: #34d399; }}
          .fbtn[data-filter="Finance"].active  {{ background: rgba(52,211,153,0.15); border-color: #34d399; }}
          .fbtn[data-filter="Markets"]  {{ border-color: rgba(20,184,166,0.3);   color: #14b8a6; }}
          .fbtn[data-filter="Markets"].active  {{ background: rgba(20,184,166,0.15); border-color: #14b8a6; }}
          .fbtn[data-filter="Energy"]   {{ border-color: rgba(250,204,21,0.3);   color: #facc15; }}
          .fbtn[data-filter="Energy"].active   {{ background: rgba(250,204,21,0.15); border-color: #facc15; }}
          .fbtn[data-filter="Politics"] {{ border-color: rgba(251,113,133,0.3);  color: #fb7185; }}
          .fbtn[data-filter="Politics"].active {{ background: rgba(251,113,133,0.15); border-color: #fb7185; }}
          .fbtn[data-filter="Trade"]    {{ border-color: rgba(99,102,241,0.3);   color: #6366f1; }}
          .fbtn[data-filter="Trade"].active    {{ background: rgba(99,102,241,0.15); border-color: #6366f1; }}
          .fbtn[data-filter="Corporate"] {{ border-color: rgba(148,163,184,0.3); color: #94a3b8; }}
          .fbtn[data-filter="Corporate"].active {{ background: rgba(148,163,184,0.15); border-color: #94a3b8; }}
          .fbtn[data-filter="Defense"]  {{ border-color: rgba(239,68,68,0.3);    color: #ef4444; }}
          .fbtn[data-filter="Defense"].active  {{ background: rgba(239,68,68,0.15); border-color: #ef4444; }}
          .tag {{
            font-family: var(--sans);
            font-size: 10.5px;
            padding: 3px 9px;
            border-radius: 999px;
            font-weight: 500;
            letter-spacing: 0.01em;
          }}
          .tag-ai-tech    {{ background: rgba(56,189,248,0.15); color: #38bdf8; }}
          .tag-geopolitical {{ background: rgba(251,146,60,0.15); color: #fb923c; }}
          .tag-macro      {{ background: rgba(167,139,250,0.15); color: #a78bfa; }}
          .tag-finance    {{ background: rgba(52,211,153,0.15); color: #34d399; }}
          .tag-markets    {{ background: rgba(20,184,166,0.15); color: #14b8a6; }}
          .tag-energy     {{ background: rgba(250,204,21,0.15); color: #facc15; }}
          .tag-politics   {{ background: rgba(251,113,133,0.15); color: #fb7185; }}
          .tag-trade      {{ background: rgba(99,102,241,0.15); color: #6366f1; }}
          .tag-corporate  {{ background: rgba(148,163,184,0.15); color: #94a3b8; }}
          .tag-defense    {{ background: rgba(239,68,68,0.15); color: #ef4444; }}
          .block-src {{
            margin-top: 12px;
            padding-top: 10px;
            border-top: 1px dashed var(--border-hairline);
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            gap: 6px;
            font-family: var(--mono);
            font-size: 10px;
            color: var(--ink-40);
            letter-spacing: 0.08em;
            text-transform: uppercase;
          }}
          .src-badge {{
            display: inline-block;
            font-family: var(--sans);
            font-size: 11px;
            padding: 2px 9px;
            border-radius: 4px;
            background: var(--surface-2);
            color: var(--ink-80);
            border: 1px solid var(--border-hairline);
            letter-spacing: 0;
            text-transform: none;
          }}
          .card.hidden {{ display: none; }}
          .top-bar {{
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 16px;
            margin-bottom: 4px;
          }}
          .top-bar-actions {{
            display: flex;
            align-items: center;
            gap: 14px;
          }}
          .next-refresh {{
            font-family: var(--mono);
            font-size: 11px;
            color: var(--ink-60);
            letter-spacing: 0.04em;
            white-space: nowrap;
            cursor: help;
          }}
          @media (max-width: 767px) {{
            .top-bar-actions {{ flex-direction: column; align-items: flex-end; gap: 6px; }}
            .next-refresh {{ font-size: 10px; }}
          }}
          .brand {{
            display: flex;
            flex-direction: column;
            gap: 0;
          }}
          #syncBtn {{
            font-family: var(--mono);
            padding: 9px 18px;
            border-radius: 999px;
            border: 1px solid var(--gold-line);
            background: transparent;
            color: var(--gold-strong);
            font-size: 11px;
            font-weight: 500;
            letter-spacing: 0.1em;
            text-transform: uppercase;
            cursor: pointer;
            transition: all 0.18s ease;
          }}
          #syncBtn:hover {{
            background: var(--gold-soft);
            border-color: var(--gold);
          }}
          #syncBtn.syncing {{ opacity: 0.5; cursor: not-allowed; }}
        </style>
        <script>
          var _lastTs = null;
          function _pollAnalysis() {{
            fetch('/api/analysis-ts')
              .then(function(r) {{ return r.json(); }})
              .then(function(data) {{
                if (_lastTs === null) {{ _lastTs = data.ts; return; }}
                if (data.ts !== _lastTs) {{ window.location.reload(); }}
              }})
              .catch(function() {{}});
          }}
          setInterval(_pollAnalysis, 5000);
          _pollAnalysis();

          function runSync() {{
            var btn = document.getElementById('syncBtn');
            btn.textContent = '↻ Syncing...';
            btn.classList.add('syncing');
            btn.disabled = true;
            fetch('/newsletters/sync').catch(function() {{
                btn.textContent = '↻ Sync';
                btn.classList.remove('syncing');
                btn.disabled = false;
              }});
          }}

          var _mktCharts = {{}};
          function _fmtPrice(p) {{
            if (p == null) return '—';
            if (p >= 1000) return p.toLocaleString(undefined, {{ maximumFractionDigits: 2 }});
            return p.toFixed(2);
          }}
          function _renderOneTicker(tk) {{
            var card = document.querySelector(".mkt-card[data-symbol='" + tk.symbol + "']");
            if (!card) return;
            var priceEl = card.querySelector('.mkt-price');
            var chgEl = card.querySelector('.mkt-change');
            var canvas = card.querySelector('.mkt-chart');
            if (tk.error) {{
              priceEl.textContent = '—';
              chgEl.textContent = 'unavailable';
              chgEl.className = 'mkt-change neutral';
              return;
            }}
            priceEl.textContent = _fmtPrice(tk.last);
            var dir = tk.change > 0 ? 'up' : (tk.change < 0 ? 'down' : 'neutral');
            var arrow = tk.change > 0 ? '▲' : (tk.change < 0 ? '▼' : '·');
            chgEl.className = 'mkt-change ' + dir;
            chgEl.textContent = arrow + ' ' + (tk.change >= 0 ? '+' : '') + _fmtPrice(tk.change) + ' (' + (tk.change_pct >= 0 ? '+' : '') + tk.change_pct.toFixed(2) + '%)';
            var color = dir === 'up' ? '#47e8a0' : (dir === 'down' ? '#e85c47' : 'rgba(232,230,225,0.5)');
            var series = (tk.series || []).map(function(p) {{ return p.c; }});
            var labels = (tk.series || []).map(function(p) {{ return p.t; }});
            if (_mktCharts[tk.symbol]) {{ _mktCharts[tk.symbol].destroy(); }}
            _mktCharts[tk.symbol] = new Chart(canvas.getContext('2d'), {{
              type: 'line',
              data: {{ labels: labels, datasets: [{{
                data: series, borderColor: color, backgroundColor: color + '22',
                borderWidth: 1.5, fill: true, pointRadius: 0, tension: 0.25,
              }}] }},
              options: {{
                responsive: true, maintainAspectRatio: false,
                plugins: {{ legend: {{ display: false }}, tooltip: {{ enabled: false }} }},
                scales: {{ x: {{ display: false }}, y: {{ display: false }} }},
                animation: false,
              }},
            }});
          }}
          function _loadMarkets(force) {{
            fetch('/api/markets' + (force ? '?force=true' : ''))
              .then(function(r) {{ return r.json(); }})
              .then(function(data) {{
                if (data && data.tickers) data.tickers.forEach(_renderOneTicker);
              }})
              .catch(function() {{}});
          }}
          function _loadOneTickerPeriod(symbol, period) {{
            var card = document.querySelector(".mkt-card[data-symbol='" + symbol + "']");
            if (card) {{ card.dataset.period = period; }}
            fetch('/api/markets/' + encodeURIComponent(symbol) + '?period=' + period)
              .then(function(r) {{ return r.json(); }})
              .then(_renderOneTicker)
              .catch(function() {{}});
          }}
          var _mktSummaries = {{}};
          function _periodLabel(p) {{ return ({{'1d':'Today','1w':'This week','1m':'This month','1y':'This year'}})[p] || p; }}
          function _renderPeriodCommentary(symbol, period) {{
            var el = document.querySelector(".mkt-summary[data-symbol='" + symbol + "']");
            if (!el) return;
            var entry = (_mktSummaries[symbol] || {{}})[period];
            if (!entry || !entry.bullets || !entry.bullets.length) {{
              el.innerHTML = "<span class='mkt-summary-loading'>No commentary yet for " + _periodLabel(period) + " — runs after next sync.</span>";
              return;
            }}
            var bulletHtml = "<ul class='mkt-summary-bullets'>" +
              entry.bullets.map(function(b) {{ return "<li>" + b + "</li>"; }}).join('') +
              "</ul>";
            var srcHtml = (entry.sources || []).map(function(s) {{
              return "<span class='src-badge'>" + s + "</span>";
            }}).join('');
            var header = "<div class='mkt-summary-header'>" + _periodLabel(period) + " drivers</div>";
            var peHtml = (entry.plain_english && entry.plain_english.length)
              ? "<div class='plain-english mkt-summary-plain'><span class='pe-label'>Plain English</span> " + entry.plain_english + "</div>"
              : "";
            el.innerHTML = header + bulletHtml + peHtml + (srcHtml ? "<div class='mkt-summary-sources'>Sources: " + srcHtml + "</div>" : "");
          }}
          function _loadChartSummaries() {{
            fetch('/api/markets/summaries')
              .then(function(r) {{ return r.json(); }})
              .then(function(data) {{
                _mktSummaries = (data && data.summaries) || {{}};
                document.querySelectorAll('.mkt-card').forEach(function(card) {{
                  var sym = card.dataset.symbol;
                  var period = card.dataset.period || '1d';
                  _renderPeriodCommentary(sym, period);
                }});
              }})
              .catch(function() {{}});
          }}
          document.addEventListener('DOMContentLoaded', function() {{
            // Tab switching
            document.querySelectorAll('.tab-btn').forEach(function(btn) {{
              btn.addEventListener('click', function() {{
                var tab = btn.dataset.tab;
                document.querySelectorAll('.tab-btn').forEach(function(b) {{ b.classList.toggle('active', b === btn); }});
                document.querySelectorAll('.tab-panel').forEach(function(p) {{
                  p.classList.toggle('active', p.id === 'tab-' + tab);
                }});
                try {{ localStorage.setItem('pulse_active_tab', tab); }} catch(e) {{}}
              }});
            }});
            try {{
              var saved = localStorage.getItem('pulse_active_tab');
              if (saved) {{
                var b = document.querySelector(".tab-btn[data-tab='" + saved + "']");
                if (b) b.click();
              }}
            }} catch(e) {{}}
            // Per-card period buttons
            document.querySelectorAll('.mkt-card').forEach(function(card) {{
              card.querySelectorAll('.mkt-period').forEach(function(btn) {{
                btn.addEventListener('click', function() {{
                  card.querySelectorAll('.mkt-period').forEach(function(b) {{ b.classList.toggle('active', b === btn); }});
                  _loadOneTickerPeriod(card.dataset.symbol, btn.dataset.period);
                  _renderPeriodCommentary(card.dataset.symbol, btn.dataset.period);
                }});
              }});
            }});
          }});
          _loadMarkets(false);
          _loadChartSummaries();
          setInterval(function() {{ _loadMarkets(false); }}, 60000);
          document.addEventListener('DOMContentLoaded', function() {{
            var active = 'ALL';
            document.getElementById('filterBar').addEventListener('click', function(e) {{
              var btn = e.target.closest('.fbtn');
              if (!btn) return;
              active = btn.dataset.filter;
              document.querySelectorAll('.fbtn').forEach(function(b) {{
                b.classList.toggle('active', b.dataset.filter === active);
              }});
              document.querySelectorAll('#newsletterGrid .card').forEach(function(card) {{
                if (active === 'ALL') {{
                  card.classList.remove('hidden');
                }} else {{
                  var tags = (card.dataset.tags || '').toUpperCase().split(' ');
                  card.classList.toggle('hidden', !tags.includes(active.toUpperCase()));
                }}
              }});
            }});
          }});

          // ---------- Dossiers ----------
          var _dossierEntities = [];
          var _activeDossier = null;

          function _kindColor(kind) {{
            return ({{'company':'#c9a35d','sector':'#6ec3b0','concept':'#a995c3'}})[kind] || '#c9a35d';
          }}

          function _renderSidebar() {{
            var followedEl = document.getElementById('dossiersFollowed');
            var discoverEl = document.getElementById('dossiersDiscover');
            if (!followedEl || !discoverEl) return;
            followedEl.innerHTML = _dossierEntities.map(function(e) {{
              var sel = (_activeDossier === e.id) ? ' active' : '';
              return "<li class='d-item" + sel + "' data-id='" + e.id + "' data-kind='" + e.kind + "'>" +
                     "<span class='d-rail' style='background:" + _kindColor(e.kind) + "'></span>" +
                     "<span class='d-name'>" + e.name + "</span>" +
                     "<span class='d-count'>" + (e.mention_count || 0) + "</span></li>";
            }}).join('');
            followedEl.querySelectorAll('.d-item').forEach(function(li) {{
              li.addEventListener('click', function() {{
                _activeDossier = parseInt(li.dataset.id, 10);
                _renderSidebar();
                _loadDossierPage(_activeDossier);
              }});
            }});
            // Discover bucket
            fetch('/api/dossiers/discover').then(function(r) {{ return r.json(); }}).then(function(d) {{
              var cands = (d && d.candidates) || [];
              discoverEl.innerHTML = cands.map(function(c) {{
                return "<li class='d-item discover-row' data-kind='" + c.kind + "' data-key='" + c.key + "' data-name='" + c.name + "'>" +
                       "<span class='d-rail' style='background:" + _kindColor(c.kind) + ";opacity:0.5'></span>" +
                       "<span class='d-name'>" + c.name + "</span>" +
                       "<span class='d-count'>" + c.mention_count + "</span></li>";
              }}).join('');
              discoverEl.querySelectorAll('.discover-row').forEach(function(li) {{
                li.addEventListener('click', function() {{
                  _followNew(li.dataset.kind, li.dataset.key, li.dataset.name);
                }});
              }});
            }});
          }}

          function _loadDossiers() {{
            fetch('/api/dossiers').then(function(r) {{ return r.json(); }}).then(function(d) {{
              _dossierEntities = (d && d.followed) || [];
              if (!_activeDossier && _dossierEntities.length) {{
                _activeDossier = _dossierEntities[0].id;
                _loadDossierPage(_activeDossier);
              }}
              _renderSidebar();
            }});
          }}

          function _loadDossierPage(entityId, pollAttempt) {{
            var page = document.getElementById('dossiersPage');
            if (!page) return;
            if (!pollAttempt) page.innerHTML = "<div class='dossiers-empty'>Loading…</div>";
            fetch('/api/dossiers/' + entityId).then(function(r) {{ return r.json(); }}).then(function(d) {{
              if (!d || !d.entity) return;
              // Stop polling if user navigated away from this dossier
              if (_activeDossier !== entityId) return;
              var e = d.entity;
              var s = d.snapshot || {{}};
              var total = d.total_mentions || (d.mentions || []).length;
              var loaded = (d.mentions || []).length;
              var mentions = _renderMentionsHtml(d.mentions || []);
              page.className = 'dossiers-page kind-' + e.kind;
              var showMoreBtn = (d.has_more)
                ? "<button class='d-showmore' data-id='" + e.id + "' data-offset='" + loaded + "'>Show more (" + (total - loaded) + " more)</button>"
                : "";
              // Snapshot is null right after a follow — background warmup is still
              // running. Show an explicit "Analyzing…" banner and poll every 2.5s
              // for up to ~60s until the snapshot lands.
              var attempt = pollAttempt || 0;
              var analyzingBanner = "";
              if (!d.snapshot && attempt < 24) {{
                analyzingBanner = "<div class='d-analyzing'>Analyzing newsletters for this dossier… <span class='d-analyzing-dots'>•••</span></div>";
                setTimeout(function() {{
                  if (_activeDossier === entityId) _loadDossierPage(entityId, attempt + 1);
                }}, 2500);
              }}
              page.innerHTML =
                "<header class='d-head'>" +
                  "<div><h2 class='d-title'>" + e.name + "</h2><span class='d-kind' style='color:" + _kindColor(e.kind) + "'>" + e.kind + " · " + e.key + "</span></div>" +
                  "<button class='d-unfollow' onclick='_unfollow(" + e.id + ")'>Unfollow</button>" +
                "</header>" +
                analyzingBanner +
                (s.overview ? "<p class='d-overview'>" + s.overview + "</p>" : "") +
                (s.plain_english ? "<div class='plain-english d-plain'><span class='pe-label'>Plain English</span> " + s.plain_english + "</div>" : "") +
                (s.bull_thesis ? "<div class='analysis-grid'><div class='analysis-block bull'><strong>Bull thesis</strong><p>" + s.bull_thesis + "</p></div><div class='analysis-block bear'><strong>Bear thesis</strong><p>" + (s.bear_thesis || '') + "</p></div></div>" : "") +
                "<div class='d-mentions-label'>Mentions (showing " + loaded + " of " + total + ")</div>" +
                "<ul class='d-mentions' id='dMentionsList'>" + mentions + "</ul>" +
                showMoreBtn;
              var btn = page.querySelector('.d-showmore');
              if (btn) btn.addEventListener('click', _loadMoreMentions);
            }});
          }}

          function _renderMentionsHtml(arr) {{
            return arr.map(function(m) {{
              var date = (m.newsletter_received_at || m.tagged_at || '').slice(0,10);
              var title = m.newsletter_title || '';
              var src = m.newsletter_source || '';
              var nlId = m.newsletter_id || '';
              var titleHtml;
              if (title) {{
                titleHtml = "<a class='d-mention-title' href='https://mail.google.com/mail/u/gl3064@stern.nyu.edu/#all/" + nlId + "' target='_blank' rel='noreferrer'>" + title + "</a>";
              }} else if (m.newsletter_subject) {{
                titleHtml = "<span class='d-mention-title archived'>" + m.newsletter_subject + " <span class='d-mention-archived-tag'>archived</span></span>";
              }} else {{
                titleHtml = "<span class='d-mention-title archived'>(archived newsletter)</span>";
              }}
              var srcHtml = src ? "<span class='d-mention-src'>" + src + "</span>" : "";
              var bodyHtml = '';
              if (Array.isArray(m.bullets)) {{
                if (m.bullets.length) {{
                  bodyHtml = "<ul class='d-mention-bullets'>" +
                    m.bullets.map(function(b) {{ return "<li>" + b + "</li>"; }}).join('') +
                    "</ul>";
                }}
              }} else if (m.quote) {{
                bodyHtml = "<div class='d-mention-quote'>" + m.quote + "</div>";
              }}
              return "<li class='d-mention'>" +
                       "<div class='d-mention-head'>" +
                         "<span class='d-mention-date'>" + date + "</span>" +
                         titleHtml + srcHtml +
                       "</div>" +
                       bodyHtml +
                     "</li>";
            }}).join('');
          }}

          function _loadMoreMentions(evt) {{
            var btn = evt.currentTarget;
            var id = parseInt(btn.dataset.id, 10);
            var offset = parseInt(btn.dataset.offset, 10);
            btn.textContent = 'Loading…';
            btn.disabled = true;
            fetch('/api/dossiers/' + id + '?offset=' + offset + '&limit=20')
              .then(function(r) {{ return r.json(); }}).then(function(d) {{
                var list = document.getElementById('dMentionsList');
                if (list) list.insertAdjacentHTML('beforeend', _renderMentionsHtml(d.mentions || []));
                var newOffset = offset + (d.mentions || []).length;
                var label = document.querySelector('.d-mentions-label');
                if (label) label.textContent = 'Mentions (showing ' + newOffset + ' of ' + d.total_mentions + ')';
                if (d.has_more) {{
                  btn.dataset.offset = newOffset;
                  btn.textContent = 'Show more (' + (d.total_mentions - newOffset) + ' more)';
                  btn.disabled = false;
                }} else {{
                  btn.remove();
                }}
              }});
          }}

          function _followNew(kind, key, name) {{
            fetch('/api/dossiers/follow', {{
              method: 'POST',
              headers: {{'Content-Type': 'application/json'}},
              body: JSON.stringify({{kind: kind, key: key, name: name || key}}),
            }}).then(function(r) {{ return r.json(); }}).then(function(d) {{
              _activeDossier = d.id;
              _loadDossiers();
              _loadDossierPage(d.id);
            }});
          }}

          function _unfollow(id) {{
            fetch('/api/dossiers/' + id + '/unfollow', {{method: 'POST'}}).then(function() {{
              _activeDossier = null;
              document.getElementById('dossiersPage').innerHTML = "<div class='dossiers-empty'>Select a dossier on the left.</div>";
              _loadDossiers();
            }});
          }}

          document.addEventListener('DOMContentLoaded', function() {{
            _loadDossiers();
            var addBtn = document.getElementById('dossierAddBtn');
            if (addBtn) {{
              addBtn.addEventListener('click', function() {{
                var inp = document.getElementById('dossierAddInput');
                var kindSel = document.getElementById('dossierAddKind');
                var val = (inp.value || '').trim();
                if (!val) return;
                _followNew(kindSel.value, val, val);
                inp.value = '';
              }});
            }}
          }});

          // Next-refresh countdown — reads the ISO timestamp injected at page
          // render time, formats as 'Next refresh: Mon 8:00 AM (in 8h 19m)',
          // re-renders every 60s so the relative time stays current without
          // a full page reload.
          function updateNextRefresh() {{
            var el = document.getElementById('nextRefresh');
            if (!el) return;
            var iso = el.getAttribute('data-iso');
            if (!iso) {{ el.textContent = ''; return; }}
            var target = new Date(iso);
            var now = new Date();
            var ms = target - now;
            if (ms <= 0) {{ el.textContent = 'Refreshing soon...'; return; }}
            var totalMin = Math.floor(ms / 60000);
            var h = Math.floor(totalMin / 60);
            var m = totalMin % 60;
            var dateStr = target.toLocaleString('en-US', {{
              weekday: 'short', hour: 'numeric', minute: '2-digit',
              timeZone: 'America/New_York'
            }});
            var rel;
            if (h >= 24) {{
              rel = Math.floor(h / 24) + 'd ' + (h % 24) + 'h';
            }} else if (h >= 1) {{
              rel = h + 'h ' + m + 'm';
            }} else {{
              rel = m + 'm';
            }}
            // Compact format on phone widths — full "Next refresh: ..." eats
            // too much top-bar room at 375px.
            var compact = window.innerWidth <= 767;
            if (compact) {{
              el.textContent = 'Next: ' + dateStr + ' (' + rel + ')';
            }} else {{
              el.textContent = 'Next refresh: ' + dateStr + ' ET (in ' + rel + ')';
            }}
          }}
          // Wrap in DOMContentLoaded so the element exists when we first read
          // it — the <script> sits in <head> so naked calls run too early.
          if (document.readyState === 'loading') {{
            document.addEventListener('DOMContentLoaded', updateNextRefresh);
          }} else {{
            updateNextRefresh();
          }}
          setInterval(updateNextRefresh, 60000);

          // Tap-to-toggle jargon/vault-ref tooltips.
          //
          // Desktop: CSS :hover handles the ::after popover (unchanged).
          //
          // Touch devices: the ::after popover was breaking because some
          // ancestor has a transform/filter that turns position:fixed into
          // position:absolute (CSS spec quirk). So on touch we render a
          // singleton tooltip element appended directly to <body> — that
          // element can't be affected by any ancestor transform. The .is-open
          // class still toggles for the term's gold-highlight state.
          var _isTouch = window.matchMedia('(hover: none)').matches
                       || ('ontouchstart' in window);
          var _mobileTipEl = null;
          function _getMobileTip() {{
            if (!_mobileTipEl) {{
              _mobileTipEl = document.createElement('div');
              _mobileTipEl.id = 'mobile-tip';
              document.body.appendChild(_mobileTipEl);
            }}
            return _mobileTipEl;
          }}
          function _showMobileTip(text) {{
            var el = _getMobileTip();
            el.textContent = text;
            // Force reflow so the transition fires reliably even when reusing
            // the same element for consecutive taps on different terms.
            // eslint-disable-next-line no-unused-expressions
            el.offsetHeight;
            el.classList.add('is-visible');
          }}
          function _hideMobileTip() {{
            if (_mobileTipEl) _mobileTipEl.classList.remove('is-visible');
          }}
          document.addEventListener('click', function(e) {{
            var tip = e.target.closest && e.target.closest('.jargon, .vault-ref');
            var openSel = '.jargon.is-open, .vault-ref.is-open';
            if (tip) {{
              var wasOpen = tip.classList.contains('is-open');
              document.querySelectorAll(openSel).forEach(function(el) {{
                if (el !== tip) el.classList.remove('is-open');
              }});
              if (!wasOpen) {{
                tip.classList.add('is-open');
                if (_isTouch) {{
                  var text = tip.getAttribute('data-tip') || '';
                  if (text) _showMobileTip(text);
                }}
              }} else {{
                if (_isTouch) _hideMobileTip();
              }}
            }} else {{
              document.querySelectorAll(openSel).forEach(function(el) {{
                el.classList.remove('is-open');
              }});
              if (_isTouch) _hideMobileTip();
            }}
          }});
        </script>
      </head>
      <body>
        <main>
          <div class="top-bar">
            <div class="brand">
              <h1>Pulse<span class="dot"></span></h1>
              <div class="sub">{dateline} &nbsp;·&nbsp; Markets brief &nbsp;·&nbsp; NYU Stern edition</div>
            </div>
            <div class="top-bar-actions">
              <span class="next-refresh" id="nextRefresh" data-iso="{next_refresh_iso}" title="The auto-refresh schedule: weekdays 8/11/14/17 ET. Manual ↻ Sync works anytime."></span>
              <button id="syncBtn" onclick="runSync()">↻ Sync</button>
            </div>
          </div>
          <div class="tab-bar">
            <button class="tab-btn active" data-tab="news">Newsletters</button>
            <button class="tab-btn" data-tab="markets">Markets</button>
            <button class="tab-btn" data-tab="dossiers">Dossiers</button>
          </div>
          <div id="tab-news" class="tab-panel active">
            <section class="briefing">
              <h2>{overarching_analysis["title"]}</h2>
              <p class="tldr">{md_inline(overarching_analysis["tldr"])}</p>
              {plain_tldr_html}
              {vocab_html}
              {what_html}
              {mkts_html}
              {watch_html}
              {bull_bear_html}
              {nq_html}
              {stern_html}
            </section>
            <h3>Newsletters</h3>
            <div class="filter-bar" id="filterBar">
              {filter_bar_html}
            </div>
            <div class="grid" id="newsletterGrid">
              {newsletter_html}
            </div>
          </div>
          <div id="tab-markets" class="tab-panel">
            {markets_html}
          </div>
          <div id="tab-dossiers" class="tab-panel">
            <div class="dossiers-shell">
              <aside class="dossiers-side" id="dossiersSide">
                <div class="dossiers-side-header">Followed</div>
                <ul class="dossiers-list" id="dossiersFollowed"></ul>
                <div class="dossiers-side-header" style="margin-top:18px">Discover</div>
                <ul class="dossiers-list discover" id="dossiersDiscover"></ul>
                <div class="dossiers-add">
                  <input id="dossierAddInput" placeholder="Ticker or term"/>
                  <select id="dossierAddKind">
                    <option value="company">Company</option>
                    <option value="sector">Sector</option>
                    <option value="concept">Concept</option>
                  </select>
                  <button id="dossierAddBtn">+ Follow</button>
                </div>
              </aside>
              <main class="dossiers-page" id="dossiersPage">
                <div class="dossiers-empty">Select a dossier on the left.</div>
              </main>
            </div>
          </div>
        </main>
      </body>
    </html>
    """

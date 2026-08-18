from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from ..db import get_connection


BRIEFING_KEY = "daily_overview"


def top_categories(stories: list[dict[str, Any]], limit: int = 3) -> list[str]:
    counts = Counter(story.get("nlTier") or story.get("category") or "other" for story in stories)
    return [name for name, _ in counts.most_common(limit)]


def top_sources(stories: list[dict[str, Any]], limit: int = 3) -> list[str]:
    counts = Counter(story.get("source", "Unknown") for story in stories)
    return [name for name, _ in counts.most_common(limit)]


def top_story_lines(stories: list[dict[str, Any]], limit: int = 5) -> list[str]:
    lines = []
    for story in stories[:limit]:
        title = story.get("title", "Untitled")
        source = story.get("source", "Unknown")
        summary = story.get("summary") or story.get("description") or "No summary available."
        lines.append(f"{source}: {title} — {summary}")
    return lines


def sort_for_briefing(newsletters: list[dict[str, Any]], rss: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def score(story: dict[str, Any]) -> tuple[int, str]:
        tier = story.get("nlTier") or story.get("category") or "other"
        tier_score = {
            "geopolitical": 5,
            "finance": 4,
            "macro": 4,
            "markets": 3,
            "earnings": 3,
            "tech": 2,
            "crypto": 2,
            "mixed": 2,
            "other": 1,
            "lifestyle": 0,
        }.get(tier, 1)
        source_bonus = 3 if story.get("isNewsletter") else 0
        return (tier_score + source_bonus, story.get("pubDate") or "")

    return sorted(newsletters + rss, key=score, reverse=True)


def build_briefing(newsletters: list[dict[str, Any]], rss: list[dict[str, Any]]) -> dict[str, Any]:
    combined = sort_for_briefing(newsletters, rss)

    category_focus = top_categories(combined)
    source_focus = top_sources(combined)
    top_lines = top_story_lines(combined)

    if newsletters:
        lead = "Newsletter coverage is available, so Pulse has deeper context than RSS alone."
    else:
        lead = "Pulse is currently relying on RSS only, so this briefing is breadth-first rather than deep."

    tldr = (
        f"{lead} The flow today is centered on {', '.join(category_focus) if category_focus else 'mixed themes'}, "
        f"with the heaviest usable context coming from {', '.join(source_focus) if source_focus else 'your tracked sources'}."
    )

    watch_items = []
    if any("geopolitical" == (story.get("nlTier") or story.get("category")) for story in combined):
        watch_items.append("Watch for geopolitical escalation feeding into oil, rates, and risk sentiment.")
    if any("finance" == (story.get("nlTier") or story.get("category")) or "macro" == story.get("category") for story in combined):
        watch_items.append("Watch whether macro headlines change the market's view on Fed timing, growth, or inflation.")
    if any("markets" == (story.get("nlTier") or story.get("category")) for story in combined):
        watch_items.append("Watch whether market stories confirm broad risk-off behavior or just isolated sector rotation.")
    if not watch_items:
        watch_items.append("Watch which themes keep repeating across sources, because repetition often signals what markets are pricing most aggressively.")

    return {
        "title": "Pulse Briefing",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "story_count": len(combined),
        "newsletter_count": len(newsletters),
        "rss_count": len(rss),
        "top_themes": category_focus,
        "top_sources": source_focus,
        "tldr": tldr,
        "market_view": "This cached briefing now prioritizes newsletter context first and uses RSS as supporting breadth. It is still rule-based, so treat it as a structured prep layer rather than a final analyst view.",
        "watch_list": watch_items[:3],
        "top_stories": top_lines,
    }


def save_briefing(payload: dict[str, Any]) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO briefings (briefing_key, title, payload_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(briefing_key) DO UPDATE SET
                title=excluded.title,
                payload_json=excluded.payload_json,
                updated_at=excluded.updated_at
            """,
            (
                BRIEFING_KEY,
                payload["title"],
                json.dumps(payload),
                payload["updated_at"],
            ),
        )


def load_briefing() -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT payload_json FROM briefings WHERE briefing_key = ?",
            (BRIEFING_KEY,),
        ).fetchone()

    if not row:
        return None
    return json.loads(row["payload_json"])


def get_or_build_briefing(newsletters: list[dict[str, Any]], rss: list[dict[str, Any]], refresh: bool = False) -> dict[str, Any]:
    cached = load_briefing()
    if cached and not refresh:
        return cached

    briefing = build_briefing(newsletters, rss)
    save_briefing(briefing)
    return briefing

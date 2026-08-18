from __future__ import annotations

import html
import re
from typing import Any

import feedparser
import requests


RSS_FEEDS = [
    {
        "name": "Reuters Business",
        "url": "https://feeds.reuters.com/reuters/businessNews",
        "icon": "R",
    },
    {
        "name": "Reuters Markets",
        "url": "https://feeds.reuters.com/reuters/marketsNews",
        "icon": "R",
    },
    {
        "name": "NYT Business",
        "url": "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
        "icon": "NYT",
    },
    {
        "name": "WSJ Markets",
        "url": "https://feeds.a.wsj.com/rss/RSSMarketsMain.xml",
        "icon": "WSJ",
    },
    {
        "name": "CNBC Top News",
        "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
        "icon": "C",
    },
    {
        "name": "FT Markets",
        "url": "https://www.ft.com/rss/markets",
        "icon": "FT",
    },
    {
        "name": "Bloomberg Markets",
        "url": "https://feeds.bloomberg.com/markets/news.rss",
        "icon": "B",
    },
]

TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def cat_rss(title: str, desc: str) -> str:
    text = f"{title} {desc}".lower()
    if any(
        token in text
        for token in [
            "fed",
            "fomc",
            "inflation",
            "cpi",
            "ppi",
            "gdp",
            "unemployment",
            "treasury",
            "yield",
            "powell",
            "recession",
            "rate",
        ]
    ):
        return "macro"
    if any(
        token in text
        for token in ["earning", "revenue", "profit", "eps", "guidance", "forecast"]
    ):
        return "earnings"
    if any(
        token in text
        for token in ["s&p", "nasdaq", "dow", "stock", "bull", "bear", "ipo", "futures", "vix"]
    ):
        return "markets"
    if any(
        token in text
        for token in ["war", "tariff", "sanction", "china", "russia", "ukraine", "nato", "opec", "iran", "hormuz"]
    ):
        return "geopolitical"
    if any(
        token in text
        for token in [
            "artificial intelligence",
            "tech",
            "apple",
            "google",
            "meta",
            "nvidia",
            "microsoft",
            "semiconductor",
            "openai",
        ]
    ):
        return "tech"
    if any(token in text for token in ["bitcoin", "crypto", "ethereum", "blockchain"]):
        return "crypto"
    return "other"


def clean_text(value: str) -> str:
    text = html.unescape(value or "")
    text = TAG_RE.sub(" ", text)
    text = text.replace("\xa0", " ")
    text = WHITESPACE_RE.sub(" ", text)
    return text.strip(" -\n\t")


def shorten_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value

    clipped = value[:limit].rsplit(" ", 1)[0].strip()
    if not clipped:
        clipped = value[:limit].strip()
    return f"{clipped}..."


def build_summary(title: str, description: str) -> str:
    if not description:
        return "No summary available."

    text = description
    lowered_title = title.lower().strip()
    lowered_text = text.lower().strip()
    if lowered_text.startswith(lowered_title):
        text = text[len(title) :].lstrip(" :-|")

    sentences = [part.strip() for part in SENTENCE_SPLIT_RE.split(text) if part.strip()]
    if not sentences:
        return shorten_text(text, 180)

    if len(sentences[0]) >= 90 or len(sentences) == 1:
        return shorten_text(sentences[0], 180)

    return shorten_text(" ".join(sentences[:2]), 220)


def build_why_it_matters(category: str, title: str, description: str, source: str) -> str:
    text = f"{title}. {description}".strip()
    concise = shorten_text(text, 170)

    templates = {
        "macro": f"This matters because macro stories change the backdrop for rates, inflation, hiring, and consumer demand. For a business student, ask how this could affect Fed expectations, company costs, and market sentiment. Key context: {concise}",
        "earnings": f"This matters because earnings stories show whether companies are actually delivering on growth, margins, and guidance. For interviews or investing, focus on what this says about demand, pricing power, and management credibility. Key context: {concise}",
        "markets": f"This matters because market stories often reveal where investors are rotating risk and what themes are driving price action. For Pulse, this is useful as a read on positioning, sentiment, and what could move indexes like NQ next. Key context: {concise}",
        "geopolitical": f"This matters because geopolitical shocks usually hit markets through energy, supply chains, regulation, and risk appetite. For a business student, the useful question is which sectors, trade routes, or policy assumptions get repriced if this story grows. Key context: {concise}",
        "tech": f"This matters because tech and AI stories often affect valuations, capex, labor productivity, and competitive advantage. For recruiting and case interviews, it helps to connect the headline to adoption, monetization, and who wins or loses if the trend persists. Key context: {concise}",
        "crypto": f"This matters because crypto stories often signal shifts in regulation, risk appetite, and financial product innovation. The useful lens is whether this changes institutional adoption, market structure, or speculative behavior. Key context: {concise}",
        "other": f"This matters because even non-market headlines can signal changes in consumer behavior, regulation, or industry structure. The useful habit is to ask what business model or decision-maker is most affected. Key context: {concise}",
    }
    return templates.get(category, templates["other"])


def fetch_rss(url: str, name: str, icon: str) -> list[dict[str, Any]]:
    try:
        response = requests.get(url, timeout=20, headers={"User-Agent": "Pulse/0.1"})
        response.raise_for_status()
        parsed = feedparser.parse(response.text)
    except requests.RequestException:
        return []

    stories: list[dict[str, Any]] = []
    for item in parsed.entries[:15]:
        title = (getattr(item, "title", "") or "").strip()
        raw_description = getattr(item, "summary", "") or getattr(item, "description", "") or ""
        description = clean_text(raw_description)
        summary = build_summary(title, description)
        stories.append(
            {
                "title": title,
                "description": shorten_text(description, 320),
                "summary": summary,
                "whyItMatters": build_why_it_matters(
                    cat_rss(title, description), title, description, name
                ),
                "link": getattr(item, "link", None),
                "pubDate": getattr(item, "published", None),
                "source": name,
                "sourceIcon": icon,
                "category": cat_rss(title, description),
                "isNewsletter": False,
            }
        )
    return stories


def dedupe_stories(stories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for story in stories:
        key = "".join(ch for ch in story["title"].lower() if ch.isalnum())[:50]
        if key in seen:
            continue
        seen.add(key)
        unique.append(story)
    return unique


def load_dashboard_data() -> list[dict[str, Any]]:
    stories: list[dict[str, Any]] = []
    for feed in RSS_FEEDS:
        stories.extend(fetch_rss(feed["url"], feed["name"], feed["icon"]))
    return dedupe_stories(stories)

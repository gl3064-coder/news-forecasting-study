from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

from ..db import get_connection


ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(dotenv_path=ENV_PATH, override=True)

SUMMARY_ENGINE = "heuristic"
LAST_SUMMARY_ERROR: dict[str, Any] | None = None
SUMMARY_SCHEMA = {
    "name": "pulse_story_summary",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "summary": {"type": "string"},
            "main_points": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 2,
                "maxItems": 4,
            },
            "why_it_matters": {"type": "string"},
            "market_impact": {"type": "string"},
            "framing": {"type": "string"},
        },
        "required": ["summary", "main_points", "why_it_matters", "market_impact", "framing"],
    },
    "strict": True,
}


def content_hash(*parts: str) -> str:
    joined = "\n".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def model_summary_budget() -> int:
    try:
        return max(0, int(os.getenv("PULSE_MODEL_SUMMARY_BUDGET", "50")))
    except ValueError:
        return 15


def model_subject_allowlist() -> list[str]:
    raw = os.getenv(
        "PULSE_MODEL_SUBJECT_ALLOWLIST",
        "The Morning:,The World:,WSJ Politics:,DealBook,What's News,Heard on the Street,Capital Journal",
    )
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


def split_sentences(text: str) -> list[str]:
    raw = text.replace("\n", " ")
    bits = []
    current = []
    for char in raw:
        current.append(char)
        if char in ".!?":
            sentence = "".join(current).strip()
            if sentence:
                bits.append(sentence)
            current = []
    tail = "".join(current).strip()
    if tail:
        bits.append(tail)
    return [item.strip() for item in bits if item.strip()]


def normalize_text(text: str) -> str:
    return " ".join(text.replace("\n", " ").split()).strip()


def trim_text(text: str, limit: int = 220) -> str:
    if len(text) <= limit:
        return text
    clipped = text[:limit]
    last_stop = max(clipped.rfind("."), clipped.rfind("!"), clipped.rfind("?"))
    if last_stop > 120:
        return clipped[: last_stop + 1].strip()
    last_space = clipped.rfind(" ")
    if last_space > 0:
        return clipped[:last_space].strip() + "..."
    return clipped.strip() + "..."


def sentence_score(sentence: str, category: str, title: str) -> float:
    lowered = sentence.lower()
    score = 0.0
    important_terms = {
        "geopolitical": ["iran", "war", "oil", "shipping", "sanction", "missile", "military", "israel", "china", "russia"],
        "finance": ["fed", "inflation", "rates", "economy", "yield", "growth", "consumer", "jobs", "margin"],
        "macro": ["fed", "inflation", "rates", "economy", "yield", "growth", "consumer", "jobs"],
        "markets": ["stocks", "nasdaq", "s&p", "market", "investors", "shares", "selloff", "rally"],
        "earnings": ["earnings", "revenue", "profit", "guidance", "sales", "forecast"],
        "tech": ["ai", "nvidia", "openai", "semiconductor", "chip", "software"],
    }.get(category, ["market", "economy", "company", "policy"])

    for term in important_terms:
        if term in lowered:
            score += 2

    if any(ch.isdigit() for ch in sentence):
        score += 1.5
    if len(sentence) >= 50:
        score += 1
    if len(sentence) > 220:
        score -= 1.5

    title_words = {word for word in title.lower().split() if len(word) > 4}
    overlap = sum(1 for word in title_words if word in lowered)
    score += min(overlap, 3) * 0.75

    boilerplate_flags = [
        "newsletter",
        "coverage you might be interested in",
        "based on what you've read",
        "good morning",
        "view in browser",
        "sponsored by",
    ]
    if any(flag in lowered for flag in boilerplate_flags):
        score -= 4

    return score


def choose_best_sentences(sentences: list[str], category: str, title: str, limit: int = 2) -> list[str]:
    cleaned = [normalize_text(sentence) for sentence in sentences if normalize_text(sentence)]
    ranked = sorted(
        cleaned,
        key=lambda sentence: (sentence_score(sentence, category, title), -cleaned.index(sentence)),
        reverse=True,
    )

    chosen: list[str] = []
    for candidate in ranked:
        if any(candidate == existing for existing in chosen):
            continue
        chosen.append(candidate)
        if len(chosen) >= limit:
            break

    ordered = [sentence for sentence in cleaned if sentence in chosen]
    return ordered[:limit]


def _is_url_noise(sentence: str) -> bool:
    s = sentence.strip()
    return bool(re.search(r"https?://\S{40,}|com_[a-z_]+[-\w]{20,}|[A-Za-z0-9_%]{40,}", s)) or s.startswith("com_")


def relevant_context(text: str, title: str, category: str, max_sentences: int = 15, max_chars: int = 3500) -> str:
    sentences = split_sentences(text)
    ranked = sorted(
        [normalize_text(sentence) for sentence in sentences if normalize_text(sentence) and not _is_url_noise(sentence)],
        key=lambda sentence: sentence_score(sentence, category, title),
        reverse=True,
    )

    selected: list[str] = []
    total = 0
    for sentence in ranked:
        if sentence in selected:
            continue
        if total + len(sentence) > max_chars and selected:
            break
        selected.append(sentence)
        total += len(sentence)
        if len(selected) >= max_sentences:
            break

    if not selected:
        return trim_text(normalize_text(text), max_chars)

    ordered = [sentence for sentence in sentences if normalize_text(sentence) in selected]
    normalized_ordered = [normalize_text(sentence) for sentence in ordered if normalize_text(sentence)]
    return " ".join(normalized_ordered)


def market_angle(category: str, title: str, body: str) -> str:
    text = f"{title} {body}".lower()
    if category in {"geopolitical"} or any(token in text for token in ["oil", "iran", "war", "sanction", "shipping"]):
        return "This could matter for markets through energy prices, risk sentiment, and global supply-chain repricing."
    if category in {"finance", "macro"} or any(token in text for token in ["fed", "inflation", "rates", "yield", "economy"]):
        return "This could matter for markets by changing expectations around growth, inflation, interest rates, and valuation multiples."
    if category in {"markets", "earnings"} or any(token in text for token in ["stocks", "earnings", "profit", "guidance", "nasdaq"]):
        return "This could matter for markets by shifting positioning, earnings expectations, and short-term sector leadership."
    if category in {"tech"} or any(token in text for token in ["ai", "nvidia", "openai", "chip", "semiconductor"]):
        return "This could matter for markets through tech multiples, capex expectations, and who investors think will capture the next leg of growth."
    return "This matters if it changes consumer behavior, regulation, or the broader narrative investors are using to price risk."


def business_angle(source: str, category: str, title: str, body: str) -> str:
    text = f"{title} {body}".lower()
    if category == "geopolitical":
        return "For a business student, focus on second-order effects: which industries, commodities, or policy assumptions get repriced if this situation expands."
    if category in {"finance", "macro"}:
        return "For a business student, use this as practice connecting a headline to rates, margins, hiring, and company decision-making."
    if category in {"markets", "earnings"}:
        return "For a business student, ask what this says about positioning, management credibility, and where investors think growth will come from next."
    if category == "tech":
        return "For a business student, connect the story to adoption, monetization, competitive advantage, and whether the winners have durable moats."
    if "wsj" in source.lower():
        return "For a business student, pay attention to how the piece frames incentives, institutions, and the business consequences behind the headline."
    return "For a business student, the useful habit is turning the headline into a business mechanism: who benefits, who gets hurt, and why now."


def heuristic_story_summary(story: dict[str, Any]) -> dict[str, Any]:
    title = story.get("title", "Untitled")
    body = story.get("fullContent") or ""
    fallback_summary = story.get("summary") or story.get("description") or ""
    category = story.get("nlTier") or story.get("category") or "other"
    source = story.get("source", "Unknown")
    sentences = split_sentences(body or fallback_summary)
    best_sentences = choose_best_sentences(sentences, category, title, limit=2)
    main_points = [trim_text(item, 170) for item in best_sentences if item][:3]
    if not main_points:
        if fallback_summary:
            main_points = [trim_text(fallback_summary, 170)]
        else:
            main_points = [trim_text(title, 160)]

    summary = trim_text(" ".join(main_points), 260)
    return {
        "story_id": story.get("emailId") or story.get("link") or title,
        "story_type": "newsletter" if story.get("isNewsletter") else "rss",
        "source": source,
        "title": title,
        "summary": summary,
        "main_points": main_points,
        "why_it_matters": business_angle(source, category, title, body),
        "market_impact": market_angle(category, title, body),
        "framing": f"{source} is emphasizing the {category} angle of this story rather than presenting it as raw news alone.",
        "engine": SUMMARY_ENGINE,
        "engine_label": "heuristic",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Anthropic API ──────────────────────────────────────────────────────────────

def _call_anthropic(system: str, user: str, model: str, max_tokens: int = 1400, timeout: int = 180,
                    cache_system: bool = False) -> str | None:
    """Call Anthropic. When cache_system=True, the system prompt is sent as a
    structured block with cache_control: ephemeral so multiple calls in a
    chain run can share the cached prefix (90% input-token discount on hits,
    5-min default TTL).

    Caller turns this on only when the same system prompt is reused across
    multiple calls in a short window — currently just the per-newsletter
    Haiku summary, which fires once per new newsletter."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None
    if cache_system and system:
        system_payload: Any = [
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
        ]
    else:
        system_payload = system
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": max_tokens,
                "system": system_payload,
                "messages": [{"role": "user", "content": user}],
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()["content"][0]["text"]
    except Exception as exc:
        raise exc


def _extract_json(text: str) -> dict[str, Any] | None:
    """Pull the first balanced {...} JSON object out of a model response.
    Tolerates ```json ... ``` fences and trailing text."""
    if not text:
        return None
    # Strip common markdown code fences
    cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip())
    cleaned = re.sub(r"\s*```\s*$", "", cleaned)
    # Find first '{' then walk to its matching '}' (handles nested braces)
    start = cleaned.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(cleaned)):
        ch = cleaned[i]
        if esc:
            esc = False
            continue
        if ch == "\\" and in_str:
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(cleaned[start:i+1])
                except json.JSONDecodeError:
                    return None
    return None


def maybe_model_story_summary_anthropic(story: dict[str, Any]) -> dict[str, Any] | None:
    model = os.getenv("PULSE_SUMMARY_MODEL", "claude-haiku-4-5-20251001")
    title = story.get("title", "Untitled")
    body = story.get("fullContent") or story.get("description") or story.get("summary") or ""
    category = story.get("nlTier") or story.get("category") or "other"
    source = story.get("source", "Unknown")

    # Give the model full content — newsletters need the depth
    context = relevant_context(body, title, category, max_sentences=20, max_chars=3500)

    from ..vault import inject as vault_inject
    vault_block = vault_inject.for_haiku()
    system = (
        "You are writing analyst-quality summaries for Pulse, a financial news dashboard "
        "for a business student at NYU Stern who day-trades NQ futures and studies IB/consulting.\n\n"
        "Rules:\n"
        "- Extract REAL reported substance, not newsletter wrapper text\n"
        "- Be specific: name names, cite numbers, describe mechanisms\n"
        "- For multi-story digest emails, focus on the dominant story implied by the subject line\n"
        "- Ignore boilerplate: 'good morning', 'view in browser', 'sign up', 'share article', promotional lines\n"
        "- Write like a strong analyst briefing, not a paraphrase of headlines\n"
        "- Explain jargon in plain English\n"
        "- Respond with ONLY valid JSON, no other text"
    )
    if vault_block:
        system = system + "\n\n" + vault_block

    prompt = (
        f"Source: {source}\n"
        f"Category: {category}\n"
        f"Subject: {title}\n\n"
        f"Newsletter content:\n{context}\n\n"
        "Return a JSON object with exactly these fields:\n"
        "{\n"
        '  "summary": "If the newsletter covers ONE connected story, write 2-4 sentences of prose. If it covers 2+ disconnected topics (e.g. a multi-story digest), return markdown bullets — one per topic, each starting with \\"- \\" on its own line. Name key people, numbers, and what happened. No fluff.",\n'
        '  "main_points": ["specific development 1", "specific development 2", "specific development 3 if warranted"],\n'
        '  "why_it_matters": "Why a business student or trader should care. Specific to this story, not generic.",\n'
        '  "plain_english_why_it_matters": "ONE sentence, ≤20 words, plain English. No jargon. Empty string if why_it_matters is already plain.",\n'
        '  "market_impact": "Which assets move, which direction, and what is the transmission mechanism. If minimal, say so concisely.",\n'
        '  "plain_english_market_impact": "ONE sentence, ≤20 words, plain English. No jargon. Empty string if already plain.",\n'
        f'  "framing": "How {source} is angling this — what they emphasize vs. what they downplay or omit.",\n'
        '  "plain_english_framing": "ONE sentence, ≤20 words, plain English. Empty string if already plain.",\n'
        '  "plain_english": "Plain-English version of the main `summary` field — if your summary uses finance jargon (e.g. \\"basis points\\", \\"yield curve\\", \\"transmission mechanism\\"), provide a 1-2 sentence plain-English version. Empty string if summary is already plain.",\n'
        '  "jargon": [{"match":"EXACT substring as it appears in one of your text fields above", "plain":"1-sentence contextual definition a finance beginner would understand"}, ...],  // 3-10 unfamiliar terms OR multi-word phrases a Stern freshman would Google. Pull from summary/why_it_matters/market_impact/framing. Include: (a) acronyms (WTI, NQ, M&A, IPO, ETF, FOMC, CPI, OPEC, KPI, BoJ); (b) single-word jargon (duration, yield, dovish, hawkish, soft landing); (c) MULTI-WORD opaque phrases ("model-sharing agreement", "meet internal targets", "second-order constraint", "transmission mechanism", "deepening the China-Russia axis", "safe-haven bid"). Multi-word phrases ARE MORE VALUABLE than single words because beginners can\'t Google them. Each `match` MUST appear character-for-character in one of those fields. Each `plain` should be tailored to THIS newsletter\'s context. If nothing qualifies, return [].\n'
        '  "tags": ["pick 1-3 from: Geopolitical, Macro, Finance, Markets, AI/Tech, Energy, Politics, Trade, Corporate, Defense"],\n'
        '  "entities": [{"kind":"company|sector|concept", "key":"TICKER_OR_SLUG", "name":"Display Name", "bullets":["2-3 dossier-relevant bullets, each ≤25 words, each citing a specific name/number/date/quote from THIS newsletter about THIS entity. Empty array [] if the entity is only a passing reference."]}, ...]\n'
        "}\n\n"
        "ENTITY-TAGGING RULES — read before populating `entities`:\n"
        "  • Only tag an entity if the newsletter has 2+ specific facts about it — a name, number, deal, quote, or event tied to that entity. A single passing mention of a ticker or buzzword is NOT enough.\n"
        "  • Sectors are NOT topical labels. Do NOT tag `ai_chips` just because the word \"AI\" appears; tag it only when the article discusses chip designers (NVDA / AMD / TSM / AVGO), GPU demand, semicap equipment, or AI hardware supply. A general AI-adoption story does not get this tag.\n"
        "  • Do NOT tag `banks` for a one-line earnings mention; only for stories materially about bank balance sheets, regulation, lending, or specific named banks' results.\n"
        "  • Do NOT tag `energy` / `defense` / `crypto` / `macro` for tangential references; tag only when the article's substance is in that bucket.\n"
        "  • Same rule for concepts: only tag `duration`, `dcf`, `m_and_a`, etc. when the article actually discusses the concept (not just uses the word once).\n"
        "  • If unsure, OMIT the entity. Empty `entities: []` is correct for stories that aren't really about a tagged thing. Over-tagging pollutes the user's dossiers.\n"
    )

    # cache_system=True: this is the highest-volume Haiku call (one per new
    # newsletter). Within a chain run, the system + vault block is identical
    # across all calls, so caching the prefix gives 90% input discount after
    # the first call. 5-min default TTL covers our ~60-90s chain duration.
    text = _call_anthropic(system, prompt, model=model, max_tokens=2200, cache_system=True)
    if not text:
        return None

    parsed = _extract_json(text)
    if not parsed:
        return None

    for field in ("summary", "main_points", "why_it_matters", "market_impact", "framing"):
        if field not in parsed:
            return None

    # Ensure main_points is a list
    if isinstance(parsed["main_points"], str):
        parsed["main_points"] = [parsed["main_points"]]

    # Tags — mirror Groq normalization
    if isinstance(parsed.get("tags"), str):
        parsed["tags"] = [t.strip() for t in parsed["tags"].split(",")]
    if not isinstance(parsed.get("tags"), list):
        parsed["tags"] = []

    # plain_english fields — all optional, default to empty
    for pe_field in ("plain_english", "plain_english_why_it_matters",
                     "plain_english_market_impact", "plain_english_framing"):
        if not isinstance(parsed.get(pe_field), str):
            parsed[pe_field] = ""

    # jargon — list of {match, plain} dicts; optional, default to empty list
    raw_jargon = parsed.get("jargon")
    cleaned_jargon: list[dict[str, str]] = []
    if isinstance(raw_jargon, list):
        for entry in raw_jargon:
            if not isinstance(entry, dict):
                continue
            match = str(entry.get("match", "")).strip()
            plain = str(entry.get("plain", "")).strip()
            if match and plain:
                cleaned_jargon.append({"match": match, "plain": plain})
    parsed["jargon"] = cleaned_jargon[:10]

    # Entities — list of {kind, key, name, bullets} dicts; default to empty.
    # Legacy `quote` field is still accepted from older mocks/responses so the
    # transition is non-breaking, but the prompt above asks for `bullets` now.
    ents = parsed.get("entities")
    if not isinstance(ents, list):
        ents = []
    cleaned_entities: list[dict[str, Any]] = []
    for e in ents:
        if not isinstance(e, dict):
            continue
        kind = str(e.get("kind", "")).strip().lower()
        if kind not in {"company", "sector", "concept"}:
            continue
        key = str(e.get("key", "")).strip()
        name = str(e.get("name", "")).strip() or key
        quote = str(e.get("quote", "")).strip()[:240]
        raw_bullets = e.get("bullets")
        bullets: list[str] | None
        if isinstance(raw_bullets, list):
            bullets = [str(b).strip() for b in raw_bullets if isinstance(b, str) and str(b).strip()][:3]
        else:
            bullets = None
        if not key:
            continue
        cleaned_entities.append({
            "kind": kind, "key": key, "name": name,
            "quote": quote, "bullets": bullets,
        })
    parsed["entities"] = cleaned_entities

    parsed.update(
        {
            "story_id": story.get("emailId") or story.get("link") or title,
            "story_type": "newsletter" if story.get("isNewsletter") else "rss",
            "source": source,
            "title": title,
            "engine": model,
            "engine_label": model,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    return parsed


# ── Groq ──────────────────────────────────────────────────────────────────────

def _call_openai_compat_fast(base_url: str, api_key: str, model: str, system: str, user: str, max_tokens: int = 1200) -> str:
    """Single-attempt caller for story summaries — fails fast on 429 so heuristic fallback kicks in."""
    payload = {
        "model": model,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    resp = requests.post(f"{base_url}/chat/completions", headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _call_openai_compat(base_url: str, api_key: str, model: str, system: str, user: str, max_tokens: int = 1200) -> str:
    """Shared caller for OpenAI-compatible APIs (Groq, OpenAI, etc.)."""
    payload = {
        "model": model,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    resp = None
    for wait in [0, 30, 60, 120]:
        if wait > 0:
            time.sleep(wait)
        resp = requests.post(f"{base_url}/chat/completions", headers=headers, json=payload, timeout=60)
        if resp.status_code != 429:
            break
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def maybe_model_story_summary_groq(story: dict[str, Any]) -> dict[str, Any] | None:
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        return None

    model = os.getenv("PULSE_GROQ_MODEL", "llama-3.1-8b-instant")
    title = story.get("title", "Untitled")
    body = story.get("fullContent") or story.get("description") or story.get("summary") or ""
    category = story.get("nlTier") or story.get("category") or "other"
    source = story.get("source", "Unknown")
    context = relevant_context(body, title, category, max_sentences=20, max_chars=3500)

    system = (
        "You are writing analyst-quality summaries for Pulse, a financial news dashboard "
        "for a business student at NYU Stern who day-trades NQ futures and studies IB/consulting.\n\n"
        "Rules:\n"
        "- Extract REAL reported substance, not newsletter wrapper text\n"
        "- Be specific: name names, cite numbers, describe mechanisms\n"
        "- For multi-story digest emails, focus on the dominant story implied by the subject line\n"
        "- Ignore boilerplate: 'good morning', 'view in browser', 'sign up', promotional lines\n"
        "- Write like a strong analyst briefing, not a paraphrase of headlines\n"
        "- Respond with ONLY valid JSON, no other text"
    )
    prompt = (
        f"Source: {source}\nCategory: {category}\nSubject: {title}\n\n"
        f"Newsletter content:\n{context}\n\n"
        "Return a JSON object with exactly these fields:\n"
        '{"summary": "2-4 sentences. What is this story actually about? Name key people, numbers, what happened. No fluff.", '
        '"main_points": ["specific development 1", "specific development 2", "specific development 3 if warranted"], '
        '"why_it_matters": "Why a business student or trader should care. Specific to this story.", '
        '"market_impact": "Which assets move, which direction, what is the transmission mechanism. If minimal, say so.", '
        f'"framing": "How {source} is angling this — what they emphasize vs. what they downplay.", '
        '"tags": ["pick 1-3 from: Geopolitical, Macro, Finance, Markets, AI/Tech, Energy, Politics, Trade, Corporate, Defense"]}'
    )

    try:
        raw = _call_openai_compat_fast("https://api.groq.com/openai/v1", api_key, model, system, prompt)
    except Exception:
        return None
    parsed = _extract_json(raw)
    if not parsed:
        return None

    for field in ("summary", "main_points", "why_it_matters", "market_impact", "framing"):
        if field not in parsed:
            return None

    if isinstance(parsed["main_points"], str):
        parsed["main_points"] = [parsed["main_points"]]
    if isinstance(parsed.get("tags"), str):
        parsed["tags"] = [t.strip() for t in parsed["tags"].split(",")]
    if not isinstance(parsed.get("tags"), list):
        parsed["tags"] = []

    parsed.update({
        "story_id": story.get("emailId") or story.get("link") or title,
        "story_type": "newsletter" if story.get("isNewsletter") else "rss",
        "source": source,
        "title": title,
        "engine": model,
        "engine_label": f"groq/{model}",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    return parsed


def maybe_model_overarching_analysis_groq(
    newsletter_summaries: list[dict[str, Any]],
    rss: list[dict[str, Any]],
) -> dict[str, Any] | None:
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        return None

    model = os.getenv("PULSE_GROQ_ANALYSIS_MODEL", "llama-3.1-8b-instant")

    nl_blocks: list[str] = []
    source_index: list[str] = []
    for i, item in enumerate(newsletter_summaries[:20]):
        title = item.get("title", "Untitled")
        ref = f'Source {i+1}: "{title}"'
        source_index.append(ref)
        summary = (item.get("summary") or "")[:200]
        points = item.get("main_points", [])[:2]
        block = f"{ref}\n{summary}"
        if points:
            block += "\n" + "\n".join(f"  • {p}" for p in points)
        nl_blocks.append(block)

    source_list = "\n".join(source_index)
    context = f"AVAILABLE SOURCES (copy these titles EXACTLY into *_sources fields):\n{source_list}\n\nNEWSLETTER SUMMARIES:\n" + "\n\n".join(nl_blocks)

    system = (
        "You are writing the Pulse daily analysis for Gavin — a freshman at NYU Stern who "
        "day-trades NQ futures and is building knowledge of markets, IB, and consulting.\n\n"
        "Write like a sharp, opinionated analyst. Be specific: names, numbers, mechanisms. "
        "Explain second-order effects. Tell him what to watch and what the trade is.\n"
        "Respond with ONLY valid JSON, no other text."
    )
    prompt = (
        f"Based on today's coverage, write the Pulse daily analysis.\n\n{context}\n\n"
        "Return a JSON object with exactly these fields. "
        "For each *_sources field, use the EXACT newsletter titles from the AVAILABLE SOURCES list above that most informed that section (1-2 titles max):\n"
        '{"tldr": "2-3 sentence executive summary of what is actually happening and the key market implication", '
        '"what_happened": "3-5 sentences: what happened, who the key players are, specific numbers and names", '
        '"what_happened_sources": ["exact title from source list"], '
        '"why_markets_move": "3-4 sentences. Which specific assets move and in which direction. Explain the transmission mechanism step by step. Name specific instruments (NQ, crude, yields, dollar). Include second-order effects.", '
        '"why_markets_move_sources": ["exact title from source list"], '
        '"watch_today": "3-4 specific things to watch today and why each matters", '
        '"watch_today_sources": ["exact title from source list"], '
        '"bull_case": "What scenario flips things more positive and what that looks like for markets", '
        '"bull_case_sources": ["exact title from source list"], '
        '"bear_case": "What scenario makes things worse and what that looks like for markets", '
        '"bear_case_sources": ["exact title from source list"], '
        '"nq_game_plan": "3-4 sentences: directional bias (bullish/bearish/neutral) with a specific rationale, 2-3 key catalysts to watch today, what data or event would flip the bias, and any specific levels or ranges that matter", '
        '"nq_game_plan_sources": ["exact title from source list"], '
        '"stern_angle": "2-3 sentences of plain text (no nested JSON). How a business student should think about this — finance theory, interview relevance, second-order frameworks.", '
        '"top_themes": ["theme 1", "theme 2", "theme 3"]}'
    )

    try:
        raw = _call_openai_compat("https://api.groq.com/openai/v1", api_key, model, system, prompt, max_tokens=3000)
    except Exception as exc:
        print(f"[groq overarching analysis] failed: {exc}", flush=True)
        return None
    parsed = _extract_json(raw)
    if not parsed:
        return None

    def src_badges(key: str) -> str:
        srcs = parsed.get(key, [])
        if isinstance(srcs, str):
            srcs = [srcs]
        return "".join(f'<span class="src-badge">{s}</span>' for s in srcs if s)

    return {
        "title": "Pulse Daily Analysis",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "tldr": parsed.get("tldr", ""),
        "what_happened": parsed.get("what_happened", ""),
        "what_happened_sources": src_badges("what_happened_sources"),
        "why_markets_move": parsed.get("why_markets_move", ""),
        "why_markets_move_sources": src_badges("why_markets_move_sources"),
        "watch_today": parsed.get("watch_today", ""),
        "watch_today_sources": src_badges("watch_today_sources"),
        "bull_case": parsed.get("bull_case", ""),
        "bull_case_sources": src_badges("bull_case_sources"),
        "bear_case": parsed.get("bear_case", ""),
        "bear_case_sources": src_badges("bear_case_sources"),
        "nq_game_plan": parsed.get("nq_game_plan", ""),
        "nq_game_plan_sources": src_badges("nq_game_plan_sources"),
        "stern_angle": parsed.get("stern_angle", ""),
        "theme_analysis": parsed.get("top_themes", []),
        "source_take": [
            f"{item.get('source', '')}: {item.get('framing', '')}"
            for item in newsletter_summaries[:4] if item.get("framing")
        ],
        "market_implications": [
            item.get("market_impact", "") for item in newsletter_summaries[:4] if item.get("market_impact")
        ],
        "top_story_lines": [
            f"{item.get('source', '')}: {item.get('title', '')}" for item in newsletter_summaries[:6]
        ],
        "coverage_breakdown": dict(Counter(item.get("source", "Unknown") for item in newsletter_summaries)),
        "engine": f"groq/{model}",
    }


# ── OpenAI fallback ────────────────────────────────────────────────────────────

def maybe_model_story_summary_openai(story: dict[str, Any]) -> dict[str, Any] | None:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None

    # Default to gpt-4o-mini if the configured model looks wrong
    configured_model = os.getenv("PULSE_OPENAI_MODEL", "gpt-4o-mini")
    model = configured_model if configured_model else "gpt-4o-mini"

    title = story.get("title", "Untitled")
    body = story.get("fullContent") or story.get("description") or story.get("summary") or ""
    category = story.get("nlTier") or story.get("category") or "other"
    source = story.get("source", "Unknown")
    distilled_body = relevant_context(body, title, category, max_sentences=15, max_chars=3000)

    instructions = (
        "You are writing one high-quality article summary for Pulse, a financial-news dashboard for a business student. "
        "Extract real article substance from messy newsletter text. "
        "Ignore boilerplate, newsletter intros, promotional lines, links, greetings, and wrapper text. "
        "Focus only on actual reported content. Write like a strong analyst briefing. "
        "Be concrete, specific, and plain-English. Respond with ONLY valid JSON."
    )
    prompt = (
        f"Source: {source}\nCategory: {category}\nTitle: {title}\n\n"
        "Return JSON with fields: summary (2-4 sentences), main_points (array of 2-4 bullets), "
        "why_it_matters, market_impact, framing.\n\n"
        f"Newsletter content:\n{distilled_body}"
    )

    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": prompt},
            ],
        },
        timeout=45,
    )
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"]
    parsed = json.loads(raw)

    for field in ("summary", "main_points", "why_it_matters", "market_impact", "framing"):
        if field not in parsed:
            return None

    if isinstance(parsed["main_points"], str):
        parsed["main_points"] = [parsed["main_points"]]

    parsed.update(
        {
            "story_id": story.get("emailId") or story.get("link") or title,
            "story_type": "newsletter" if story.get("isNewsletter") else "rss",
            "source": source,
            "title": title,
            "engine": model,
            "engine_label": model,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    return parsed


def summary_runtime_status() -> dict[str, Any]:
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    model = os.getenv("PULSE_SUMMARY_MODEL", "claude-haiku-4-5-20251001")
    analysis_model = os.getenv("PULSE_ANALYSIS_MODEL", "claude-sonnet-4-6")
    return {
        "env_path": str(ENV_PATH),
        "anthropic_key_present": bool(anthropic_key),
        "anthropic_key_prefix": anthropic_key[:10] if anthropic_key else "",
        "openai_key_present": bool(openai_key),
        "openai_key_prefix": openai_key[:7] if openai_key else "",
        "summary_model": model,
        "analysis_model": analysis_model,
        "model_budget": model_summary_budget(),
        "model_allowlist": model_subject_allowlist(),
        "last_error": LAST_SUMMARY_ERROR,
    }


def extract_jargon_for_summary(summary_payload: dict[str, Any]) -> list[dict[str, str]]:
    """Smaller Haiku call that ONLY extracts the jargon list for an existing
    summary payload — used by the backfill admin route. Doesn't regenerate
    the rest of the summary fields."""
    text_fields = "\n\n".join(
        f"[{k}]\n{summary_payload.get(k, '')}"
        for k in ("summary", "why_it_matters", "market_impact", "framing")
        if summary_payload.get(k)
    )
    if not text_fields.strip():
        return []
    system = (
        "You extract finance-jargon AND opaque multi-word phrases for a Stern "
        "freshman who's still learning. Given text fields from a newsletter "
        "summary, return up to 10 unfamiliar terms or phrases a beginner would "
        "need explained. Each `match` MUST appear character-for-character in the "
        "text. Each `plain` must be a 1-sentence definition tailored to THIS "
        "newsletter's context (not a generic dictionary entry). Respond with "
        "ONLY raw JSON: no fences, no prose."
    )
    user = (
        text_fields
        + '\n\nReturn JSON: {"jargon": [{"match":"exact substring", "plain":"contextual 1-sentence definition"}, ...]}'
        + "\n\nWhat to extract — be GENEROUS, this is a learning aid:"
        + "\n  • Acronyms: WTI, NQ, M&A, IPO, ETF, FOMC, CPI, PCE, OPEC, EBITDA, KPI, GDP, FOMC, BoJ, ECB."
        + "\n  • Single-word finance jargon: duration, yield, basis points, dovish, hawkish, soft landing, fiscal dominance."
        + "\n  • **Multi-word phrases that need context**: \"model-sharing agreement\", \"meet internal targets\", "
          "\"second-order constraint\", \"transmission mechanism\", \"backdoor through\", \"rip in yields\", "
          "\"safe-haven bid\", \"deepening the China-Russia axis\", \"sticky inflation\". If the phrase has "
          "specialized meaning beyond its literal words, EXTRACT IT as one match."
        + "\n  • Numbers + their referent ('30Y at 5.127%', '+18bp', '$112/bbl Brent') when the unit/context is "
          "non-obvious — `plain` should say what the number means in plain English."
        + "\n\nIMPORTANT: longer phrases ARE more valuable than single words because they're the things you "
          "can't Google easily. Prefer extracting 'meet internal targets' over just 'targets'."
        + '\n\nIf nothing qualifies, return {"jargon": []}.'
    )
    model = os.getenv("PULSE_SUMMARY_MODEL", "claude-haiku-4-5-20251001")
    try:
        text = _call_anthropic(system, user, model=model, max_tokens=900)
    except Exception as exc:
        print(f"[jargon] LLM failed: {exc}", flush=True)
        return []
    parsed = _extract_json(text) if text else None
    if not isinstance(parsed, dict):
        return []
    raw = parsed.get("jargon")
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        m = str(entry.get("match", "")).strip()
        p = str(entry.get("plain", "")).strip()
        if m and p:
            out.append({"match": m, "plain": p})
    return out[:10]


def backfill_jargon_for_existing_summaries(limit: int | None = None, force: bool = False) -> dict[str, int]:
    """Pass over story_summaries to populate the jargon field. Idempotent —
    skips rows that already have jargon. Pass `force=True` to re-extract
    everything (e.g. after a prompt change that should produce better
    multi-word phrase extraction)."""
    import time as _time
    sql = "SELECT story_id, payload_json FROM story_summaries WHERE engine NOT IN ('heuristic', '')"
    with get_connection() as conn:
        rows = conn.execute(sql).fetchall()
    targets: list[tuple[str, dict]] = []
    for r in rows:
        try:
            payload = json.loads(r["payload_json"])
        except Exception:
            continue
        if not force:
            if "jargon" in payload and isinstance(payload["jargon"], list) and payload["jargon"]:
                continue  # already has jargon
        targets.append((r["story_id"], payload))
    if limit is not None:
        targets = targets[:int(limit)]
    total = len(targets)
    print(f"[jargon] backfill: {total} summaries missing jargon", flush=True)
    filled = 0
    empty = 0
    skipped = 0
    for i, (sid, payload) in enumerate(targets, start=1):
        jargon = extract_jargon_for_summary(payload)
        if jargon is None:
            skipped += 1
            continue
        payload["jargon"] = jargon
        if jargon:
            filled += 1
        else:
            empty += 1
        with get_connection() as conn:
            conn.execute(
                "UPDATE story_summaries SET payload_json = ? WHERE story_id = ?",
                (json.dumps(payload), sid),
            )
        if i % 10 == 0:
            print(f"[jargon] backfill progress {i}/{total}", flush=True)
        _time.sleep(0.5)
    print(f"[jargon] backfill done: considered={total} filled={filled} empty={empty}", flush=True)
    return {"considered": total, "filled": filled, "empty": empty, "skipped": skipped}


def load_cached_story_summary(story_id: str, current_hash: str) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT payload_json
            FROM story_summaries
            WHERE story_id = ? AND content_hash = ?
            """,
            (story_id, current_hash),
        ).fetchone()
    if not row:
        return None
    return json.loads(row["payload_json"])


def story_cache_key(story: dict[str, Any]) -> tuple[str, str]:
    story_id = story.get("emailId") or story.get("link") or story.get("title", "untitled")
    body = story.get("fullContent") or story.get("description") or story.get("summary") or ""
    # Prompt-schema version. Bump this string when changing the Haiku prompt's
    # output shape so existing cached summaries get invalidated and regenerated.
    # Current bump (2026-05-20): added plain_english_why_it_matters / _market_impact / _framing.
    PROMPT_VERSION = "v3-vault-aware"
    current_hash = content_hash(story.get("title", ""), body, story.get("source", ""), PROMPT_VERSION)
    return story_id, current_hash


def story_priority(story: dict[str, Any]) -> tuple[int, int, str]:
    signal = str(story.get("signal", ""))
    title = (story.get("title") or "").lower()
    tier = story.get("nlTier") or story.get("category") or "other"
    received = story.get("received_at") or story.get("publishedAt") or ""
    signal_score = 3 if "signal:high" in signal else 2 if "signal:medium" in signal else 1
    tier_score = {
        "geopolitical": 5,
        "finance": 4,
        "macro": 4,
        "markets": 3,
        "earnings": 3,
        "tech": 2,
        "mixed": 2,
        "other": 1,
        "lifestyle": 0,
    }.get(tier, 1)
    # Boost stories with financial/geopolitical keywords even if tier not set
    keyword_boost = 0
    for kw in ["iran", "war", "fed", "oil", "trump", "tariff", "inflation", "rate", "market", "nasdaq", "recession"]:
        if kw in title:
            keyword_boost = 2
            break
    return (signal_score * 10 + tier_score + keyword_boost, received, story.get("title", ""))


def story_allowed_for_model(story: dict[str, Any]) -> bool:
    return True


def save_story_summary(summary_payload: dict[str, Any], current_hash: str) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO story_summaries (story_id, story_type, content_hash, payload_json, engine, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(story_id) DO UPDATE SET
                story_type=excluded.story_type,
                content_hash=excluded.content_hash,
                payload_json=excluded.payload_json,
                engine=excluded.engine,
                updated_at=excluded.updated_at
            """,
            (
                summary_payload["story_id"],
                summary_payload["story_type"],
                current_hash,
                json.dumps(summary_payload),
                summary_payload["engine"],
                summary_payload["updated_at"],
            ),
        )


def summarize_story(story: dict[str, Any], refresh: bool = False, allow_model: bool = True) -> dict[str, Any]:
    global LAST_SUMMARY_ERROR
    story_id, current_hash = story_cache_key(story)
    cached = load_cached_story_summary(story_id, current_hash)
    if cached and cached.get("engine") not in ("heuristic", None, ""):
        if not refresh:
            return cached
        if cached.get("tags"):
            # Already has a real model summary with tags — keep it
            return cached

    if not allow_model:
        generated = heuristic_story_summary(story)
        save_story_summary(generated, current_hash)
        return generated

    generated = heuristic_story_summary(story)
    try:
        # Priority: Anthropic → Groq → OpenAI → heuristic
        model_summary = (
            maybe_model_story_summary_anthropic(story)
            or maybe_model_story_summary_groq(story)
            or maybe_model_story_summary_openai(story)
        )
        if model_summary:
            LAST_SUMMARY_ERROR = None
            generated = model_summary
    except Exception as exc:
        LAST_SUMMARY_ERROR = {
            "story_id": story_id,
            "title": story.get("title", "Untitled"),
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        }
        generated = heuristic_story_summary(story)

    # Push extracted entities to the dossier system (best-effort, never blocks summary save)
    try:
        entities = generated.get("entities") if isinstance(generated, dict) else None
        if entities:
            from .dossiers import process_entities
            nl_id = story.get("emailId") or story.get("link") or story.get("title", "untitled")
            process_entities(nl_id, entities)
    except Exception as exc:
        print(f"[dossiers] process_entities failed: {exc}", flush=True)

    save_story_summary(generated, current_hash)
    return generated


def summarize_stories(stories: list[dict[str, Any]], refresh: bool = False) -> list[dict[str, Any]]:
    budget = model_summary_budget()
    ranked_ids = {
        (story.get("emailId") or story.get("link") or story.get("title", "untitled"))
        for story in sorted(
            [story for story in stories if story_allowed_for_model(story)],
            key=story_priority,
            reverse=True,
        )[:budget]
    }

    summaries: list[dict[str, Any]] = []
    for story in stories:
        story_id, current_hash = story_cache_key(story)
        cached = load_cached_story_summary(story_id, current_hash)
        if cached and cached.get("engine") not in ("heuristic", None, ""):
            summaries.append(cached)
            continue
        result = summarize_story(story, refresh=refresh, allow_model=story_id in ranked_ids)
        summaries.append(result)
        if result.get("engine") not in ("heuristic", None, ""):
            time.sleep(1)
    return summaries


# ── Overarching analysis ───────────────────────────────────────────────────────

def _overarching_analysis_content_hash(newsletter_summaries: list[dict[str, Any]], rss: list[dict[str, Any]]) -> str:
    # Hash only what actually goes into the Sonnet prompt. The prompt uses
    # newsletter_summaries[:15] and rss[:12], so changes outside those slices
    # don't change the input — and don't justify regenerating the briefing.
    # Was [:30]/[:10] before, which made newsletters at positions 16-30
    # silently invalidate the cache for nothing.
    nl_ids = "|".join(item.get("story_id", item.get("title", "")) for item in newsletter_summaries[:15])
    rss_titles = "|".join(item.get("title", "") for item in rss[:12])
    return content_hash(nl_ids, rss_titles)


def maybe_model_overarching_analysis(
    newsletter_summaries: list[dict[str, Any]],
    rss: list[dict[str, Any]],
) -> dict[str, Any] | None:
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not anthropic_key:
        return None

    model = os.getenv("PULSE_ANALYSIS_MODEL", "claude-sonnet-4-6")

    # Build rich context from newsletter summaries
    nl_blocks: list[str] = []
    source_index: list[str] = []
    for i, item in enumerate(newsletter_summaries[:15]):
        source = item.get("source", "Unknown")
        title = item.get("title", "Untitled")
        ref = f'Source {i+1}: "{title}"'
        source_index.append(ref)
        summary = item.get("summary", "")
        points = item.get("main_points", [])
        market = item.get("market_impact", "")
        block = f"{ref} [{source}]\n{summary}"
        if points:
            block += "\n" + "\n".join(f"  • {p}" for p in points)
        if market:
            block += f"\n  Market: {market}"
        nl_blocks.append(block)

    rss_lines = [
        f"[{item.get('source', 'RSS')}] {item.get('title', '')}"
        for item in rss[:12]
    ]

    context = (
        "AVAILABLE SOURCES (copy these titles EXACTLY into *_sources fields):\n"
        + "\n".join(source_index)
        + "\n\nNEWSLETTER SUMMARIES:\n"
        + "\n\n".join(nl_blocks)
    )
    if rss_lines:
        context += "\n\nRSS HEADLINES:\n" + "\n".join(rss_lines)

    from ..vault import inject as vault_inject
    # Filter the vault block to concepts touched by today's news instead of
    # dumping ALL 42 atomic notes. Cuts input tokens substantially AND helps
    # Sonnet focus on relevant concepts rather than scanning the full library.
    # Falls back to the unfiltered block if no entity matches today's news.
    _relevant_entities: set[str] = set()
    for _nl in newsletter_summaries[:15]:
        for _ent in (_nl.get("entities") or []):
            for _v in (_ent.get("name"), _ent.get("key")):
                if _v:
                    _relevant_entities.add(str(_v))
    if _relevant_entities:
        vault_block = vault_inject.for_sonnet(filter_entities=list(_relevant_entities))
    else:
        vault_block = vault_inject.for_sonnet()
    system = (
        "You are writing the Pulse daily analysis for Gavin — a freshman at NYU Stern who "
        "day-trades NQ futures and is building knowledge of markets, IB, and consulting.\n\n"
        "Write like a sharp, opinionated analyst — not a journalist. Be specific: name names, "
        "cite numbers, describe mechanisms. Explain how things connect. Identify second-order "
        "effects. Tell him what to watch, why, and what the trade is.\n\n"
        "When today's news touches a concept in the user's vault below, weave the concept "
        "into your prose naturally — name the concept inline (e.g. \"this is a duration "
        "play\") and lean on the user's own phrasing where it clarifies the news. Do NOT "
        "use [[bracketed wiki syntax]] in the output — write smooth editorial prose. When "
        "writing the stern_angle field, ground it in the user's vault concepts and active "
        "learning where possible.\n"
        "Respond with ONLY valid JSON, no other text."
    )
    if vault_block:
        system = system + "\n\n" + vault_block

    prompt = (
        "Based on today's coverage, write the Pulse daily analysis.\n\n"
        f"{context}\n\n"
        "For every *_sources field below, list 1-3 source titles copied EXACTLY from the AVAILABLE SOURCES list above (no paraphrasing).\n\n"
        "FORMAT RULES for the main text fields (what_happened, why_markets_move, watch_today, bull_case, bear_case, nq_game_plan, stern_angle):\n"
        "  - If you would write a single coherent narrative, use prose.\n"
        "  - If you would naturally make 2+ distinct points, use markdown bullets — one per line, each starting with \"- \".\n"
        "  - Do NOT use **bold** markdown anywhere. Plain text only.\n\n"
        "PLAIN ENGLISH RULES (applies to every `plain_english_*` field below):\n"
        "  - The user is an NYU Stern freshman who's still learning the jargon. Translate the analyst version into how you'd explain it to a smart non-finance friend over coffee.\n"
        "  - No jargon without inline explanation. Avoid \"basis points\", \"yield curve\", \"duration\", \"transmission mechanism\", \"capex\", \"PE multiple\", \"hawkish/dovish\", etc. without saying what they mean in plain words.\n"
        "  - 1-2 sentences max per plain-English field. Concise.\n"
        "  - If the original section is already plain, return an empty string.\n\n"
        "Return a JSON object with exactly these fields:\n"
        "{\n"
        '  "tldr": "2-3 sentence executive summary: what is actually happening today and the single most important market implication",\n'
        '  "plain_english_tldr": "1-2 sentences explaining the day in everyday language — see PLAIN ENGLISH RULES",\n'
        '  "what_happened": "3-5 sentences OR bullets on the main story — what happened, who the key players are, specific numbers and names",\n'
        '  "what_happened_sources": ["exact title from AVAILABLE SOURCES"],\n'
        '  "plain_english_what_happened": "Plain-English version of what_happened (or empty if already plain)",\n'
        '  "why_markets_move": "Specific assets moving and the transmission mechanism. Prose or bullets per format rules.",\n'
        '  "why_markets_move_sources": ["exact title from AVAILABLE SOURCES"],\n'
        '  "plain_english_why_markets_move": "Plain-English version (or empty if already plain)",\n'
        '  "watch_today": "3-4 specific things to watch today, with why each matters — best as bullets",\n'
        '  "watch_today_sources": ["exact title from AVAILABLE SOURCES"],\n'
        '  "plain_english_watch_today": "Plain-English version (or empty if already plain)",\n'
        '  "bull_case": "Scenario that flips things positive — prose or bullets per format rules",\n'
        '  "bull_case_sources": ["exact title from AVAILABLE SOURCES"],\n'
        '  "plain_english_bull_case": "Plain-English version (or empty if already plain)",\n'
        '  "bear_case": "Scenario that makes things worse — prose or bullets per format rules",\n'
        '  "bear_case_sources": ["exact title from AVAILABLE SOURCES"],\n'
        '  "plain_english_bear_case": "Plain-English version (or empty if already plain)",\n'
        '  "nq_game_plan": "Directional bias, key catalysts, what would flip the bias. Bullets work well here.",\n'
        '  "nq_game_plan_sources": ["exact title from AVAILABLE SOURCES"],\n'
        '  "plain_english_nq_game_plan": "Plain-English version (or empty if already plain)",\n'
        '  "stern_angle": "Finance theory links / interview-prep relevance — prose or bullets per format rules",\n'
        '  "plain_english_stern_angle": "Plain-English version (or empty if already plain)",\n'
        '  "top_themes": ["theme 1", "theme 2", "theme 3"],\n'
        '  "vocab": [{"term": "Term Name", "definition": "1 sentence analyst-style definition", "plain_english": "1 sentence non-jargon explanation a non-finance friend would understand — use an analogy if helpful"}, ... up to 8 entries — scan BOTH the AVAILABLE SOURCES titles, the NEWSLETTER SUMMARIES context, AND your own analysis sections for finance/markets jargon and acronyms a Stern freshman would Google. Strongly prioritize: (a) ACRONYMS that appear unexplained (e.g. WTI, NQ, SPX, M&A, IPO, KPI, ETF, FOMC, CPI, PCE, OPEC, GDP), (b) terms that appear in MULTIPLE summaries — weight by frequency in NEWSLETTER SUMMARIES. Capitalize the first letter of each term. Skip plain-English words. If nothing qualifies, return [].]\n'
        "}"
    )

    try:
        text = _call_anthropic(system, prompt, model=model, max_tokens=8000)
    except Exception as exc:
        print(f"[anthropic overarching analysis] failed: {exc}", flush=True)
        return None
    if not text:
        print("[anthropic overarching analysis] empty response", flush=True)
        return None

    parsed = _extract_json(text)
    if not parsed:
        print(f"[anthropic overarching analysis] JSON parse failed; text len={len(text)}; head={text[:200]!r}; tail={text[-200:]!r}", flush=True)
        return None

    def src_badges(key: str) -> str:
        srcs = parsed.get(key, [])
        if isinstance(srcs, str):
            srcs = [srcs]
        return "".join(f'<span class="src-badge">{s}</span>' for s in srcs if s)

    vocab_raw = parsed.get("vocab", [])
    vocab = []
    if isinstance(vocab_raw, list):
        for entry in vocab_raw:
            if isinstance(entry, dict) and entry.get("term") and entry.get("definition"):
                vocab.append({
                    "term": str(entry["term"]),
                    "definition": str(entry["definition"]),
                    "plain_english": str(entry.get("plain_english", "")) if entry.get("plain_english") else "",
                })

    def pe(field: str) -> str:
        v = parsed.get(field, "")
        return v if isinstance(v, str) else ""

    return {
        "title": "Pulse Daily Analysis",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "tldr": parsed.get("tldr", ""),
        "plain_english_tldr": pe("plain_english_tldr"),
        "vocab": vocab,
        "what_happened": parsed.get("what_happened", ""),
        "what_happened_sources": src_badges("what_happened_sources"),
        "plain_english_what_happened": pe("plain_english_what_happened"),
        "why_markets_move": parsed.get("why_markets_move", ""),
        "why_markets_move_sources": src_badges("why_markets_move_sources"),
        "plain_english_why_markets_move": pe("plain_english_why_markets_move"),
        "watch_today": parsed.get("watch_today", ""),
        "watch_today_sources": src_badges("watch_today_sources"),
        "plain_english_watch_today": pe("plain_english_watch_today"),
        "bull_case": parsed.get("bull_case", ""),
        "bull_case_sources": src_badges("bull_case_sources"),
        "plain_english_bull_case": pe("plain_english_bull_case"),
        "bear_case": parsed.get("bear_case", ""),
        "bear_case_sources": src_badges("bear_case_sources"),
        "plain_english_bear_case": pe("plain_english_bear_case"),
        "nq_game_plan": parsed.get("nq_game_plan", ""),
        "nq_game_plan_sources": src_badges("nq_game_plan_sources"),
        "plain_english_nq_game_plan": pe("plain_english_nq_game_plan"),
        "stern_angle": parsed.get("stern_angle", ""),
        "plain_english_stern_angle": pe("plain_english_stern_angle"),
        "theme_analysis": parsed.get("top_themes", []),
        "source_take": [
            f"{item.get('source', '')}: {item.get('framing', '')}"
            for item in newsletter_summaries[:4]
            if item.get("framing")
        ],
        "market_implications": [
            item.get("market_impact", "")
            for item in newsletter_summaries[:4]
            if item.get("market_impact")
        ],
        "top_story_lines": [
            f"{item.get('source', '')}: {item.get('title', '')}"
            for item in newsletter_summaries[:6]
        ],
        "coverage_breakdown": dict(Counter(item.get("source", "Unknown") for item in newsletter_summaries)),
        "engine": model,
    }


def build_overarching_analysis(newsletter_summaries: list[dict[str, Any]], rss: list[dict[str, Any]]) -> dict[str, Any]:
    """Heuristic fallback — used only when no model API key is configured."""
    top_newsletters = newsletter_summaries[:5]
    top_rss = rss[:5]
    all_categories = Counter()
    for item in newsletter_summaries:
        all_categories[item.get("source", "Unknown")] += 1

    top_points: list[str] = []
    for item in top_newsletters[:3]:
        top_points.append(f'{item["source"]}: {item["summary"]}')
    for item in top_rss[:2]:
        line = item.get("summary") or item.get("description") or item.get("title", "Untitled")
        top_points.append(f'{item.get("source", "Unknown")}: {trim_text(line, 160)}')

    themes = []
    joined = " ".join(item.get("summary", "") for item in top_newsletters).lower()
    if any(token in joined for token in ["iran", "war", "oil", "sanction", "shipping"]):
        themes.append("Geopolitics and energy remain the dominant market transmission channel.")
    if any(token in joined for token in ["fed", "inflation", "rates", "yield", "economy"]):
        themes.append("Macro framing is centered on rates, inflation, and how growth expectations are changing.")
    if any(token in joined for token in ["stocks", "nasdaq", "earnings", "markets"]):
        themes.append("Market coverage is focused on whether positioning and leadership are shifting.")
    if not themes:
        themes.append("The current flow is mixed — the best signal comes from which themes repeat across newsletters.")

    tldr = (
        "Pulse is summarizing each high-signal newsletter individually and using RSS as supporting breadth. "
        "Set ANTHROPIC_API_KEY in your .env to enable model-powered analysis."
    )

    return {
        "title": "Pulse Daily Analysis",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "tldr": tldr,
        "what_happened": "",
        "why_markets_move": "",
        "watch_today": "",
        "bull_case": "",
        "bear_case": "",
        "nq_game_plan": "",
        "stern_angle": "",
        "theme_analysis": themes[:3],
        "source_take": [
            f"{item['source']}: {item['framing']}" for item in top_newsletters[:3] if item.get("framing")
        ],
        "market_implications": [item.get("market_impact", "") for item in top_newsletters[:3]],
        "top_story_lines": top_points,
        "coverage_breakdown": dict(all_categories),
        "engine": "heuristic",
    }


def get_latest_cached_analysis() -> dict[str, Any] | None:
    """Return the most recently saved overarching analysis from cache, regardless of key."""
    with get_connection() as connection:
        row = connection.execute(
            "SELECT payload_json FROM briefings WHERE briefing_key LIKE 'overarching_analysis_v7_%' ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
    if row:
        return json.loads(row["payload_json"])
    return None


def get_or_build_overarching_analysis(
    newsletter_summaries: list[dict[str, Any]],
    rss: list[dict[str, Any]],
    refresh: bool = False,
) -> dict[str, Any]:
    # Use a content-based cache key so the analysis refreshes when newsletters change
    content_key = _overarching_analysis_content_hash(newsletter_summaries, rss)
    briefing_key = f"overarching_analysis_v7_{content_key[:16]}"

    if not refresh:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM briefings WHERE briefing_key = ?",
                (briefing_key,),
            ).fetchone()
        if row:
            return json.loads(row["payload_json"])

    # Priority: Anthropic → Groq → heuristic
    payload = (
        maybe_model_overarching_analysis(newsletter_summaries, rss)
        or maybe_model_overarching_analysis_groq(newsletter_summaries, rss)
        or build_overarching_analysis(newsletter_summaries, rss)
    )

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
            (briefing_key, payload["title"], json.dumps(payload), payload["updated_at"]),
        )
    return payload

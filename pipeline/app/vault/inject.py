"""Build the prompt fragments injected into LLM calls.

Two tiers:
    for_haiku()   — ~2.5KB compact list, suitable for per-newsletter Haiku calls
    for_sonnet()  — ~15KB full bodies, suitable for daily Sonnet calls

Both return "" when the vault is empty or unavailable, so callers can
unconditionally inject without worrying about the no-vault case."""

from __future__ import annotations

import hashlib
import re

from . import index


_WIKILINK_RE = re.compile(r"\[\[([^\]\|]+)(?:\|[^\]]+)?\]\]")


def for_haiku() -> str:
    """Compact list of vault concepts for Haiku tier. ~2.5KB.

    Format:
        === USER'S WATCHLIST CONCEPTS ===
        ...framing...
        - Title: one-liner
        - ...
        === END WATCHLIST ===
    """
    titles = index.titles()
    if not titles:
        return ""
    lines = [
        "=== USER'S WATCHLIST CONCEPTS ===",
        "The user has personal notes on these concepts. If a story touches one,",
        "prefer extracting it as an entity and reference it by name so summaries",
        "link back to their notes.",
        "",
    ]
    for t in titles:
        # Truncate one-liners to keep the block bounded
        one_liner = t.one_liner[:160].rstrip()
        lines.append(f"- {t.title}: {one_liner}")
    lines.append("=== END WATCHLIST ===")
    return "\n".join(lines)


def for_sonnet(filter_entities: list[str] | None = None) -> str:
    """Full atomic note bodies for Sonnet tier. ~15KB unfiltered.

    When filter_entities is provided, returns only notes whose title matches
    one of the entities OR is wikilinked from a matching note. Falls back to
    the most recently parsed 5 notes if no matches.
    """
    notes = index.full()
    if not notes:
        return ""

    if filter_entities:
        wanted = {e.lower() for e in filter_entities}
        # Pass 1: direct title match
        direct: list[index.VaultNote] = [n for n in notes if n.title.lower() in wanted]
        # Pass 2: wikilink expansion — pull in notes referenced from direct hits
        linked_titles: set[str] = set()
        for n in direct:
            for m in _WIKILINK_RE.finditer(n.body):
                linked_titles.add(m.group(1).lower())
        linked = [n for n in notes if n.title.lower() in linked_titles and n not in direct]
        selected = direct + linked
        if not selected:
            # Fallback: top-5 by index order (which mirrors filesystem sort)
            selected = notes[:5]
    else:
        selected = list(notes)

    lines = [
        "=== USER'S VAULT CONCEPTS ===",
        "The following are atomic notes the user has written in their personal",
        "vault. When today's news touches one of these, explicitly cite the",
        "concept using its wikilink (e.g. [[Duration]]) and weave in the user's",
        "own phrasing where it clarifies the story.",
        "",
    ]
    for n in selected:
        lines.append(f"[[{n.title}]]")
        lines.append(n.body)
        lines.append("")
    lines.append("=== END VAULT CONCEPTS ===")
    return "\n".join(lines)


def haiku_block_hash() -> str:
    """Short hash of the Haiku-tier block. Returns 'none' when empty."""
    block = for_haiku()
    if not block:
        return "none"
    return hashlib.sha256(block.encode("utf-8")).hexdigest()[:12]

"""Integration test: vault block actually appears in each LLM prompt.

Patches _call_anthropic to capture the prompt arguments, runs each of the
three injection sites, and asserts the vault block was injected when the
index has content."""

from __future__ import annotations

import json

import pytest

from app.vault import index as vault_index


@pytest.fixture
def captured_prompts(monkeypatch, fixture_vault):
    """Patches _call_anthropic in both summaries and dossiers modules.
    Returns a list that gets appended to on every LLM call: (system, user, model)."""
    captured: list[tuple[str, str, str]] = []

    def fake_call(system, user, model, max_tokens=0, timeout=0, **kwargs):
        captured.append((system, user, model))
        # Return a JSON shape that all three callers can parse without crashing
        return json.dumps({
            "summary": "x", "main_points": ["a"], "why_it_matters": "x",
            "market_impact": "x", "framing": "x", "tldr": "x", "what_happened": "x",
            "why_markets_move": "x", "watch_today": "x", "bull_case": "x",
            "bear_case": "x", "nq_game_plan": "x", "stern_angle": "x",
            "top_themes": [], "overview": "x", "plain_english": "x",
            "bull_thesis": "x", "bear_thesis": "x", "topic_tags": [],
            "key_themes": [], "notable_quotes": [],
        })

    monkeypatch.setattr("app.services.summaries._call_anthropic", fake_call)
    import app.services.dossiers as _dossiers_mod
    monkeypatch.setattr(_dossiers_mod, "_call_anthropic", fake_call)

    vault_index.rebuild()  # populate from fixture vault
    return captured


def test_story_summary_prompt_contains_haiku_vault_block(captured_prompts):
    from app.services.summaries import maybe_model_story_summary_anthropic
    story = {
        "title": "Fed signals on rates",
        "fullContent": "The Fed today indicated...",
        "source": "WSJ",
        "category": "macro",
        "isNewsletter": True,
        "emailId": "test1",
    }
    maybe_model_story_summary_anthropic(story)
    assert captured_prompts, "no LLM call captured"
    system, _user, _model = captured_prompts[-1]
    assert "USER'S WATCHLIST CONCEPTS" in system
    assert "Duration" in system
    assert "Convexity" in system


def test_briefing_prompt_contains_sonnet_vault_block(captured_prompts):
    from app.services.summaries import maybe_model_overarching_analysis
    nl = [{
        "title": "Markets recap", "source": "WSJ", "summary": "stocks moved",
        "main_points": ["a"], "market_impact": "yields rose",
    }]
    maybe_model_overarching_analysis(nl, [])
    assert captured_prompts, "no LLM call captured"
    system, _user, _model = captured_prompts[-1]
    assert "USER'S VAULT CONCEPTS" in system
    # Full bodies are injected — must contain phrasing from Duration.md
    assert "When the Fed raises rates" in system


def test_dossier_prompt_contains_filtered_vault_block(captured_prompts, db_initialized):
    from app.services.dossiers import upsert_entity, regenerate_snapshot
    eid = upsert_entity("concept", "duration", "Duration")
    regenerate_snapshot(eid)
    assert captured_prompts, "no LLM call captured"
    _system, user, _model = captured_prompts[-1]
    # Dossier injects into the user prompt (prefix to mention block)
    assert "[[Duration]]" in user
    # Duration's note links to Convexity, so Convexity should be pulled in
    assert "Convexity is the second-order" in user


def test_no_vault_block_when_index_empty(monkeypatch):
    """If the index has no notes, no vault block appears in the system prompt."""
    captured: list[tuple[str, str, str]] = []
    def fake_call(system, user, model, max_tokens=0, timeout=0, **kwargs):
        captured.append((system, user, model))
        return json.dumps({
            "summary": "x", "main_points": ["a"], "why_it_matters": "x",
            "market_impact": "x", "framing": "x",
        })
    monkeypatch.setattr("app.services.summaries._call_anthropic", fake_call)
    vault_index._titles.clear()
    vault_index._full.clear()
    vault_index._built = True

    from app.services.summaries import maybe_model_story_summary_anthropic
    maybe_model_story_summary_anthropic({
        "title": "x", "fullContent": "x", "source": "x", "category": "x",
        "emailId": "x", "isNewsletter": True,
    })
    system, _user, _model = captured[-1]
    assert "USER'S WATCHLIST CONCEPTS" not in system

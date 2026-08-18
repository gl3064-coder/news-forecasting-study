"""Tests for app.vault.inject — builds prompt fragments for Haiku/Sonnet calls."""

from __future__ import annotations

import pytest

from app.vault import index as vault_index, inject as vault_inject


def test_for_haiku_returns_empty_when_index_empty(monkeypatch):
    vault_index._titles.clear()
    vault_index._full.clear()
    vault_index._built = True
    assert vault_inject.for_haiku() == ""


def test_for_sonnet_returns_empty_when_index_empty(monkeypatch):
    vault_index._titles.clear()
    vault_index._full.clear()
    vault_index._built = True
    assert vault_inject.for_sonnet() == ""


def test_for_haiku_lists_titles_with_one_liners(fixture_vault):
    vault_index.rebuild()
    block = vault_inject.for_haiku()
    assert "USER'S WATCHLIST CONCEPTS" in block
    assert "- Duration:" in block
    assert "- Convexity:" in block
    # One-liner accompanies each title
    assert "bond price sensitivity" in block.lower()


def test_for_haiku_under_5kb_for_typical_vault(fixture_vault):
    vault_index.rebuild()
    block = vault_inject.for_haiku()
    assert 0 < len(block.encode("utf-8")) < 5000


def test_for_sonnet_includes_full_bodies(fixture_vault):
    vault_index.rebuild()
    block = vault_inject.for_sonnet()
    assert "USER'S VAULT CONCEPTS" in block
    assert "[[Duration]]" in block
    assert "[[Convexity]]" in block
    assert "When the Fed raises rates" in block  # body content from Duration


def test_for_sonnet_filters_by_entity_substring(fixture_vault):
    vault_index.rebuild()
    block = vault_inject.for_sonnet(filter_entities=["Duration"])
    # Duration's own note is included
    assert "When the Fed raises rates" in block
    # Convexity is included because Duration's note links to [[Convexity]]
    # via wikilink (per spec rule: title matches OR linked from another included note)
    assert "Convexity is the second-order" in block


def test_for_sonnet_filter_with_no_matches_returns_top_5_by_recency(fixture_vault):
    vault_index.rebuild()
    block = vault_inject.for_sonnet(filter_entities=["NonexistentEntity"])
    # Fallback: top-5 most-recent notes (just the 3 well-formed in fixtures)
    assert "[[Duration]]" in block or "[[Convexity]]" in block or "[[NoPreamble]]" in block


def test_haiku_block_hash_changes_with_content(fixture_vault):
    vault_index.rebuild()
    h1 = vault_inject.haiku_block_hash()
    # Mutate the cache so the hash changes
    vault_index._titles.clear()
    vault_index._titles.append(vault_index.VaultTitle("Other", "one line"))
    h2 = vault_inject.haiku_block_hash()
    assert h1 != h2
    assert h1 != "none"


def test_haiku_block_hash_is_none_when_empty(monkeypatch):
    vault_index._titles.clear()
    vault_index._full.clear()
    vault_index._built = True
    assert vault_inject.haiku_block_hash() == "none"

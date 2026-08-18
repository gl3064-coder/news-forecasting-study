"""Tests for app.vault.index — parses 02 Notes/ atomic notes into caches."""

from __future__ import annotations

import pytest

from app.vault import index as vault_index


def test_rebuild_parses_well_formed_notes(fixture_vault):
    vault_index.rebuild()
    titles = {t.title for t in vault_index.titles()}
    assert "Duration" in titles
    assert "Convexity" in titles


def test_rebuild_skips_empty_notes(fixture_vault):
    vault_index.rebuild()
    titles = {t.title for t in vault_index.titles()}
    assert "Empty" not in titles


def test_rebuild_skips_bad_yaml(fixture_vault):
    vault_index.rebuild()
    titles = {t.title for t in vault_index.titles()}
    assert "BadYaml" not in titles


def test_rebuild_uses_for_future_claude_preamble_when_present(fixture_vault):
    vault_index.rebuild()
    by_title = {t.title: t.one_liner for t in vault_index.titles()}
    assert by_title["Duration"].startswith("Duration measures bond price sensitivity")


def test_rebuild_uses_first_body_line_when_no_preamble(fixture_vault):
    vault_index.rebuild()
    by_title = {t.title: t.one_liner for t in vault_index.titles()}
    assert "NoPreamble" in by_title
    assert by_title["NoPreamble"].startswith("This note has no")


def test_full_bodies_include_complete_markdown(fixture_vault):
    vault_index.rebuild()
    by_title = {n.title: n.body for n in vault_index.full()}
    assert "Convexity is the second-order rate sensitivity" in by_title["Convexity"]
    assert "[[Duration]]" in by_title["Convexity"]


def test_is_built_flips_after_rebuild(fixture_vault):
    assert vault_index.is_built() is False
    vault_index.rebuild()
    assert vault_index.is_built() is True


def test_missing_directory_does_not_raise(monkeypatch, tmp_path):
    monkeypatch.setenv("PULSE_VAULT_PATH", str(tmp_path / "nonexistent"))
    monkeypatch.setenv("PULSE_VAULT_SUBPATH", "notes")
    vault_index._titles.clear()
    vault_index._full.clear()
    vault_index._built = False
    vault_index.rebuild()  # should not raise
    assert vault_index.titles() == []
    assert vault_index.full() == []

import json


def test_parses_entities_field(mock_anthropic):
    mock_anthropic.returns(json.dumps({
        "summary": "S&P fell as rates rose.",
        "main_points": ["10Y at 4.6%", "Tech led down"],
        "why_it_matters": "Duration sensitivity hit growth names.",
        "market_impact": "Equities -1%, bonds -0.5%.",
        "framing": "Bond rout narrative.",
        "plain_english": "",
        "tags": ["Markets"],
        "entities": [
            {"kind": "company", "key": "NVDA", "name": "Nvidia",
             "bullets": ["NVDA -3% as duration weighed on growth",
                         "Citi cut PT to $140 citing rate sensitivity"]},
            {"kind": "concept", "key": "duration", "name": "Duration",
             "bullets": ["10Y +18bp tied to inflation print"]},
        ],
    }))
    from app.services.summaries import maybe_model_story_summary_anthropic
    result = maybe_model_story_summary_anthropic({
        "title": "Bond Rout", "fullContent": "body", "source": "WSJ",
    })
    assert result is not None
    assert len(result["entities"]) == 2
    assert result["entities"][0]["kind"] == "company"
    assert result["entities"][0]["key"] == "NVDA"
    assert result["entities"][0]["bullets"] == [
        "NVDA -3% as duration weighed on growth",
        "Citi cut PT to $140 citing rate sensitivity",
    ]


def test_parses_entities_legacy_quote_field_still_accepted(mock_anthropic):
    """Older mocks/responses without `bullets` still parse; bullets defaults to None."""
    mock_anthropic.returns(json.dumps({
        "summary": "x", "main_points": ["a"], "why_it_matters": "y",
        "market_impact": "z", "framing": "w", "plain_english": "", "tags": [],
        "entities": [{"kind": "company", "key": "NVDA", "name": "Nvidia",
                      "quote": "NVDA up 2%."}],
    }))
    from app.services.summaries import maybe_model_story_summary_anthropic
    result = maybe_model_story_summary_anthropic({
        "title": "T", "fullContent": "body", "source": "WSJ",
    })
    assert result is not None
    assert result["entities"][0]["bullets"] is None
    assert result["entities"][0]["quote"] == "NVDA up 2%."


def test_missing_entities_field_defaults_to_empty(mock_anthropic):
    mock_anthropic.returns(json.dumps({
        "summary": "x", "main_points": ["a"], "why_it_matters": "y",
        "market_impact": "z", "framing": "w", "plain_english": "",
        "tags": [],
    }))
    from app.services.summaries import maybe_model_story_summary_anthropic
    result = maybe_model_story_summary_anthropic({
        "title": "Untitled", "fullContent": "", "source": "RSS",
    })
    assert result is not None
    assert result["entities"] == []


def test_summarize_story_records_mentions(mock_anthropic, db_initialized):
    import json
    mock_anthropic.returns(json.dumps({
        "summary": "s", "main_points": ["a"], "why_it_matters": "y",
        "market_impact": "z", "framing": "w", "plain_english": "", "tags": [],
        "entities": [
            {"kind": "company", "key": "NVDA", "name": "Nvidia",
             "bullets": ["NVDA up 2% on guidance raise",
                         "Mentions Blackwell ramp into Q4"]},
        ],
    }))
    from app.services.summaries import summarize_story
    from app.services import dossiers
    summarize_story(
        {"emailId": "msg_int_1", "title": "T", "fullContent": "body", "source": "WSJ"},
        refresh=True, allow_model=True,
    )
    eid = dossiers.get_entity_id("company", "NVDA")
    assert eid is not None
    assert dossiers.count_mentions(eid) == 1
    mentions = dossiers.list_mentions(eid, limit=5)
    assert mentions[0]["bullets"] == [
        "NVDA up 2% on guidance raise",
        "Mentions Blackwell ramp into Q4",
    ]

import json
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(db_initialized, mock_anthropic):
    # Make every snapshot call return a canned payload
    mock_anthropic.returns(json.dumps({
        "overview": "x", "plain_english": "y", "bull_thesis": "b",
        "bear_thesis": "z", "topic_tags": [], "key_themes": [], "notable_quotes": [],
    }))
    from app.main import app
    return TestClient(app)


def test_get_dossiers_returns_seed(client):
    # init_db seeds 5 follows: NVDA, JPM, AAPL, ai_chips, duration
    r = client.get("/api/dossiers")
    assert r.status_code == 200
    keys = {e["key"] for e in r.json()["followed"]}
    assert {"NVDA", "JPM", "AAPL", "ai_chips", "duration"}.issubset(keys)


def test_follow_creates_and_lists(client):
    r = client.post("/api/dossiers/follow",
                    json={"kind": "company", "key": "NVDA", "name": "Nvidia"})
    assert r.status_code == 200
    body = r.json()
    assert "id" in body
    listed = client.get("/api/dossiers").json()
    assert any(e["key"] == "NVDA" for e in listed["followed"])


def test_get_one_dossier_returns_snapshot(client):
    r = client.post("/api/dossiers/follow",
                    json={"kind": "company", "key": "NVDA", "name": "Nvidia"})
    eid = r.json()["id"]
    detail = client.get(f"/api/dossiers/{eid}").json()
    assert detail["entity"]["name"] == "Nvidia"
    assert "snapshot" in detail
    assert "mentions" in detail


def test_unfollow_removes_from_followed(client):
    r = client.post("/api/dossiers/follow",
                    json={"kind": "company", "key": "NVDA", "name": "Nvidia"})
    eid = r.json()["id"]
    client.post(f"/api/dossiers/{eid}/unfollow")
    listed = client.get("/api/dossiers").json()
    assert all(e["key"] != "NVDA" for e in listed["followed"])


def test_discover_returns_unfollowed_with_mentions(client):
    # Use AMD — not in seed, so it starts unfollowed and qualifies for Discover
    from app.services import dossiers
    eid = dossiers.upsert_entity("company", "AMD", "AMD")
    for i in range(3):
        dossiers.record_mention(eid, f"m{i}", "AMD sold...")
    found = client.get("/api/dossiers/discover").json()["candidates"]
    assert any(c["key"] == "AMD" for c in found)


def test_end_to_end_flow(client, mock_anthropic):
    # 1) Stub a snapshot response and follow NVDA
    mock_anthropic.returns(json.dumps({
        "overview": "AI bellwether.",
        "plain_english": "NVDA makes the AI chips.",
        "bull_thesis": "Capex up.",
        "bear_thesis": "China risk.",
        "topic_tags": ["AI/Tech"],
        "key_themes": [],
        "notable_quotes": [],
        # Empty list is a valid Sonnet output. The lazy-upgrade in
        # /api/dossiers/{id} only fires when the key is MISSING, so an empty
        # list here proves the route respects that distinction.
        "jargon": [],
    }))
    r = client.post("/api/dossiers/follow",
                    json={"kind": "company", "key": "NVDA", "name": "Nvidia"})
    assert r.status_code == 200
    nvda_id = r.json()["id"]
    initial_mention_count = 0

    # follow_route is async (backfill + snapshot run in a background thread).
    # Wait briefly for the warmup thread to complete using the mocked anthropic
    # stub — in tests this finishes in milliseconds, but poll up to 2s to be safe.
    import time as _time
    from app.services import dossiers as _ds
    for _ in range(40):
        if _ds.get_snapshot(nvda_id):
            break
        _time.sleep(0.05)

    # 2) Stub a Haiku response with entities
    mock_anthropic.returns(json.dumps({
        "summary": "Nvidia hits highs.",
        "main_points": ["NVDA +4%"],
        "why_it_matters": "AI capex thesis intact.",
        "market_impact": "Tech-heavy indices up.",
        "framing": "Bullish.",
        "plain_english": "",
        "tags": ["AI/Tech"],
        "entities": [{"kind": "company", "key": "NVDA", "name": "Nvidia",
                      "bullets": ["NVDA +4% on capex commentary",
                                  "Citi raised PT to $200"]}],
    }))
    from app.services.summaries import summarize_story
    summarize_story({"emailId": "e2e_1", "title": "T", "fullContent": "x", "source": "WSJ"},
                    refresh=True, allow_model=True)

    # 3) Confirm via API the mention was recorded
    listed = client.get("/api/dossiers").json()
    nvda = next((e for e in listed["followed"] if e["key"] == "NVDA"), None)
    assert nvda is not None
    assert nvda["mention_count"] > initial_mention_count

    # 4) Fetch the dossier page and verify snapshot + mentions
    detail = client.get(f"/api/dossiers/{nvda_id}").json()
    assert detail["entity"]["key"] == "NVDA"
    assert detail["snapshot"]["overview"] == "AI bellwether."
    assert len(detail["mentions"]) >= 1

from app.services import dossier_taxonomy as dt


def test_sectors_present_and_unique():
    assert isinstance(dt.SECTORS, dict)
    assert "ai_chips" in dt.SECTORS
    assert "banks" in dt.SECTORS
    assert dt.SECTORS["ai_chips"]["name"] == "AI / Chips"
    assert len(set(dt.SECTORS.keys())) == len(dt.SECTORS)  # unique keys


def test_concepts_present_and_unique():
    assert isinstance(dt.CONCEPTS, dict)
    assert "duration" in dt.CONCEPTS
    assert "dcf" in dt.CONCEPTS
    assert len(set(dt.CONCEPTS.keys())) == len(dt.CONCEPTS)


def test_seed_follows_match_taxonomy():
    for kind, key, _name in dt.SEED_FOLLOWS:
        assert kind in {"company", "sector", "concept"}
        if kind == "sector":
            assert key in dt.SECTORS, f"seed sector {key!r} missing from SECTORS"
        elif kind == "concept":
            assert key in dt.CONCEPTS, f"seed concept {key!r} missing from CONCEPTS"

"""Regression tests for detect_tier.

Two bugs are pinned here, both found by grading the shipped classifier against a
2,049-message corpus of real NYT/WSJ newsletters:

  1. substring matching - `"war" in text` fired on "forwarded", "toward", "warm"
     and "software"; `"nato" in text` fired on "senator" and "coordinator". A
     Wirecutter newsletter about linen sheets was tagged geopolitical because its
     footer said "forwarded".
  2. first-match-wins ordering - geopolitical was tested first against the whole
     body, so any newsletter mentioning war/china/trump anywhere in ~5,000
     characters became geopolitical. 87.7% of the corpus landed in that bucket.

The fix must not overcorrect into the opposite bug: a bare \\btoken\\b test drops
legitimate plurals ("sanctions" matched 177 messages by substring but only 3 as
\\bsanction\\b), so inflections are spelled out per concept.
"""

from app.services import gmail


FOOTER = (
    " You received this email because you signed up. "
    "This email was forwarded to you? Sign up here. Manage preferences."
)


def test_tier_vocabulary_is_stable():
    """briefing.py and summaries.py weight these exact strings."""
    assert set(gmail.TIER_KEYWORDS) == {"geopolitical", "finance", "lifestyle"}
    tiers = {
        gmail.detect_tier(subject, body)
        for subject, body in [
            ("The World: Iran strikes back", "Missiles hit Tehran overnight."),
            ("Markets A.M.: Stocks slide", "The Fed held interest rates steady."),
            ("Our favorite vacuums", "We tested 30 vacuums for your kitchen."),
            ("", ""),
        ]
    }
    assert tiers <= {"geopolitical", "finance", "lifestyle", "mixed"}


class TestSubstringMatching:
    def test_forwarded_is_not_a_war(self):
        subject = "Linen sheets that don't cost a fortune"
        body = "Our favorite linen sheets are on sale. The best pillows too." + FOOTER
        assert gmail.detect_tier(subject, body) == "lifestyle"
        assert gmail.tier_scores(subject, body)["geopolitical"] == 0

    def test_decoy_words_score_zero(self):
        """Words that merely CONTAIN a keyword. ('senator' is left out on
        purpose: it contains "nato" but is also a real geopolitical word.)"""
        decoys = (
            "This email was forwarded toward a warm software update, "
            "sent by the coordinator, and the designator was warranted."
        )
        assert gmail.tier_scores("A note", decoys)["geopolitical"] == 0

    def test_real_war_words_still_match(self):
        for text in ("The war in Iran", "Postwar warfare doctrine", "Two wars at once"):
            assert gmail.tier_scores(text, "")["geopolitical"] > 0, text

    def test_plurals_and_inflections_survive_the_fix(self):
        """The naive \\btoken\\b fix would fail every one of these."""
        for text in (
            "New sanctions on Moscow",
            "The sanctioned bank",
            "Elections across Europe",
            "Chinese exporters",
            "Russian troops",
            "Israeli airstrikes",
        ):
            assert gmail.tier_scores(text, "")["geopolitical"] > 0, text


class TestScoringBeatsFirstMatch:
    def test_finance_wins_when_it_dominates(self):
        subject = "Markets A.M.: Inflation Is Picking Investors' Pockets"
        body = (
            "Tech stocks went on another roller-coaster ride, and futures point to "
            "more losses. Investors are finding refuge in bonds as the Fed weighs "
            "interest rates. Elsewhere, the war in Iran drags on."
        )
        assert gmail.detect_tier(subject, body) == "finance"

    def test_one_passing_mention_does_not_hijack_the_tier(self):
        """The old version returned geopolitical off the word 'China' alone."""
        subject = "Our favorite air purifiers"
        body = (
            "We tested 40 air purifiers for dust, mold and smoke. Our top pick is "
            "quiet enough for a bedroom. Most are manufactured in China." + FOOTER
        )
        assert gmail.detect_tier(subject, body) == "lifestyle"

    def test_subject_outweighs_the_body(self):
        subject = "The World: Iran and Israel trade strikes"
        body = (
            "A ceasefire looks unlikely. " + "Markets were mixed on the news. " * 8
        )
        scores = gmail.tier_scores(subject, body)
        assert scores["geopolitical"] > scores["finance"]

    def test_repeated_word_cannot_carry_a_tier(self):
        """Concepts are counted distinctly, so 20 'Trump's are still one concept."""
        one = gmail.tier_scores("A note", "Trump said so.")
        many = gmail.tier_scores("A note", "Trump said so. " * 20)
        assert one["geopolitical"] == many["geopolitical"]

    def test_empty_input_is_mixed(self):
        assert gmail.detect_tier("", "") == "mixed"
        assert gmail.detect_tier("(No subject)", "") == "mixed"

    def test_grab_bag_without_a_dominant_theme_is_mixed(self):
        subject = "N.Y. Today: A.I. in the classroom"
        body = (
            "It's Wednesday. Today we'll look at what role artificial intelligence "
            "could play in classrooms in New York City. We'll also get details on "
            "the mayor's free child care program."
        )
        assert gmail.detect_tier(subject, body) == "mixed"


class TestPatternHygiene:
    def test_every_keyword_compiles_and_is_word_bounded(self):
        for tier, pattern in gmail.TIER_PATTERNS.items():
            assert pattern.pattern.startswith(r"\b(?:"), tier
            assert pattern.pattern.endswith(r")\b"), tier

    def test_no_duplicate_concepts_within_a_tier(self):
        for tier, keywords in gmail.TIER_KEYWORDS.items():
            assert len(set(keywords)) == len(keywords), f"{tier} has a duplicate entry"

    def test_scores_cover_every_tier(self):
        scores = gmail.tier_scores("Anything", "at all")
        assert set(scores) == set(gmail.TIER_KEYWORDS)
        assert all(isinstance(v, int) for v in scores.values())

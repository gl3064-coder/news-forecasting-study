from markets_v2 import resolve, MARKETS


def test_exact_name_resolves():
    assert resolve("gold") == "GLD"
    assert resolve("nasdaq") == "^NDX"


def test_normalisation_is_case_and_space_insensitive():
    assert resolve("  GOLD  ") == "GLD"
    assert resolve("S&P  500") == "^GSPC"
    assert resolve("The Dow") == "^DJI"


def test_unknown_market_returns_none():
    assert resolve("turkish lira") is None
    assert resolve("shipping rates") is None


def test_bond_price_and_bond_yield_are_different_tickers():
    # "bonds" is a price; "10-year yield" is a yield. They move opposite ways,
    # so mapping both to one ticker would silently invert half the calls.
    assert resolve("bonds") == "TLT"
    assert resolve("10-year yield") == "^TNX"
    assert resolve("bonds") != resolve("10-year yield")


def test_every_ticker_in_the_table_is_a_nonempty_string():
    assert MARKETS
    for name, ticker in MARKETS.items():
        assert name == name.strip().lower(), f"{name!r} is not normalised"
        assert isinstance(ticker, str) and ticker, name


# ---- Amendment 1: normalisation cascade -------------------------------------

def test_trailing_modifiers_do_not_block_resolution():
    assert resolve("nasdaq 100 futures") == "^NDX"
    assert resolve("the 10-year treasury yield") == "^TNX"
    assert resolve("the U.S. 10-year Treasury yield") == "^TNX"


def test_parenthetical_qualifier_wins_because_it_is_more_specific():
    # Brent and WTI are different instruments. Dropping the parenthetical
    # would silently score a Brent call against WTI.
    assert resolve("crude oil (brent)") == "BNO"
    assert resolve("crude oil (WTI)") == "USO"


def test_parenthetical_that_names_nothing_falls_back_to_the_outer_term():
    assert resolve("nasdaq 100 (NQ futures)") == "^NDX"


def test_genuinely_exotic_markets_stay_unscoreable():
    assert resolve("Argentine dollar sovereign bonds") is None
    assert resolve("European natural gas (TTF front-month)") is None
    assert resolve("the 2-year Treasury yield") is None


def test_resolve_exact_preserves_the_pre_amendment_rule():
    from markets_v2 import resolve_exact
    assert resolve_exact("nasdaq 100 futures") is None
    assert resolve_exact("nasdaq 100") == "^NDX"


# ---- Amendment 2: forward-only tradeable overrides --------------------------

def test_retrospective_mapping_is_unchanged():
    # The frozen sample must keep scoring exactly as it did.
    assert resolve("nasdaq") == "^NDX"
    assert resolve("s&p 500") == "^GSPC"


def test_forward_mapping_swaps_indices_for_tradeable_etfs():
    assert resolve("nasdaq", forward=True) == "QQQ"
    assert resolve("s&p 500", forward=True) == "SPY"


def test_forward_mapping_leaves_yields_on_the_yield():
    # The tradeable proxies agree with the yield's direction only ~80% of the
    # time, so swapping would add measurement error, not remove it.
    assert resolve("10-year yield", forward=True) == "^TNX"
    assert resolve("30-year yield", forward=True) == "^TYX"


def test_forward_mapping_leaves_everything_else_alone():
    for name in ("gold", "brent", "bonds", "yen", "bitcoin", "technology"):
        assert resolve(name) == resolve(name, forward=True), name


def test_normalisation_cascade_still_applies_on_the_forward_table():
    assert resolve("nasdaq 100 futures", forward=True) == "QQQ"
    assert resolve("crude oil (brent)", forward=True) == "BNO"


# ---- Amendment 3: forward-only additions ------------------------------------

def test_forward_additions_resolve_only_forward():
    for name in ("micron", "uber", "cannabis stocks", "nikkei 225"):
        assert resolve(name) is None, f"{name} leaked into the frozen table"
        assert resolve(name, forward=True) is not None, name


def test_forward_additions_do_not_shadow_frozen_entries():
    # Every name in the frozen table must still resolve forward to the same
    # ticker, or an override, and never to something unrelated.
    from markets_v2 import MARKETS, FORWARD_OVERRIDES
    for name, ticker in MARKETS.items():
        assert resolve(name, forward=True) == FORWARD_OVERRIDES.get(ticker, ticker), name


def test_untradeable_gaps_stay_gaps():
    # No stretched proxies: these have no clean US-listed cash-session
    # equivalent and must remain unscoreable rather than be approximated.
    for name in ("the UK 10-year gilt yield", "European natural gas (TTF)",
                 "the 2-year Treasury yield", "Argentine sovereign dollar bonds"):
        assert resolve(name, forward=True) is None, name


def test_industry_words_are_not_stripped_generically():
    # "technology stocks" must still reach the sector ETF. A generic rule that
    # stripped "technology" to make "Micron Technology" resolve would break it.
    assert resolve("technology stocks") == "XLK"
    assert resolve("technology stocks", forward=True) == "XLK"
    assert resolve("Micron Technology (MU) shares", forward=True) == "MU"


def test_corporate_suffixes_and_abbreviating_periods_are_dropped():
    assert resolve("Warner Bros. Discovery shares", forward=True) == "WBD"
    assert resolve("Bally's Corporation stock", forward=True) == "BALY"

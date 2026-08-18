from score_v2 import corrected_hit, statistic


def test_up_call_on_a_market_that_always_rises_scores_zero():
    # market up-rate 1.0, called up, hit. Expectation is also 1.0.
    assert corrected_hit("up", "up", up_rate=1.0) == 0.0


def test_down_call_is_scored_against_the_down_rate_not_the_up_rate():
    # A market that rises 70% of the time falls 30%. A correct DOWN call beat
    # a 0.30 expectation, so it is worth +0.70 -- not +0.30.
    assert abs(corrected_hit("down", "down", up_rate=0.7) - 0.70) < 1e-9


def test_a_wrong_call_is_negative_by_the_expectation():
    assert abs(corrected_hit("up", "down", up_rate=0.6) - (-0.6)) < 1e-9


def test_direction_aware_baseline_neutralises_a_persistent_tilt():
    # A forecaster who always says "down" on a market that rises 60% of the
    # time is right 40% of the time. Expectation for a down call is 0.40.
    # Skill is therefore zero, not -20pp.
    rows = []
    for i in range(100):
        actual = "up" if i < 60 else "down"
        rows.append(("d%d" % i, "X", "down", actual, 0.6))
    assert abs(statistic(rows)) < 1e-9


def test_a_real_edge_shows_up_as_positive():
    # Always right, on a coin-flip market. Expectation 0.5 every time.
    rows = [("d%d" % i, "X", "up", "up", 0.5) for i in range(50)]
    assert abs(statistic(rows) - 0.5) < 1e-9


def test_statistic_of_no_rows_is_nan():
    assert statistic([]) != statistic([])

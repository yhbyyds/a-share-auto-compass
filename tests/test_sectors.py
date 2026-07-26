import pandas as pd

from market_forecast.sectors import _direction, _outlook, _recent_history


def test_sector_direction_requires_validated_quality():
    assert _direction(0.60, 0.01, True) == "偏强"
    assert _direction(0.60, 0.01, False) == "震荡"
    assert _direction(0.40, -0.01, True) == "偏弱"


def test_relative_outlook_uses_probability_and_expected_excess():
    assert _outlook(0.57, 0.005) == "相对领先"
    assert _outlook(0.43, -0.005) == "相对落后"
    assert _outlook(0.57, -0.005) == "相对中性"


def test_recent_history_is_normalized_and_limited_to_60_days():
    index = pd.bdate_range("2026-04-01", periods=65)
    frame = pd.DataFrame(
        {"close": [100 + value for value in range(65)]},
        index=index,
    )
    benchmark = pd.Series(
        [200 + value for value in range(65)],
        index=index,
    )

    history = _recent_history(frame, benchmark)

    assert len(history) == 60
    assert history[0]["sector"] == 100
    assert history[0]["benchmark"] == 100
    assert history[0]["relative"] == 100
    assert history[-1]["date"] == index[-1].strftime("%Y-%m-%d")
    assert history[-1]["sector"] > history[-1]["benchmark"]

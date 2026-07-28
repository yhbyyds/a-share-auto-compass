import pandas as pd

from market_forecast.sectors import (
    _cross_sectional_signal,
    _direction,
    _outlook,
    _recent_history,
    _signal_strength,
)


def test_sector_direction_requires_validated_quality():
    assert _direction(0.60, 0.01, True) == "偏强"
    assert _direction(0.68, 0.01, True, 78) == "强偏强"
    assert _direction(0.60, 0.01, False) == "震荡"
    assert _direction(0.40, -0.01, True) == "偏弱"
    assert _direction(0.32, -0.01, True, 78) == "强偏弱"


def test_signal_strength_does_not_inflate_failed_validation():
    good_score, good_band = _signal_strength(0.70, 0.01, 0.05, True)
    weak_score, weak_band = _signal_strength(0.70, 0.01, 0.05, False)
    assert good_score == 100
    assert good_band == "强"
    assert weak_score == 50
    assert weak_band == "中"


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


def test_cross_sectional_signal_stays_neutral_when_spread_is_small():
    rows = [
        {"up_probability": 50.0 + index * 0.2, "expected_excess": 0.0001 * index,
         "outperform_probability": 50.0, "confidence": "中"}
        for index in range(5)
    ]
    _cross_sectional_signal(rows)
    assert all(row["relative_signal"] == "相对中性" for row in rows)
    assert rows[0]["relative_signal_spread"]["separated"] is False


def test_cross_sectional_signal_marks_only_validated_extremes():
    rows = [
        {"up_probability": 42.0, "expected_excess": -0.6, "outperform_probability": 44.0, "confidence": "中"},
        {"up_probability": 50.0, "expected_excess": 0.0, "outperform_probability": 50.0, "confidence": "中"},
        {"up_probability": 60.0, "expected_excess": 0.6, "outperform_probability": 56.0, "confidence": "中"},
    ]
    _cross_sectional_signal(rows)
    assert rows[0]["relative_signal"] == "相对偏弱"
    assert rows[2]["relative_signal"] == "相对偏强"

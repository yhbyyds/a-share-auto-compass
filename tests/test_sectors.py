import pandas as pd

from market_forecast.sectors import (
    _cross_sectional_signal,
    _direction,
    _excess_metrics,
    _outlook,
    _recent_history,
    _regression_weights,
    _selection_metrics,
    _signal_strength,
    _tomorrow_selection,
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


def test_excess_validation_downweights_a_regression_that_misses_baseline():
    dates = pd.Series(pd.bdate_range("2026-01-01", periods=6))
    actual = pd.Series([0.01, -0.01, 0.02, -0.02, 0.01, -0.01])
    good = actual * 0.8
    poor = -actual * 0.8

    good_metrics = _excess_metrics(actual, good, dates)
    poor_metrics = _excess_metrics(actual, poor, dates)

    assert good_metrics["mae_skill"] > 0
    assert good_metrics["ranking_weight"] > poor_metrics["ranking_weight"]
    assert poor_metrics["ranking_weight"] == 0.25


def test_selection_validation_requires_oof_top_bottom_edge():
    dates = []
    sectors = []
    actual = []
    predicted = []
    for date in pd.bdate_range("2025-01-01", periods=100):
        for rank in range(6):
            dates.append(date)
            sectors.append(f"s{rank}")
            predicted.append(float(rank))
            actual.append((rank - 2.5) / 100)
    train = pd.DataFrame({"date": dates, "sector": sectors})

    metrics = _selection_metrics(train, pd.Series(actual), pd.Series(predicted))

    assert metrics["samples"] == 100
    assert metrics["top_bottom_excess"] > 0
    assert metrics["reliable"] is True


def test_regression_weights_favor_the_lower_oof_error_component():
    actual = pd.Series([0.01, -0.01, 0.02, -0.02])
    predictions = pd.DataFrame(
        {
            "good": [0.009, -0.009, 0.018, -0.018],
            "poor": [-0.009, 0.009, -0.018, 0.018],
        }
    )

    weights = _regression_weights(predictions, actual)

    assert weights["good"] > weights["poor"]
    assert sum(weights.values()) == 1.0


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


def test_tomorrow_selection_separates_ranking_from_validated_direction():
    sectors = []
    for index, score in enumerate((80, 60, 40), start=1):
        direction = "偏强" if index == 1 else "震荡"
        sectors.append(
            {
                "key": f"s{index}",
                "name": f"行业{index}",
                "is_composite": False,
                "validation": {
                    "accuracy": 57.0 if index == 1 else 51.0,
                    "baseline": 52.0,
                },
                "days": [
                    {
                        "date": "2026-07-30",
                        "direction": direction,
                        "confidence": "中" if index == 1 else "低",
                        "relative_signal": (
                            "相对偏强" if index == 1 else "相对中性"
                        ),
                        "relative_signal_score": score,
                        "relative_signal_spread": {
                            "probability_pp": 8.0,
                            "expected_excess_pp": 0.6,
                            "separated": True,
                        },
                        "up_probability": 60.0 if index == 1 else 50.0,
                        "expected_return": 0.4 if index == 1 else 0.0,
                        "expected_excess": 0.5 if index == 1 else -0.1,
                        "signal_strength": score,
                    }
                ],
            }
        )

    result = _tomorrow_selection(sectors)

    assert result["up"][0]["key"] == "s1"
    assert result["up"][0]["status"] == "模型偏强候选"
    assert result["down"][0]["key"] == "s3"
    assert result["down"][0]["status"] == "相对落后观察"
    assert result["validated_up_count"] == 1
    assert result["score_spread"] > 0

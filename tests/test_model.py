import numpy as np
import pandas as pd

from market_forecast.model import (
    _direction,
    _evidence_score,
    _next_weekdays,
    _rsi,
    _select_ensemble,
    build_features,
)


def test_next_weekdays_skips_weekend():
    result = _next_weekdays(pd.Timestamp("2026-07-24"))
    assert [value.strftime("%Y-%m-%d") for value in result] == [
        "2026-07-27",
        "2026-07-28",
        "2026-07-29",
        "2026-07-30",
        "2026-07-31",
    ]


def test_direction_requires_probability_and_return_agreement():
    assert _direction(0.60, 0.002) == "偏强"
    assert _direction(0.40, -0.002) == "偏弱"
    assert _direction(0.60, -0.002) == "震荡"


def test_rsi_has_valid_range():
    series = pd.Series(np.linspace(10, 20, 40) + np.sin(np.arange(40)))
    value = _rsi(series).dropna().iloc[-1]
    assert 0 <= value <= 100


def test_strategy_features_are_available_without_lookahead():
    dates = pd.date_range("2025-01-01", periods=140, freq="B")

    def frame(scale: float) -> pd.DataFrame:
        close = pd.Series(
            100 * scale
            + np.linspace(0, 12 * scale, len(dates))
            + np.sin(np.arange(len(dates)) * 0.7) * scale,
            index=dates,
        )
        return pd.DataFrame(
            {
                "open": close * 0.998,
                "close": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "volume": np.linspace(900_000, 1_100_000, len(dates)),
                "amount": close * np.linspace(900_000, 1_100_000, len(dates)),
            },
            index=dates,
        )

    features, close = build_features(
        {
            "sse": frame(1.0),
            "csi300": frame(0.95),
            "csi1000": frame(1.05),
        }
    )

    expected = {
        "breakout_20d",
        "breakout_60d",
        "atr14_pct",
        "trend_efficiency_20",
        "volume_price_confirmation",
        "relative_strength_csi300_5d",
        "relative_strength_csi300_20d",
    }
    assert expected.issubset(features.columns)
    assert len(features) > 0
    assert features.index.max() == close.index.max()
    assert np.isfinite(features[list(expected)].to_numpy()).all()
    assert features["trend_efficiency_20"].between(0, 1.01).all()


def test_evidence_score_rewards_validated_edge_and_agreement():
    strong = {
        "recent_accuracy": 0.58,
        "recent_baseline": 0.52,
        "auc": 0.57,
        "brier": 0.22,
        "high_conf_accuracy": 0.60,
    }
    weak = {
        "recent_accuracy": 0.51,
        "recent_baseline": 0.52,
        "auc": 0.49,
        "brier": 0.27,
        "high_conf_accuracy": None,
    }

    strong_score = _evidence_score(
        strong,
        {"a": 0.58, "b": 0.56, "c": 0.57},
    )
    weak_score = _evidence_score(
        weak,
        {"a": 0.65, "b": 0.45, "c": 0.50},
    )

    assert strong_score["score"] > weak_score["score"]
    assert strong_score["label"] in {"中等", "较高"}
    assert weak_score["label"] == "较低"


def test_ensemble_uses_score_weights_only_for_oof_quality_gain():
    predictions = pd.DataFrame(
        {
            "skilled": [0.9, 0.8, 0.1, 0.2],
            "neutral": [0.5, 0.5, 0.5, 0.5],
        }
    )
    actual = pd.Series([1, 1, 0, 0])
    _, method, candidates = _select_ensemble(
        predictions,
        actual,
        {"skilled": 0.9, "neutral": 0.1},
    )

    assert method == "score_weighted"
    assert (
        candidates["score_weighted"]["brier"]
        < candidates["equal_weighted"]["brier"]
    )


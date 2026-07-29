import numpy as np
import pandas as pd

from market_forecast.model import (
    _direction,
    _next_weekdays,
    _rsi,
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


import numpy as np
import pandas as pd

from market_forecast.model import _direction, _next_weekdays, _rsi


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


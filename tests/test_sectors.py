from market_forecast.sectors import _direction, _outlook


def test_sector_direction_requires_validated_quality():
    assert _direction(0.60, 0.01, True) == "偏强"
    assert _direction(0.60, 0.01, False) == "震荡"
    assert _direction(0.40, -0.01, True) == "偏弱"


def test_relative_outlook_uses_probability_and_expected_excess():
    assert _outlook(0.57, 0.005) == "相对领先"
    assert _outlook(0.43, -0.005) == "相对落后"
    assert _outlook(0.57, -0.005) == "相对中性"

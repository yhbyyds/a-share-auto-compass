from market_forecast.watchlist import _rank_candidates


def _candidate(code: str, momentum20: float) -> dict:
    return {
        "code": code,
        "name": f"候选{code}",
        "symbol": f"sz{code}",
        "price": 12.0,
        "change": 1.0,
        "amount": 2e9,
        "turnover": 2.0,
        "pe": 20.0,
        "pb": 2.0,
        "ma20": 11.5,
        "ma60": 10.5,
        "momentum5": 0.05,
        "momentum20": momentum20,
        "momentum60": 0.15,
        "relative20": momentum20 - 0.03,
        "volatility20": 0.025,
        "drawdown60": -0.05,
        "volume_z20": 0.5,
        "rsi14": 60.0,
        "invalid_level": 11.3,
        "data_date": "2026-07-24",
    }


def test_watchlist_does_not_fill_slots_with_overextended_stock():
    normal = _candidate("000001", 0.12)
    overextended = _candidate("000002", 0.80)
    result = _rank_candidates([normal, overextended], count=6)
    assert [item["code"] for item in result] == ["000001"]


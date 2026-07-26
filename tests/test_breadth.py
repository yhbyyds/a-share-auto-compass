from market_forecast.data import _summarize_breadth


def test_breadth_summary_requires_full_market_coverage() -> None:
    rows = [
        {"change": 1.0 if index % 2 else -1.0, "amount": 100_000_000}
        for index in range(4100)
    ]

    breadth = _summarize_breadth(
        rows,
        change_key="change",
        amount_key="amount",
        source="test",
    )

    assert breadth["stocks"] == 4100
    assert breadth["advancers"] == 2050
    assert breadth["decliners"] == 2050
    assert breadth["turnover_yi"] == 4100
    assert breadth["status"] == "live"

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from market_forecast.data import (
    IndexSpec,
    _summarize_breadth,
    apply_tencent_close_overlay,
)


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


def _quote_payload(timestamp: str) -> str:
    fields = [""] * 40
    fields[0] = "1"
    fields[1] = "SSE"
    fields[2] = "000001"
    fields[3] = "3832.26"
    fields[4] = "3804.69"
    fields[5] = "3833.54"
    fields[30] = timestamp
    fields[33] = "3847.09"
    fields[34] = "3822.37"
    fields[36] = "597529427"
    fields[37] = "118768155"
    return 'v_sh000001="' + "~".join(fields) + '";'


def _single_index_data() -> tuple[dict[str, pd.DataFrame], tuple[IndexSpec, ...]]:
    frame = pd.DataFrame(
        {
            "open": [3810.0],
            "close": [3804.69],
            "high": [3830.0],
            "low": [3790.0],
            "volume": [1.0],
            "amount": [1.0],
            "amplitude": [1.0],
            "pct": [-0.62],
            "change": [-23.0],
            "turnover": [float("nan")],
        },
        index=pd.DatetimeIndex(["2026-07-30"]),
    )
    return {"sse": frame}, (
        IndexSpec("sse", "SSE", "1.000001", "sh000001"),
    )


def test_finalized_tencent_quote_overlays_lagging_daily_kline() -> None:
    data, specs = _single_index_data()
    result = apply_tencent_close_overlay(
        data,
        specs,
        payload=_quote_payload("20260731161220"),
        now=datetime(2026, 7, 31, 16, 20, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result["status"] == "applied"
    assert result["updated_keys"] == ["sse"]
    assert data["sse"].index.max() == pd.Timestamp("2026-07-31")
    assert round(float(data["sse"].iloc[-1]["pct"]), 2) == 0.72
    assert float(data["sse"].iloc[-1]["close"]) == 3832.26


def test_intraday_tencent_quote_is_not_used_as_a_close() -> None:
    data, specs = _single_index_data()
    result = apply_tencent_close_overlay(
        data,
        specs,
        payload=_quote_payload("20260731113000"),
        now=datetime(2026, 7, 31, 11, 35, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result["status"] == "not_required"
    assert data["sse"].index.max() == pd.Timestamp("2026-07-30")

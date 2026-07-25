from __future__ import annotations

from copy import deepcopy
from datetime import date

from market_forecast.quality import validate_forecast


def valid_forecast() -> dict:
    dates = [
        "2026-07-27",
        "2026-07-28",
        "2026-07-29",
        "2026-07-30",
        "2026-07-31",
    ]
    days = [
        {"date": item, "up_probability": 50 + index}
        for index, item in enumerate(dates)
    ]
    sectors = [
        {"key": f"sector_{index}", "name": f"行业{index}", "days": deepcopy(days)}
        for index in range(10)
    ]
    return {
        "meta": {"data_through": "2026-07-24"},
        "market": {"weekly_up_probability": 52},
        "days": days,
        "validation": {
            "samples": 500,
            "daily_direction_accuracy": 54,
            "weekly_direction_accuracy": 53,
            "baseline_accuracy": 52,
        },
        "sector_forecast": {
            "data_through": "2026-07-24",
            "sectors": sectors,
        },
        "breadth": {"stocks": 0},
        "events": [
            {
                "title": "已确认事件",
                "status": "已确认",
                "url": "https://example.com/official",
            }
        ],
        "event_radar": {"daily_risk": []},
    }


def test_valid_forecast_passes_with_breadth_warning() -> None:
    result = validate_forecast(valid_forecast(), today=date(2026, 7, 26))

    assert result.passed
    assert result.metrics["forecast_days"] == 5
    assert result.metrics["sector_count"] == 10
    assert any("宽度样本不足" in warning for warning in result.warnings)


def test_stale_data_blocks_publication() -> None:
    forecast = valid_forecast()
    forecast["meta"]["data_through"] = "2026-07-10"
    forecast["sector_forecast"]["data_through"] = "2026-07-10"

    result = validate_forecast(
        forecast,
        today=date(2026, 7, 26),
        max_data_age_days=5,
    )

    assert not result.passed
    assert any("行情数据已过期" in error for error in result.errors)


def test_data_regression_blocks_publication() -> None:
    previous = valid_forecast()
    previous["meta"]["data_through"] = "2026-07-25"

    result = validate_forecast(
        valid_forecast(),
        previous=previous,
        today=date(2026, 7, 26),
    )

    assert not result.passed
    assert any("行情日期倒退" in error for error in result.errors)


def test_unconfirmed_event_blocks_publication() -> None:
    forecast = valid_forecast()
    forecast["events"][0]["status"] = "待核实"

    result = validate_forecast(forecast, today=date(2026, 7, 26))

    assert not result.passed
    assert any("事件未确认" in error for error in result.errors)

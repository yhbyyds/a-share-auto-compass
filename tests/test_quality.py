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
        {
            "date": item,
            "up_probability": 50 + index,
            "direction": "震荡",
            "expected_return": 0.1,
            "confidence": "低",
        }
        for index, item in enumerate(dates)
    ]
    sectors = [
        {
            "key": f"sector_{index}",
            "name": f"行业{index}",
            "days": deepcopy(days),
            "history": [
                {
                    "date": "2026-07-24",
                    "sector": 100.0,
                    "benchmark": 100.0,
                    "relative": 100.0,
                }
                for _ in range(60)
            ],
        }
        for index in range(10)
    ]
    return {
        "meta": {
            "data_through": "2026-07-24",
            "generated_at": "2026-07-26T08:00:00+08:00",
            "trading_calendar": {
                "name": "XSHG",
                "url": "https://www.sse.com.cn/",
                "verified_through": "2026-12-31",
            },
        },
        "market": {
            "weekly_up_probability": 52,
            "weekly_direction": "震荡",
            "last_close": 3500.0,
            "breadth_guard": "degraded",
            "validation_guard": "healthy",
        },
        "days": days,
        "recent_chart": [
            {"date": "2026-07-24", "close": 3500.0},
        ],
        "validation": {
            "samples": 500,
            "daily_direction_accuracy": 54,
            "weekly_direction_accuracy": 53,
            "baseline_accuracy": 52,
            "brier": 0.24,
            "calibration": "扩展窗口样本外预测 + 时间顺序Sigmoid校准",
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
        "event_radar": {
            "daily_risk": [],
            "collection": {"status": "manual"},
        },
        "performance_monitor": {
            "status": "collecting",
            "evaluated_samples": 0,
            "degraded": False,
            "effective_sample": "第1日预测的唯一目标交易日",
        },
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


def test_expired_forecast_window_blocks_publication() -> None:
    result = validate_forecast(
        valid_forecast(),
        today=date(2026, 7, 27),
    )

    assert not result.passed
    assert any("预测窗口已过期" in error for error in result.errors)


def test_calendar_verification_range_blocks_publication() -> None:
    forecast = valid_forecast()
    forecast["meta"]["trading_calendar"]["verified_through"] = "2026-07-30"

    result = validate_forecast(forecast, today=date(2026, 7, 26))

    assert not result.passed
    assert any("超过交易日历官方核验范围" in error for error in result.errors)


def test_missing_breadth_requires_confidence_degradation() -> None:
    forecast = valid_forecast()
    forecast["market"]["breadth_guard"] = "healthy"
    forecast["days"][0]["confidence"] = "中"

    result = validate_forecast(forecast, today=date(2026, 7, 26))

    assert not result.passed
    assert any("市场宽度不足" in error for error in result.errors)


def test_missing_sector_history_blocks_publication() -> None:
    forecast = valid_forecast()
    forecast["sector_forecast"]["sectors"][0]["history"] = []

    result = validate_forecast(forecast, today=date(2026, 7, 26))

    assert not result.passed
    assert any("历史走势不足50个交易日" in error for error in result.errors)

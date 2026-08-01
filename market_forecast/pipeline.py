from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from market_forecast.data import fetch_market_breadth, fetch_market_data
from market_forecast.events import enrich_forecast_with_events
from market_forecast.intraday import (
    build_intraday_brief,
    fetch_theme_close_changes,
    settle_intraday_labels,
)
from market_forecast.model import generate_forecast
from market_forecast.official_events import fetch_official_events
from market_forecast.sectors import (
    apply_live_sector_close_overlay,
    fetch_sector_data,
    generate_sector_forecast,
)
from market_forecast.trading_calendar import (
    calendar_metadata,
    is_trading_session,
    prepare_calendar_updates,
)
from market_forecast.watchlist import generate_watchlist


RELEASE = "17"
DATA_VERSION = "1.13.0"


def _sector_actual_through(forecast: dict[str, Any]) -> date | None:
    dates: list[date] = []
    for sector in forecast.get("sector_forecast", {}).get("sectors", []):
        if sector.get("is_composite") or not sector.get("history"):
            continue
        try:
            dates.append(date.fromisoformat(str(sector["history"][-1]["date"])))
        except (KeyError, TypeError, ValueError):
            continue
    return min(dates) if dates else None


def _trading_session_lag(start: date, end: date) -> int:
    lag = 0
    current = start + timedelta(days=1)
    while current <= end:
        if is_trading_session(current):
            lag += 1
        current += timedelta(days=1)
    return lag


def _apply_sector_freshness_guard(
    forecast: dict[str, Any],
    live_overlay: dict[str, Any] | None = None,
) -> None:
    sector_block = forecast.get("sector_forecast") or {}
    market_date = date.fromisoformat(forecast["meta"]["data_through"])
    sector_date = _sector_actual_through(forecast)
    if sector_date is None:
        sector_block["freshness"] = {
            "status": "missing",
            "actual_data_through": None,
            "market_data_through": market_date.isoformat(),
            "lag_trading_sessions": None,
            "message": "\u884c\u4e1a\u6536\u76d8\u5e8f\u5217\u7b49\u5f85\u540c\u6b65\uff0c\u5f53\u65e5\u677f\u5757\u5019\u9009\u6682\u505c\u3002",
        }
        return

    lag = _trading_session_lag(sector_date, market_date)
    overlay = live_overlay or {}
    is_provisional = lag == 0 and overlay.get("status") == "provisional"
    status = "provisional" if is_provisional else "fresh" if lag == 0 else "stale"
    provisional_message = (
        "\u5f53\u65e5\u677f\u5757\u5df2\u4f7f\u7528\u884c\u4e1a ETF \u6536\u76d8\u4ee3\u7406\u503c\uff0c\u7533\u4e07\u65e5\u7ebf\u6b63\u5728\u540e\u7eed\u590d\u6838\u3002"
        if "ETF" in str(overlay.get("source", ""))
        else "\u5f53\u65e5\u677f\u5757\u5df2\u4f7f\u7528\u5b9e\u65f6\u884c\u4e1a\u7bee\u5b50\u6536\u76d8\u5feb\u7167\uff0c\u7533\u4e07\u65e5\u7ebf\u6b63\u5728\u540e\u7eed\u590d\u6838\u3002"
    )
    message = (
        provisional_message
        if is_provisional
        else "\u884c\u4e1a\u884c\u60c5\u5df2\u66f4\u65b0\u81f3 " + sector_date.isoformat()
        if lag == 0
        else "\u884c\u4e1a\u884c\u60c5\u6682\u81f3 " + sector_date.isoformat()
        + "\uff0c\u6bd4\u5927\u76d8\u6536\u76d8\u665a " + str(lag)
        + " \u4e2a\u4ea4\u6613\u65e5\uff1b\u5f53\u65e5\u677f\u5757\u5019\u9009\u6682\u505c\u3002"
    )
    freshness = {
        "status": status,
        "actual_data_through": sector_date.isoformat(),
        "market_data_through": market_date.isoformat(),
        "lag_trading_sessions": lag,
        "message": message,
        "live_overlay": overlay,
    }
    sector_block["actual_data_through"] = sector_date.isoformat()
    sector_block["freshness"] = freshness
    if status == "stale":
        selection = sector_block.get("tomorrow_selection") or {}
        selection["up"] = []
        selection["down"] = []
        selection["status"] = "stale"
        selection["method"] = freshness["message"]
        sector_block["tomorrow_selection"] = selection


def build_forecast() -> dict[str, Any]:
    """Fetch current data, retrain all models, and assemble one forecast."""
    calendar_warnings = prepare_calendar_updates()
    data = fetch_market_data()
    close_by_date = data["sse"]["close"].copy()
    close_by_date.index = close_by_date.index.strftime("%Y-%m-%d")
    settle_intraday_labels(close_by_date, fetch_theme_close_changes())
    forecast = generate_forecast(
        data,
        fetch_market_breadth(),
        generate_watchlist(data),
    )
    sector_data = fetch_sector_data()
    live_sector_overlay = apply_live_sector_close_overlay(
        sector_data,
        data["sse"].index.max().date(),
    )
    forecast["sector_forecast"] = generate_sector_forecast(
        data,
        sector_data,
        forecast["days"],
    )
    _apply_sector_freshness_guard(forecast, live_sector_overlay)
    # Intraday research is evidence-gated and remains in collection mode until
    # independently-labelled fixed-time snapshots are sufficient.
    forecast["intraday"] = build_intraday_brief(
        sector_forecast=forecast["sector_forecast"],
    )
    official_events = fetch_official_events(
        [day["date"] for day in forecast["days"]]
    )
    enrich_forecast_with_events(
        forecast,
        dynamic_events=official_events.events,
        collection=official_events.as_dict(),
    )
    # Make the primary product explicit: today's accepted close predicts the
    # next trading session, not the remainder of today's session.
    first_day = forecast["days"][0]
    forecast["day_ahead"] = {
        **first_day,
        "horizon": "next_trading_session",
        "label": "明日A股日间预测",
        "based_on_close_date": forecast["meta"]["data_through"],
    }
    forecast["meta"]["version"] = DATA_VERSION
    forecast["meta"]["release"] = RELEASE
    forecast["meta"]["model_research"] = {
        "open_source_references": [
            "Microsoft Qlib",
            "RQAlpha",
            "FinRL",
            "VeighNa vn.py",
        ],
        "strategy_priors": [
            "trend_breakout_and_relative_strength",
            "price_volume_confirmation",
            "risk_budget_and_transaction_costs",
        ],
        "note": (
            "开源框架用于数据、回测和风控工程参考；公开交易者原则只转成"
            "特征与风险规则，不把个人观点当作训练标签。"
        ),
    }
    forecast["validation"]["feature_families"] = [
        "趋势与突破：20/60日突破、均线位置、趋势效率",
        "动量与相对强弱：多周期收益、相对沪深300强弱",
        "量价确认：成交量标准分、ATR波动率、量价协同",
        "市场状态：宽度、离散度、大小盘风格与全球风险偏好",
    ]
    forecast["meta"]["trading_calendar"] = calendar_metadata()
    forecast["meta"]["trading_calendar"]["update_warnings"] = calendar_warnings
    forecast["sources"].extend(
        [
            {
                "name": "申万行业指数",
                "detail": "申万一级行业历史行情与行业分类口径",
                "url": (
                    "https://www.swsresearch.com/institute_sw/allIndex/"
                    "releasedIndex"
                ),
            },
            {
                "name": "官方短线事件日历",
                "detail": "自动采集美联储与BEA官方日程，并与人工核验事件合并",
                "url": (
                    "https://www.federalreserve.gov/monetarypolicy/"
                    "fomccalendars.htm"
                ),
            },
            {
                "name": "上海证券交易所休市安排",
                "detail": (
                    "按上交所年度休市公告维护的XSHG日历；"
                    "未知年份采用拒绝发布策略"
                ),
                "url": (
                    "https://www.sse.com.cn/disclosure/dealinstruc/closed/"
                ),
            },
            {
                "name": "实盘预测追踪",
                "detail": "逐次保存预测、到期后核对实际涨跌并触发失效降级",
                "url": (
                    "https://github.com/yhbyyds/a-share-auto-compass/"
                    "actions"
                ),
            },
            {
                "name": "自动更新质量门禁",
                "detail": "数据时效、结构完整性、验证样本与异常值检查",
                "url": "https://yhbyyds.github.io/a-share-auto-compass/",
            },
        ]
    )
    return forecast

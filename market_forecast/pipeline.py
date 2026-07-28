from __future__ import annotations

from typing import Any

from market_forecast.data import fetch_market_breadth, fetch_market_data
from market_forecast.events import enrich_forecast_with_events
from market_forecast.intraday import build_intraday_brief, settle_intraday_labels
from market_forecast.model import generate_forecast
from market_forecast.official_events import fetch_official_events
from market_forecast.sectors import fetch_sector_data, generate_sector_forecast
from market_forecast.trading_calendar import (
    calendar_metadata,
    prepare_calendar_updates,
)
from market_forecast.watchlist import generate_watchlist


RELEASE = "13"
DATA_VERSION = "1.9.0"


def build_forecast() -> dict[str, Any]:
    """Fetch current data, retrain all models, and assemble one forecast."""
    calendar_warnings = prepare_calendar_updates()
    data = fetch_market_data()
    close_by_date = data["sse"]["close"].copy()
    close_by_date.index = close_by_date.index.strftime("%Y-%m-%d")
    settle_intraday_labels(close_by_date)
    forecast = generate_forecast(
        data,
        fetch_market_breadth(),
        generate_watchlist(data),
    )
    forecast["sector_forecast"] = generate_sector_forecast(
        data,
        fetch_sector_data(),
        forecast["days"],
    )
    # Intraday research is evidence-gated and remains in collection mode until
    # independently-labelled fixed-time snapshots are sufficient.
    forecast["intraday"] = build_intraday_brief()
    official_events = fetch_official_events(
        [day["date"] for day in forecast["days"]]
    )
    enrich_forecast_with_events(
        forecast,
        dynamic_events=official_events.events,
        collection=official_events.as_dict(),
    )
    forecast["meta"]["version"] = DATA_VERSION
    forecast["meta"]["release"] = RELEASE
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

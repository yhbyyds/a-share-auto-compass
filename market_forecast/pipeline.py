from __future__ import annotations

from typing import Any

from market_forecast.data import fetch_market_breadth, fetch_market_data
from market_forecast.events import enrich_forecast_with_events
from market_forecast.model import generate_forecast
from market_forecast.sectors import fetch_sector_data, generate_sector_forecast
from market_forecast.watchlist import generate_watchlist


RELEASE = "7"
DATA_VERSION = "1.3.0"


def build_forecast() -> dict[str, Any]:
    """Fetch current data, retrain all models, and assemble one forecast."""
    data = fetch_market_data()
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
    enrich_forecast_with_events(forecast)
    forecast["meta"]["version"] = DATA_VERSION
    forecast["meta"]["release"] = RELEASE
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
                "name": "短线事件日历",
                "detail": "交易所、央行、统计机构及公司投资者关系官方日程",
                "url": (
                    "https://www.federalreserve.gov/monetarypolicy/"
                    "fomccalendars.htm"
                ),
            },
            {
                "name": "自动更新质量门禁",
                "detail": "数据时效、结构完整性、验证样本与异常值检查",
                "url": "https://a-share-event-compass-v6-202607.marialewisf383.chatgpt.site",
            },
        ]
    )
    return forecast

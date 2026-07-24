from __future__ import annotations

import argparse
import json
from pathlib import Path

from market_forecast.data import fetch_market_breadth, fetch_market_data
from market_forecast.events import enrich_forecast_with_events
from market_forecast.model import generate_forecast
from market_forecast.sectors import fetch_sector_data, generate_sector_forecast
from market_forecast.watchlist import generate_watchlist


def main() -> None:
    parser = argparse.ArgumentParser(description="生成A股下周方向预测")
    parser.add_argument(
        "--output", default="public/data/forecast.json", help="预测JSON输出路径"
    )
    args = parser.parse_args()
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

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
    forecast["meta"]["version"] = "1.2.0"
    forecast["meta"]["release"] = "6"
    forecast["sources"].append(
        {
            "name": "申万行业指数",
            "detail": "申万一级行业历史行情与行业分类口径",
            "url": "https://www.swsresearch.com/institute_sw/allIndex/releasedIndex",
        }
    )
    forecast["sources"].append(
        {
            "name": "短线事件日历",
            "detail": "交易所、央行、统计机构及公司投资者关系官方日程",
            "url": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
        }
    )
    output_path.write_text(
        json.dumps(forecast, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    market = forecast["market"]
    print(
        f"{forecast['meta']['forecast_window']} | "
        f"{market['weekly_direction']} | "
        f"上涨概率 {market['weekly_up_probability']}% | "
        f"预期 {market['weekly_expected_return']:+.2f}%"
    )


if __name__ == "__main__":
    main()

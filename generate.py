from __future__ import annotations

import argparse
import json
from pathlib import Path

from market_forecast.data import fetch_market_breadth, fetch_market_data
from market_forecast.model import generate_forecast
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

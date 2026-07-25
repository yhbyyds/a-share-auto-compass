from __future__ import annotations

import argparse
import json
from pathlib import Path

from market_forecast.pipeline import build_forecast
from market_forecast.quality import validate_forecast


def main() -> None:
    parser = argparse.ArgumentParser(description="生成A股下周方向预测")
    parser.add_argument(
        "--output", default="public/data/forecast.json", help="预测JSON输出路径"
    )
    args = parser.parse_args()
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    forecast = build_forecast()
    quality = validate_forecast(forecast)
    if not quality.passed:
        raise SystemExit("质量门禁未通过: " + "；".join(quality.errors))
    forecast["meta"]["automation"] = {
        "status": "manual",
        "quality_gate": quality.as_dict(),
    }
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

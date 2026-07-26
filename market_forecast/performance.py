from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np


HISTORY_VERSION = 2
MINIMUM_LIVE_DAYS = 60


def _metrics(rows: list[dict[str, Any]], window: int = 60) -> dict[str, Any]:
    recent = sorted(rows, key=lambda row: row["target_date"])[-window:]
    if not recent:
        return {
            "samples": 0,
            "window": 0,
            "accuracy": None,
            "baseline": None,
            "edge_pp": None,
            "brier": None,
            "last_date": None,
        }
    actual_up = np.array([bool(row["actual_up"]) for row in recent])
    correct = np.array([bool(row["correct"]) for row in recent])
    probabilities = np.array(
        [float(row["up_probability"]) / 100 for row in recent]
    )
    accuracy = float(correct.mean())
    up_rate = float(actual_up.mean())
    baseline = max(up_rate, 1 - up_rate)
    brier = float(np.mean((probabilities - actual_up.astype(float)) ** 2))
    return {
        "samples": len(rows),
        "window": len(recent),
        "accuracy": round(accuracy * 100, 1),
        "baseline": round(baseline * 100, 1),
        "edge_pp": round((accuracy - baseline) * 100, 1),
        "brier": round(brier, 3),
        "last_date": recent[-1]["target_date"],
    }


def _monitor(records: list[dict[str, Any]]) -> dict[str, Any]:
    evaluated_all = [
        row for row in records if row.get("status") == "evaluated"
    ]
    horizon_metrics = {
        str(horizon): _metrics(
            [row for row in evaluated_all if int(row.get("horizon", 0)) == horizon]
        )
        for horizon in range(1, 6)
    }
    primary = horizon_metrics["1"]
    pending = sum(
        row.get("status") == "pending" and int(row.get("horizon", 0)) == 1
        for row in records
    )
    if not primary["samples"]:
        return {
            "status": "collecting",
            "label": "实盘样本积累中",
            "evaluated_samples": 0,
            "pending_samples": pending,
            "recent_window": 0,
            "accuracy": None,
            "baseline": None,
            "edge_pp": None,
            "brier": None,
            "last_evaluated_date": None,
            "degraded": False,
            "effective_sample": "第1日预测的唯一目标交易日",
            "all_evaluated_predictions": len(evaluated_all),
            "horizon_metrics": horizon_metrics,
            "reason": "版本9按第1日唯一目标日统计，至少积累60日后判断有效性。",
        }

    edge = float(primary["edge_pp"]) / 100
    brier = float(primary["brier"])
    if primary["samples"] < MINIMUM_LIVE_DAYS:
        status = "collecting"
        label = "实盘样本积累中"
        reason = (
            f"第1日预测已实现 {primary['samples']} 个唯一目标日，"
            "达到60日后启用有效性判定。"
        )
    elif edge < 0 or brier > 0.26:
        status = "degraded"
        label = "模型近期失效"
        reason = (
            f"近60个第1日目标命中率相对基线 {edge * 100:+.1f}pp，"
            f"Brier {brier:.3f}；当前信号自动降为低置信。"
        )
    elif edge >= 0.03 and brier < 0.25:
        status = "healthy"
        label = "模型近期有效"
        reason = (
            f"近60个第1日目标命中率相对基线 {edge * 100:+.1f}pp，"
            f"Brier {brier:.3f}。"
        )
    else:
        status = "watch"
        label = "模型优势偏弱"
        reason = (
            f"近60个第1日目标命中率相对基线 {edge * 100:+.1f}pp，"
            "仅保留低至中等置信。"
        )
    return {
        "status": status,
        "label": label,
        "evaluated_samples": primary["samples"],
        "pending_samples": pending,
        "recent_window": primary["window"],
        "accuracy": primary["accuracy"],
        "baseline": primary["baseline"],
        "edge_pp": primary["edge_pp"],
        "brier": primary["brier"],
        "last_evaluated_date": primary["last_date"],
        "degraded": status == "degraded",
        "effective_sample": "第1日预测的唯一目标交易日",
        "all_evaluated_predictions": len(evaluated_all),
        "horizon_metrics": horizon_metrics,
        "reason": reason,
    }


def update_performance_history(
    history: dict[str, Any] | None,
    forecast: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = deepcopy(history or {})
    records = list(payload.get("predictions", []))
    actual_closes = {
        item["date"]: float(item["close"])
        for item in forecast.get("recent_chart", [])
        if len(str(item.get("date", ""))) == 10
    }

    for row in records:
        if row.get("status") != "pending":
            continue
        target_close = actual_closes.get(row["target_date"])
        if target_close is None:
            continue
        actual_return = target_close / float(row["base_close"]) - 1
        actual_up = actual_return > 0
        predicted_up = float(row["up_probability"]) >= 50
        row.update(
            {
                "status": "evaluated",
                "actual_close": round(target_close, 2),
                "actual_return": round(actual_return * 100, 3),
                "actual_up": actual_up,
                "correct": predicted_up == actual_up,
                "evaluated_at": forecast["meta"]["generated_at"],
            }
        )

    existing_ids = {row["id"] for row in records}
    base_date = forecast["meta"]["data_through"]
    base_close = float(forecast["market"]["last_close"])
    for horizon, day in enumerate(forecast["days"], start=1):
        record_id = f"{base_date}:{day['date']}:h{horizon}"
        if record_id in existing_ids:
            continue
        records.append(
            {
                "id": record_id,
                "generated_at": forecast["meta"]["generated_at"],
                "base_date": base_date,
                "base_close": base_close,
                "target_date": day["date"],
                "horizon": horizon,
                "direction": day["direction"],
                "up_probability": float(day["up_probability"]),
                "expected_return": float(day["expected_return"]),
                "status": "pending",
            }
        )

    records = sorted(
        records,
        key=lambda row: (row["base_date"], row["target_date"], row["horizon"]),
    )[-1500:]
    monitor = _monitor(records)
    payload.update(
        {
            "version": HISTORY_VERSION,
            "updated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
            "predictions": records,
            "monitor": monitor,
        }
    )
    forecast["performance_monitor"] = monitor

    if monitor["degraded"]:
        forecast["market"]["weekly_direction"] = "震荡"
        forecast["market"]["model_health_guard"] = "degraded"
        for day in forecast["days"]:
            day["direction"] = "震荡"
            day["confidence"] = "低"
        for sector in (forecast.get("sector_forecast") or {}).get(
            "sectors", []
        ):
            for day in sector.get("days", []):
                if day.get("confidence") != "事件":
                    day["confidence"] = "低"
    else:
        forecast["market"]["model_health_guard"] = monitor["status"]
    return payload, monitor

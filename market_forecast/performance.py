from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np


HISTORY_VERSION = 1


def _monitor(records: list[dict[str, Any]]) -> dict[str, Any]:
    evaluated = [row for row in records if row.get("status") == "evaluated"]
    pending = sum(row.get("status") == "pending" for row in records)
    recent = sorted(evaluated, key=lambda row: row["target_date"])[-20:]
    if not recent:
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
            "reason": "版本8开始记录逐日预测，至少积累20个已实现样本后判断失效。",
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
    edge = accuracy - baseline

    if len(recent) < 20:
        status = "collecting"
        label = "实盘样本积累中"
        reason = f"已实现 {len(recent)} 个样本，达到20个后启用自动降级。"
    elif edge < 0 or brier > 0.27:
        status = "degraded"
        label = "模型近期失效"
        reason = (
            f"近20次命中率相对多数类基线 {edge * 100:+.1f}pp，"
            f"Brier {brier:.3f}；当前信号自动降为低置信。"
        )
    elif edge >= 0.02 and brier <= 0.25:
        status = "healthy"
        label = "模型近期有效"
        reason = (
            f"近20次命中率相对多数类基线 {edge * 100:+.1f}pp，"
            f"Brier {brier:.3f}。"
        )
    else:
        status = "watch"
        label = "模型优势偏弱"
        reason = (
            f"近20次命中率相对多数类基线 {edge * 100:+.1f}pp，"
            "仅保留低至中等置信。"
        )
    return {
        "status": status,
        "label": label,
        "evaluated_samples": len(evaluated),
        "pending_samples": pending,
        "recent_window": len(recent),
        "accuracy": round(accuracy * 100, 1),
        "baseline": round(baseline * 100, 1),
        "edge_pp": round(edge * 100, 1),
        "brier": round(brier, 3),
        "last_evaluated_date": recent[-1]["target_date"],
        "degraded": status == "degraded",
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

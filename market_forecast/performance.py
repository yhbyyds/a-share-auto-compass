from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np


HISTORY_VERSION = 3
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


def _sector_levels(forecast: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Extract normalized sector index levels keyed by close date."""
    output: dict[str, dict[str, float]] = {}
    for sector in (forecast.get("sector_forecast") or {}).get("sectors", []):
        if sector.get("is_composite"):
            continue
        levels: dict[str, float] = {}
        for item in sector.get("history", []):
            try:
                levels[str(item["date"])] = float(item["sector"])
            except (KeyError, TypeError, ValueError):
                continue
        output[str(sector.get("key", ""))] = levels
    return output


def _sector_monitor(records: list[dict[str, Any]]) -> dict[str, Any]:
    evaluated = [row for row in records if row.get("status") == "evaluated"]
    metrics = _metrics(evaluated, window=120)
    high_priority = [
        row for row in evaluated if float(row.get("priority_score") or 0) >= 75
    ]
    priority_metrics = _metrics(high_priority, window=120)
    return {
        "evaluated_samples": len(evaluated),
        "evaluated_days": len({str(row.get("target_date", "")) for row in evaluated}),
        "pending_samples": sum(row.get("status") == "pending" for row in records),
        "accuracy": metrics["accuracy"],
        "baseline": metrics["baseline"],
        "edge_pp": metrics["edge_pp"],
        "brier": metrics["brier"],
        "last_evaluated_date": metrics["last_date"],
        "high_priority_accuracy": priority_metrics["accuracy"],
        "high_priority_samples": len(high_priority),
        "status": "ready" if evaluated else "collecting",
    }


def _review_rows(records: list[dict[str, Any]], limit: int = 18) -> list[dict[str, Any]]:
    fields = (
        "base_date", "target_date", "horizon", "sector_key", "sector_name",
        "direction", "up_probability", "expected_return", "priority_score",
        "status", "actual_return", "actual_up", "correct",
    )
    return [
        {key: row.get(key) for key in fields}
        for row in sorted(
            records,
            key=lambda row: (str(row.get("target_date", "")), str(row.get("id", ""))),
            reverse=True,
        )[:limit]
    ]


def update_performance_history(
    history: dict[str, Any] | None,
    forecast: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = deepcopy(history or {})
    records = list(payload.get("predictions", []))
    sector_records = list(payload.get("sector_predictions", []))
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

    actual_sector_levels = _sector_levels(forecast)
    for row in sector_records:
        if row.get("status") != "pending":
            continue
        target_level = actual_sector_levels.get(
            str(row.get("sector_key", "")), {}
        ).get(str(row.get("target_date", "")))
        if target_level is None:
            continue
        actual_return = target_level / float(row["base_level"]) - 1
        actual_up = actual_return > 0
        predicted_up = float(row["up_probability"]) >= 50
        row.update(
            {
                "status": "evaluated",
                "actual_level": round(target_level, 3),
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

    sector_existing_ids = {row["id"] for row in sector_records}
    selection = (forecast.get("sector_forecast") or {}).get(
        "tomorrow_selection", {}
    )
    priority_by_key = {
        str(row.get("key")): row.get("priority_score")
        for row in [*(selection.get("up") or []), *(selection.get("down") or [])]
    }
    for sector in (forecast.get("sector_forecast") or {}).get("sectors", []):
        if sector.get("is_composite") or not sector.get("days"):
            continue
        history_rows = sector.get("history") or []
        if not history_rows:
            continue
        day = sector["days"][0]
        try:
            base_level = float(history_rows[-1]["sector"])
            record_id = (
                f"{base_date}:{day['date']}:sector:{sector['key']}:h1"
            )
        except (KeyError, TypeError, ValueError):
            continue
        if record_id in sector_existing_ids:
            continue
        sector_records.append(
            {
                "id": record_id,
                "generated_at": forecast["meta"]["generated_at"],
                "base_date": base_date,
                "base_level": base_level,
                "target_date": day["date"],
                "horizon": 1,
                "sector_key": sector["key"],
                "sector_name": sector.get("name", sector["key"]),
                "direction": day.get("direction", "震荡"),
                "up_probability": float(day.get("up_probability", 50)),
                "expected_return": float(day.get("expected_return", 0)),
                "priority_score": priority_by_key.get(sector["key"]),
                "status": "pending",
            }
        )

    records = sorted(
        records,
        key=lambda row: (row["base_date"], row["target_date"], row["horizon"]),
    )[-1500:]
    sector_records = sorted(
        sector_records,
        key=lambda row: (
            str(row.get("base_date", "")),
            str(row.get("target_date", "")),
            str(row.get("sector_key", "")),
        ),
    )[-3000:]
    monitor = _monitor(records)
    sector_monitor = _sector_monitor(sector_records)
    payload.update(
        {
            "version": HISTORY_VERSION,
            "updated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
            "predictions": records,
            "sector_predictions": sector_records,
            "monitor": monitor,
            "sector_monitor": sector_monitor,
        }
    )
    forecast["performance_monitor"] = monitor
    forecast["performance_review"] = {
        "market": {"monitor": monitor, "rows": _review_rows(records)},
        "sectors": {
            "monitor": sector_monitor,
            "rows": _review_rows(sector_records, limit=36),
        },
    }

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

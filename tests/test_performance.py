from copy import deepcopy

from market_forecast.performance import update_performance_history
from tests.test_quality import valid_forecast


def test_pending_prediction_is_evaluated_when_close_arrives() -> None:
    forecast = valid_forecast()
    forecast["meta"]["data_through"] = "2026-07-27"
    forecast["sector_forecast"]["data_through"] = "2026-07-27"
    forecast["recent_chart"].append(
        {"date": "2026-07-27", "close": 3535.0}
    )
    history = {
        "predictions": [
            {
                "id": "old",
                "base_date": "2026-07-24",
                "base_close": 3500.0,
                "target_date": "2026-07-27",
                "horizon": 1,
                "direction": "偏强",
                "up_probability": 60.0,
                "expected_return": 0.5,
                "status": "pending",
            }
        ]
    }

    updated, monitor = update_performance_history(history, forecast)

    evaluated = next(row for row in updated["predictions"] if row["id"] == "old")
    assert evaluated["status"] == "evaluated"
    assert evaluated["correct"] is True
    assert monitor["evaluated_samples"] == 1


def test_degraded_live_record_forces_low_confidence() -> None:
    forecast = valid_forecast()
    history = {"predictions": []}
    for index in range(60):
        history["predictions"].append(
            {
                "id": f"bad-{index}",
                "base_date": f"2026-05-{index + 1:02d}",
                "target_date": f"2026-06-{index + 1:02d}",
                "horizon": 1,
                "status": "evaluated",
                "actual_up": True,
                "correct": False,
                "up_probability": 20.0,
            }
        )

    _, monitor = update_performance_history(deepcopy(history), forecast)

    assert monitor["status"] == "degraded"
    assert forecast["market"]["weekly_direction"] == "震荡"
    assert all(day["confidence"] == "低" for day in forecast["days"])


def test_monitor_does_not_pool_other_horizons_into_effective_sample() -> None:
    forecast = valid_forecast()
    history = {"predictions": []}
    for horizon in range(1, 6):
        history["predictions"].append(
            {
                "id": f"same-day-{horizon}",
                "base_date": "2026-07-20",
                "target_date": "2026-07-27",
                "horizon": horizon,
                "status": "evaluated",
                "actual_up": True,
                "correct": True,
                "up_probability": 60.0,
            }
        )

    _, monitor = update_performance_history(history, forecast)

    assert monitor["evaluated_samples"] == 1
    assert monitor["all_evaluated_predictions"] == 5
    assert monitor["effective_sample"] == "第1日预测的唯一目标交易日"


def test_small_live_sample_exposes_wide_accuracy_interval() -> None:
    forecast = valid_forecast()
    history = {
        "predictions": [
            {
                "id": f"small-{index}",
                "base_date": "2026-07-20",
                "target_date": f"2026-07-{21 + index:02d}",
                "horizon": 1,
                "status": "evaluated",
                "actual_up": True,
                "correct": True,
                "up_probability": 55.0,
            }
            for index in range(3)
        ]
    }

    _, monitor = update_performance_history(history, forecast)

    assert monitor["status"] == "collecting"
    assert monitor["reliability_ready"] is False
    assert monitor["minimum_reliability_samples"] == 60
    assert monitor["accuracy"] == 100.0
    assert monitor["accuracy_ci_low"] < 50
    assert monitor["accuracy_ci_high"] == 100.0


def test_sector_prediction_is_settled_and_exposed_in_review() -> None:
    forecast = valid_forecast()
    sector = forecast["sector_forecast"]["sectors"][0]
    sector["history"] = [
        {"date": "2026-07-24", "sector": 100.0, "benchmark": 100.0, "relative": 100.0},
        {"date": "2026-07-27", "sector": 101.5, "benchmark": 100.5, "relative": 101.0},
    ]
    history = {
        "predictions": [],
        "sector_predictions": [
            {
                "id": "sector-old",
                "base_date": "2026-07-24",
                "base_level": 100.0,
                "base_benchmark_level": 100.0,
                "target_date": "2026-07-27",
                "horizon": 1,
                "sector_key": sector["key"],
                "sector_name": sector["name"],
                "direction": "偏强",
                "up_probability": 58.0,
                "priority_score": 92.0,
                "selection_side": "up",
                "relative_probability": 58.0,
                "status": "pending",
            }
        ],
    }

    updated, _ = update_performance_history(history, forecast)

    settled = next(row for row in updated["sector_predictions"] if row["id"] == "sector-old")
    assert settled["status"] == "evaluated"
    assert settled["actual_return"] == 1.5
    assert settled["actual_excess_return"] == 1.0
    assert settled["relative_correct"] is True
    review = forecast["performance_review"]["sectors"]
    assert review["monitor"]["evaluated_samples"] == 1
    assert review["monitor"]["evaluated_days"] == 1
    assert review["monitor"]["selection_evaluated_samples"] == 1
    assert review["monitor"]["selection_accuracy"] == 100.0
    assert review["rows"][0]["sector_name"] == sector["name"]


def test_stale_sector_snapshot_does_not_create_new_sector_call() -> None:
    forecast = valid_forecast()
    forecast["sector_forecast"]["freshness"] = {"status": "stale"}

    updated, _ = update_performance_history({"predictions": []}, forecast)

    assert updated["sector_predictions"] == []

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
    for index in range(20):
        history["predictions"].append(
            {
                "id": f"bad-{index}",
                "base_date": f"2026-06-{index + 1:02d}",
                "target_date": f"2026-07-{index + 1:02d}",
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

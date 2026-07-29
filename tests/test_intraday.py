import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from market_forecast.intraday import (
    _bucket,
    _market_regime,
    _transfer_predictions,
    collection_window,
    collect_intraday_snapshot,
    micro_theme_training_status,
    settle_intraday_labels,
)


def test_settlement_uses_snapshot_price_and_close(tmp_path):
    path = tmp_path / "snapshots.json"
    path.write_text(json.dumps({"snapshots": [{
        "date": "2026-07-27",
        "quotes": {"sse": {"price": 100.0}},
        "themes": [{"key": "test_theme", "board": "测试板块", "change": 1.0}],
        "label": None,
    }]}), encoding="utf-8")
    changed = settle_intraday_labels(
        pd.Series([101.0], index=["2026-07-27"]),
        {"测试板块": 2.0},
        path,
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert changed == 1
    assert payload["snapshots"][0]["label"] == {
        "remaining_return": 1.0,
        "up": True,
        "themes": {
            "test_theme": {"remaining_change": 1.0, "up": True},
        },
    }


def test_snapshot_replaces_same_fixed_bucket(tmp_path, monkeypatch):
    path = tmp_path / "snapshots.json"
    monkeypatch.setattr("market_forecast.intraday._quote_snapshot", lambda: {"sse": {"price": 1, "change": 0}})
    monkeypatch.setattr("market_forecast.intraday.fetch_market_breadth", lambda: {"stocks": 5000})
    monkeypatch.setattr("market_forecast.intraday._concept_rows", lambda: [])
    now = datetime(2026, 7, 27, 9, 36, tzinfo=ZoneInfo("Asia/Shanghai"))
    collect_intraday_snapshot(path, now)
    collect_intraday_snapshot(path, now)
    assert len(json.loads(path.read_text(encoding="utf-8"))["snapshots"]) == 1


def test_bucket_never_assigns_a_future_collection_time():
    now = datetime(2026, 7, 27, 11, 20, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert _bucket(now) == "11:00"


def test_collection_window_only_accepts_short_fixed_time_periods():
    timezone = ZoneInfo("Asia/Shanghai")
    assert collection_window(datetime(2026, 7, 27, 9, 36, tzinfo=timezone)) == "09:35"
    assert collection_window(datetime(2026, 7, 27, 9, 57, tzinfo=timezone)) is None
    assert collection_window(datetime(2026, 7, 27, 16, 5, tzinfo=timezone)) is None


def test_snapshot_rejects_delayed_after_close_capture(tmp_path):
    with pytest.raises(ValueError, match="fixed intraday collection window"):
        collect_intraday_snapshot(
            tmp_path / "snapshots.json",
            datetime(2026, 7, 27, 16, 5, tzinfo=ZoneInfo("Asia/Shanghai")),
        )


def test_training_status_accepts_unsettled_none_labels(tmp_path):
    path = tmp_path / "snapshots.json"
    path.write_text(
        json.dumps(
            {
                "snapshots": [
                    {
                        "date": "2026-07-28",
                        "themes": [],
                        "label": None,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = micro_theme_training_status(path)

    assert result
    assert all(item["status"] == "collecting" for item in result.values())


def test_transfer_prediction_is_capped_and_uses_parent_prior():
    themes = [{"key": "robotics", "name": "机器人", "parent": "computer", "change": 8.0}]
    sectors = {"sectors": [{"key": "computer", "days": [{
        "up_probability": 60.0, "expected_return": 0.4, "signal_band": "中",
    }]}]}
    result = _transfer_predictions(themes, sectors)[0]
    assert result["provisional_direction"] == "明日临时偏强"
    assert result["provisional_score"] <= 100
    assert result["prediction_stage"].startswith("一级行业次日先验")
    assert result["selection_bucket"] == "候选偏强"
    assert "迁移分" in result["selection_reason"]


def test_risk_off_regime_blocks_long_candidate_and_keeps_resilience():
    regime = _market_regime({
        "quotes": {
            "csi300": {"change": -2.0},
            "chinext": {"change": -4.0},
        },
        "breadth": {"advance_ratio": 42.0, "median_change": -0.2},
    })
    assert regime["key"] == "risk_off"
    result = _transfer_predictions(
        [{"key": "robotics", "name": "机器人", "parent": "computer", "change": -0.2}],
        {"sectors": [{"key": "computer", "days": [{
            "up_probability": 60.0, "expected_return": 0.4, "signal_band": "强",
        }]}]},
        regime,
    )[0]
    assert result["selection_bucket"] == "抗跌观察"
    assert result["raw_up_candidate"] is True

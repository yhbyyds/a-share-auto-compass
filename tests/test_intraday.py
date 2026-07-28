import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from market_forecast.intraday import _bucket, collect_intraday_snapshot, settle_intraday_labels


def test_settlement_uses_snapshot_price_and_close(tmp_path):
    path = tmp_path / "snapshots.json"
    path.write_text(json.dumps({"snapshots": [{
        "date": "2026-07-27", "quotes": {"sse": {"price": 100.0}}, "label": None,
    }]}), encoding="utf-8")
    changed = settle_intraday_labels(pd.Series([101.0], index=["2026-07-27"]), path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert changed == 1
    assert payload["snapshots"][0]["label"] == {"remaining_return": 1.0, "up": True}


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

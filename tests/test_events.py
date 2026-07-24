import copy
import json

from market_forecast.events import enrich_forecast_with_events


def test_event_overlay_keeps_probabilities_and_marks_risk(tmp_path):
    calendar = {
        "events": [
            {
                "id": "shock",
                "impact_date": "2026-07-27",
                "release_time": "开盘",
                "title": "测试事件",
                "category": "测试",
                "status": "已确认",
                "source_tier": "官方",
                "risk_score": 5,
                "risk": "极高",
                "direction": "双向",
                "market_wide": False,
                "affected_sectors": ["electronics"],
                "affected_labels": ["电子"],
                "mechanism": "测试",
                "bull_case": "测试",
                "bear_case": "测试",
                "confirmation": "测试",
                "source_name": "测试",
                "url": "https://example.com",
            }
        ],
        "unscheduled_watch": [],
    }
    path = tmp_path / "events.json"
    path.write_text(json.dumps(calendar, ensure_ascii=False), encoding="utf-8")
    forecast = {
        "days": [
            {
                "date": "2026-07-27",
                "weekday": "一",
                "up_probability": 58.0,
            }
        ],
        "sector_forecast": {
            "sectors": [
                {
                    "key": "electronics",
                    "days": [
                        {
                            "date": "2026-07-27",
                            "confidence": "中",
                            "up_probability": 59.0,
                        }
                    ],
                }
            ]
        },
        "playbook": {},
    }
    original = copy.deepcopy(forecast)

    enriched = enrich_forecast_with_events(forecast, path)

    assert enriched["days"][0]["up_probability"] == original["days"][0][
        "up_probability"
    ]
    assert enriched["days"][0]["event_risk"] == "极高"
    sector_day = enriched["sector_forecast"]["sectors"][0]["days"][0]
    assert sector_day["up_probability"] == 59.0
    assert sector_day["confidence"] == "事件"


def test_clustered_events_raise_daily_risk(tmp_path):
    events = []
    for index in range(2):
        events.append(
            {
                "id": f"event-{index}",
                "impact_date": "2026-07-30",
                "release_time": "开盘前",
                "title": f"事件{index}",
                "category": "测试",
                "status": "已确认",
                "source_tier": "官方",
                "risk_score": 4,
                "risk": "高",
                "direction": "双向",
                "market_wide": True,
                "affected_sectors": [],
                "affected_labels": ["全市场"],
                "mechanism": "测试",
                "bull_case": "测试",
                "bear_case": "测试",
                "confirmation": "测试",
                "source_name": "测试",
                "url": "https://example.com",
            }
        )
    path = tmp_path / "events.json"
    path.write_text(
        json.dumps({"events": events, "unscheduled_watch": []}),
        encoding="utf-8",
    )
    forecast = {
        "days": [{"date": "2026-07-30", "weekday": "四"}],
        "sector_forecast": {"sectors": []},
        "playbook": {},
    }
    enriched = enrich_forecast_with_events(forecast, path)
    assert enriched["event_radar"]["daily_risk"][0]["risk"] == "极高"
    assert enriched["event_radar"]["daily_risk"][0]["count"] == 2

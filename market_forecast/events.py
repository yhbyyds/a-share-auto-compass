from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


DEFAULT_CALENDAR = Path(__file__).parents[1] / "data" / "event_calendar.json"
RISK_ORDER = {"低": 1, "中": 2, "高": 4, "极高": 5}


def _load_calendar(path: str | Path = DEFAULT_CALENDAR) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _cluster_risk(events: list[dict[str, Any]]) -> dict[str, Any]:
    if not events:
        return {"risk": "低", "score": 0, "count": 0}
    total = sum(int(event["risk_score"]) for event in events)
    maximum = max(int(event["risk_score"]) for event in events)
    if maximum >= 5 or total >= 8:
        risk = "极高"
    elif maximum >= 4 or total >= 6:
        risk = "高"
    elif total >= 3:
        risk = "中"
    else:
        risk = "低"
    return {"risk": risk, "score": total, "count": len(events)}


def enrich_forecast_with_events(
    forecast: dict[str, Any],
    calendar_path: str | Path = DEFAULT_CALENDAR,
) -> dict[str, Any]:
    calendar = _load_calendar(calendar_path)
    forecast_dates = {day["date"] for day in forecast["days"]}
    events = [
        event.copy()
        for event in calendar.get("events", [])
        if event["impact_date"] in forecast_dates
    ]
    events.sort(key=lambda event: (event["impact_date"], -event["risk_score"]))

    for event in events:
        timestamp = pd.Timestamp(event["impact_date"])
        event["date"] = timestamp.strftime("%m-%d")
        event["weekday"] = "一二三四五"[timestamp.weekday()]

    daily_risk: list[dict[str, Any]] = []
    for day in forecast["days"]:
        matches = [
            event for event in events if event["impact_date"] == day["date"]
        ]
        cluster = _cluster_risk(matches)
        day["event_risk"] = cluster["risk"]
        day["event_count"] = cluster["count"]
        day["event_titles"] = [event["title"] for event in matches]
        daily_risk.append(
            {
                "date": day["date"],
                "weekday": day["weekday"],
                **cluster,
                "titles": day["event_titles"],
            }
        )

    sector_forecast = forecast.get("sector_forecast") or {}
    for sector in sector_forecast.get("sectors", []):
        for day in sector["days"]:
            matches = [
                event
                for event in events
                if event["impact_date"] == day["date"]
                and (
                    event.get("market_wide", False)
                    or sector["key"] in event.get("affected_sectors", [])
                )
            ]
            cluster = _cluster_risk(matches)
            day["event_risk"] = cluster["risk"]
            day["event_count"] = cluster["count"]
            day["event_titles"] = [event["title"] for event in matches]
            if cluster["risk"] in {"高", "极高"} and day["confidence"] != "低":
                day["confidence"] = "事件"

    highest = max(
        daily_risk,
        key=lambda row: (RISK_ORDER[row["risk"]], row["score"]),
        default=None,
    )
    forecast["events"] = events
    forecast["event_radar"] = {
        "daily_risk": daily_risk,
        "events": events,
        "unscheduled_watch": calendar.get("unscheduled_watch", []),
        "highest_risk_day": highest,
        "method": (
            "事件层不直接修改量价模型概率；它按A股实际反应日聚合风险，"
            "标记受影响行业并把高冲击日改为事件置信，等待盘中条件确认。"
        ),
    }
    forecast["playbook"]["event"] = (
        "高/极高事件日不在集合竞价追单；至少等待开盘30分钟，并要求价格、"
        "成交额和行业相对强弱同向确认。"
    )
    return forecast

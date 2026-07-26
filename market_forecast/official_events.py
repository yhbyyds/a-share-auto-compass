from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from html import unescape
import json
import os
from pathlib import Path
import re
from typing import Any, Callable
from uuid import uuid4
from zoneinfo import ZoneInfo

import requests

from market_forecast.trading_calendar import (
    trading_session_after,
    trading_session_on_or_after,
)


ROOT = Path(__file__).parents[1]
DEFAULT_CACHE = ROOT / "data" / "cache" / "official_events.json"
FED_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
BEA_URL = "https://www.bea.gov/news/schedule"
USER_AGENT = "A-Share-Compass/8.0 (+official-calendar-research)"
MONTHS = {
    name: index
    for index, name in enumerate(
        (
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ),
        start=1,
    )
}


@dataclass
class EventCollection:
    events: list[dict[str, Any]] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    fetched_at: str = ""
    cache_used: bool = False

    def as_dict(self) -> dict[str, Any]:
        live = sum(source["status"] == "live" for source in self.sources)
        cached = sum(source["status"] == "cached" for source in self.sources)
        if live == len(self.sources) and self.sources:
            status = "live"
        elif live:
            status = "partial"
        elif cached:
            status = "cached"
        else:
            status = "failed"
        return {
            "status": status,
            "fetched_at": self.fetched_at,
            "cache_used": self.cache_used,
            "event_count": len(self.events),
            "sources": self.sources,
            "warnings": self.warnings,
        }


def _clean_html(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    return " ".join(unescape(value).split())


def _section_for_year(html: str, year: int) -> str:
    start = re.search(
        rf">{year}\s+FOMC Meetings<",
        html,
        flags=re.IGNORECASE,
    )
    if not start:
        return ""
    remainder = html[start.end() :]
    end = re.search(r">\d{4}\s+FOMC Meetings<", remainder, re.IGNORECASE)
    return remainder[: end.start()] if end else remainder


def parse_fomc_events(html: str, years: set[int]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    pattern = re.compile(
        r'fomc-meeting__month[^>]*>\s*<strong>([^<]+)</strong>.*?'
        r'fomc-meeting__date[^>]*>\s*([^<]+)</div>',
        flags=re.IGNORECASE | re.DOTALL,
    )
    for year in sorted(years):
        section = _section_for_year(html, year)
        for month_label, day_label in pattern.findall(section):
            numbers = [int(item) for item in re.findall(r"\d+", day_label)]
            if not numbers:
                continue
            end_day = numbers[-1]
            month_names = [item.strip() for item in month_label.split("/")]
            end_month_name = month_names[-1]
            end_month = MONTHS.get(end_month_name)
            if not end_month:
                continue
            meeting_end = date(year, end_month, end_day)
            beijing_release_date = meeting_end + timedelta(days=1)
            impact = trading_session_on_or_after(beijing_release_date).date()
            projection = "*" in day_label
            events.append(
                {
                    "id": f"fomc_{meeting_end:%Y%m%d}",
                    "impact_date": impact.isoformat(),
                    "release_time": "A股开盘前（北京时间凌晨）",
                    "title": (
                        "美联储议息结果与经济预测"
                        if projection
                        else "美联储议息结果"
                    ),
                    "category": "全球宏观",
                    "status": "已确认",
                    "source_tier": "官方自动日程",
                    "risk_score": 5,
                    "risk": "极高",
                    "direction": "双向",
                    "market_wide": True,
                    "affected_sectors": [],
                    "affected_labels": [
                        "全市场",
                        "成长风格",
                        "有色金属",
                        "银行",
                    ],
                    "mechanism": (
                        "利率路径通过美元、美债收益率、离岸人民币与隔夜"
                        "美股影响A股风险偏好。"
                    ),
                    "bull_case": (
                        "措辞偏鸽且美元、美债收益率回落，成长与有色相对受益。"
                    ),
                    "bear_case": (
                        "措辞偏鹰并推动美债收益率上行，高估值方向承压。"
                    ),
                    "confirmation": (
                        "开盘前同时检查纳指、美债收益率、美元与离岸人民币。"
                    ),
                    "source_name": "Federal Reserve",
                    "url": FED_URL,
                    "auto_collected": True,
                    "scheduled_date": meeting_end.isoformat(),
                }
            )
    return events


def parse_bea_events(html: str, year: int) -> list[dict[str, Any]]:
    grouped: dict[date, list[str]] = {}
    for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", html, re.I | re.S):
        date_match = re.search(
            r'<div class="release-date">([^<]+)</div>',
            row,
            re.I,
        )
        title_match = re.search(
            r'<td class="release-title[^"]*"[^>]*>(.*?)</td>',
            row,
            re.I | re.S,
        )
        if not date_match or not title_match:
            continue
        title = _clean_html(title_match.group(1))
        if not (
            title.startswith("GDP (")
            or title.startswith("Personal Income and Outlays")
        ):
            continue
        try:
            release_date = datetime.strptime(
                f"{date_match.group(1).strip()} {year}",
                "%B %d %Y",
            ).date()
        except ValueError:
            continue
        grouped.setdefault(release_date, []).append(title)

    events: list[dict[str, Any]] = []
    for release_date, titles in sorted(grouped.items()):
        impact = trading_session_after(release_date).date()
        has_gdp = any(title.startswith("GDP (") for title in titles)
        has_pce = any("Personal Income and Outlays" in title for title in titles)
        label = (
            "美国GDP与PCE数据"
            if has_gdp and has_pce
            else "美国GDP数据"
            if has_gdp
            else "美国PCE与收入支出数据"
        )
        events.append(
            {
                "id": f"us_gdp_pce_{release_date:%Y%m%d}",
                "impact_date": impact.isoformat(),
                "release_time": "前一日晚间发布，下一A股交易日反应",
                "title": label,
                "category": "全球宏观",
                "status": "已确认",
                "source_tier": "官方自动日程",
                "risk_score": 4,
                "risk": "高",
                "direction": "双向",
                "market_wide": True,
                "affected_sectors": [],
                "affected_labels": [
                    "全市场",
                    "成长风格",
                    "有色金属",
                    "出口链",
                ],
                "mechanism": (
                    "增长与通胀组合改变降息定价，并通过美元、利率和"
                    "美股影响下一交易日A股。"
                ),
                "bull_case": (
                    "温和增长与通胀回落组合，有利于风险偏好和成长估值。"
                ),
                "bear_case": (
                    "通胀超预期或增长失速，可能触发利率上行或衰退交易。"
                ),
                "confirmation": (
                    "结合美元、美债收益率、纳指和离岸人民币判断。"
                ),
                "source_name": "U.S. Bureau of Economic Analysis",
                "url": BEA_URL,
                "auto_collected": True,
                "scheduled_date": release_date.isoformat(),
                "release_items": titles,
            }
        )
    return events


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _read_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def fetch_official_events(
    forecast_dates: list[str],
    *,
    cache_path: str | Path = DEFAULT_CACHE,
    timeout: int = 20,
    get: Callable[..., requests.Response] = requests.get,
) -> EventCollection:
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    result = EventCollection(fetched_at=now.isoformat())
    target_dates = {date.fromisoformat(item) for item in forecast_dates}
    years = {item.year for item in target_dates}
    cache_path = Path(cache_path)
    cache = _read_cache(cache_path)
    cached_events = cache.get("events", [])
    live_events: list[dict[str, Any]] = []
    failed_sources: set[str] = set()

    source_specs = (
        (
            "federal_reserve",
            FED_URL,
            lambda text: parse_fomc_events(text, years),
        ),
        (
            "bea",
            BEA_URL,
            lambda text: [
                event
                for year in years
                for event in parse_bea_events(text, year)
            ],
        ),
    )
    for source_id, url, parser in source_specs:
        try:
            response = get(
                url,
                timeout=timeout,
                headers={"User-Agent": USER_AGENT},
            )
            response.raise_for_status()
            parsed = parser(response.text)
            for event in parsed:
                event["_source_id"] = source_id
            live_events.extend(parsed)
            result.sources.append(
                {
                    "id": source_id,
                    "url": url,
                    "status": "live",
                    "parsed_events": len(parsed),
                }
            )
        except Exception as exc:
            failed_sources.add(source_id)
            result.warnings.append(f"{source_id} 抓取失败: {type(exc).__name__}")
            result.sources.append(
                {
                    "id": source_id,
                    "url": url,
                    "status": "failed",
                    "parsed_events": 0,
                }
            )

    if failed_sources and cached_events:
        cached_fallback = [
            event
            for event in cached_events
            if event.get("_source_id") in failed_sources
        ]
        live_events.extend(cached_fallback)
        if cached_fallback:
            result.cache_used = True
            for source in result.sources:
                if (
                    source["id"] in failed_sources
                    and any(
                        item.get("_source_id") == source["id"]
                        for item in cached_fallback
                    )
                ):
                    source["status"] = "cached"
                    source["parsed_events"] = sum(
                        item.get("_source_id") == source["id"]
                        for item in cached_fallback
                    )

    deduplicated = {event["id"]: event for event in live_events}
    all_events = list(deduplicated.values())
    result.events = [
        event
        for event in all_events
        if date.fromisoformat(event["impact_date"]) in target_dates
    ]

    if any(source["status"] == "live" for source in result.sources):
        _atomic_json(
            cache_path,
            {
                "fetched_at": result.fetched_at,
                "events": all_events,
                "sources": result.sources,
            },
        )
    return result

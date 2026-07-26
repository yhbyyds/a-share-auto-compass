from __future__ import annotations

from datetime import date, datetime, timedelta
from functools import lru_cache
from html import unescape
import json
import os
from pathlib import Path
import re
from typing import Callable
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd
import requests


CALENDAR_NAME = "XSHG"
CALENDAR_SOURCE = "上海证券交易所年度休市安排（版本化官方清单）"
CALENDAR_URL = "https://www.sse.com.cn/disclosure/dealinstruc/closed/"
CALENDAR_FILE = (
    Path(__file__).resolve().parents[1] / "data" / "sse_holidays.json"
)


@lru_cache(maxsize=1)
def _manifest() -> dict:
    try:
        payload = json.loads(CALENDAR_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("无法读取上交所年度休市清单") from exc
    if payload.get("source") != CALENDAR_URL or not payload.get("years"):
        raise RuntimeError("上交所年度休市清单缺少可审计来源或年份")
    return payload


def parse_sse_holiday_ranges(
    html: str,
    year: int,
) -> list[list[str]]:
    text = unescape(re.sub(r"<[^>]+>", " ", html))
    text = " ".join(text.split())
    marker = f"{year}年休市安排"
    start = text.find(marker)
    if start < 0:
        raise ValueError(f"上交所页面尚无 {year} 年休市安排")
    section = text[start + len(marker) :]
    next_year = re.search(r"\d{4}年休市安排", section)
    if next_year:
        section = section[: next_year.start()]
    section = section.split("相关公告", 1)[0]

    ranges: list[list[str]] = []
    pattern = re.compile(
        r"(\d{1,2})月(\d{1,2})日[^，。；]*?"
        r"至(?:(\d{1,2})月)?(\d{1,2})日[^，。；]*?休市"
    )
    for start_month, start_day, end_month, end_day in pattern.findall(
        section
    ):
        first = date(year, int(start_month), int(start_day))
        last = date(
            year,
            int(end_month or start_month),
            int(end_day),
        )
        if first > last:
            raise ValueError(f"{year} 年休市安排包含倒置日期")
        ranges.append([first.isoformat(), last.isoformat()])

    if len(ranges) < 7:
        raise ValueError(
            f"{year} 年休市安排仅解析出 {len(ranges)} 段，拒绝采用"
        )
    return ranges


def refresh_official_calendar(
    year: int,
    *,
    timeout: int = 20,
    get: Callable[..., requests.Response] = requests.get,
) -> bool:
    payload = _manifest()
    if str(year) in payload["years"]:
        return False

    response = get(
        CALENDAR_URL,
        timeout=timeout,
        headers={"User-Agent": "A-Share-Compass/8.0"},
    )
    response.raise_for_status()
    try:
        html = response.content.decode("utf-8")
    except (AttributeError, UnicodeDecodeError):
        html = response.text
    ranges = parse_sse_holiday_ranges(html, year)

    updated = json.loads(json.dumps(payload, ensure_ascii=False))
    updated["years"][str(year)] = {
        "notice": f"上海证券交易所{year}年休市安排",
        "closed_ranges": ranges,
    }
    updated["retrieved_at"] = datetime.now(
        ZoneInfo("Asia/Shanghai")
    ).date().isoformat()
    updated["verified_through"] = max(
        str(updated.get("verified_through", "")),
        f"{year}-12-31",
    )
    temporary = CALENDAR_FILE.with_name(
        f".{CALENDAR_FILE.name}.{uuid4().hex}.tmp"
    )
    temporary.write_text(
        json.dumps(updated, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, CALENDAR_FILE)
    _manifest.cache_clear()
    _closed_dates.cache_clear()
    return True


def prepare_calendar_updates(
    reference: date | None = None,
) -> list[str]:
    reference = reference or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    years = [reference.year]
    if reference.month >= 11:
        years.append(reference.year + 1)

    warnings: list[str] = []
    available = _manifest()["years"]
    for year in years:
        if str(year) in available:
            continue
        try:
            refresh_official_calendar(year)
        except Exception as exc:
            warnings.append(f"{year}年官方休市安排同步失败: {type(exc).__name__}")
    return warnings


@lru_cache(maxsize=16)
def _closed_dates(year: int) -> frozenset[date]:
    year_data = _manifest()["years"].get(str(year))
    if not year_data:
        raise RuntimeError(
            f"缺少 {year} 年上交所官方休市清单；为防止误报，停止生成预测"
        )

    closed: set[date] = set()
    for start_text, end_text in year_data.get("closed_ranges", []):
        start = date.fromisoformat(start_text)
        end = date.fromisoformat(end_text)
        if start.year != year or end.year != year or start > end:
            raise RuntimeError(f"{year} 年上交所休市清单包含无效区间")
        current = start
        while current <= end:
            closed.add(current)
            current += timedelta(days=1)
    return frozenset(closed)


def _date(value: date | pd.Timestamp | str) -> date:
    return pd.Timestamp(value).date()


def is_trading_session(value: date | pd.Timestamp | str) -> bool:
    session_date = _date(value)
    return (
        session_date.weekday() < 5
        and session_date not in _closed_dates(session_date.year)
    )


def trading_sessions_after(
    value: date | pd.Timestamp | str,
    count: int = 5,
) -> list[pd.Timestamp]:
    if count < 1:
        return []

    sessions: list[pd.Timestamp] = []
    candidate = _date(value) + timedelta(days=1)
    while len(sessions) < count:
        if is_trading_session(candidate):
            sessions.append(pd.Timestamp(candidate))
        candidate += timedelta(days=1)
        if (candidate - _date(value)).days > 60:
            raise RuntimeError(
                f"交易日历不足，无法生成未来 {count} 个A股交易日"
            )
    return sessions


def trading_session_on_or_after(
    value: date | pd.Timestamp | str,
) -> pd.Timestamp:
    candidate = _date(value)
    for _ in range(61):
        if is_trading_session(candidate):
            return pd.Timestamp(candidate)
        candidate += timedelta(days=1)
    raise RuntimeError("交易日历不足，无法找到下一个A股交易日")


def trading_session_after(
    value: date | pd.Timestamp | str,
) -> pd.Timestamp:
    return trading_sessions_after(value, 1)[0]


def calendar_metadata() -> dict[str, str]:
    manifest = _manifest()
    return {
        "name": CALENDAR_NAME,
        "source": CALENDAR_SOURCE,
        "url": CALENDAR_URL,
        "verified_through": str(manifest.get("verified_through", "")),
        "retrieved_at": str(manifest.get("retrieved_at", "")),
        "available_years": ",".join(sorted(manifest.get("years", {}))),
        "policy": "仅使用已核验年度；缺少官方清单时拒绝发布",
    }

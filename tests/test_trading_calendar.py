from datetime import date

import pytest

from market_forecast.trading_calendar import (
    is_trading_session,
    parse_sse_holiday_ranges,
    trading_sessions_after,
)


def test_labor_day_holiday_is_skipped() -> None:
    sessions = trading_sessions_after(date(2026, 4, 30), 2)

    assert [item.strftime("%Y-%m-%d") for item in sessions] == [
        "2026-05-06",
        "2026-05-07",
    ]


def test_national_day_holiday_is_skipped() -> None:
    sessions = trading_sessions_after(date(2026, 9, 30), 2)

    assert [item.strftime("%Y-%m-%d") for item in sessions] == [
        "2026-10-08",
        "2026-10-09",
    ]
    assert not is_trading_session(date(2026, 10, 5))


def test_unknown_calendar_year_fails_closed() -> None:
    with pytest.raises(RuntimeError, match="缺少 2027 年"):
        trading_sessions_after(date(2026, 12, 31), 1)


def test_parse_official_sse_calendar() -> None:
    html = """
    <h2>2027年休市安排</h2>
    元旦：1月1日至1月3日休市。
    春节：2月6日至2月14日休市。
    清明节：4月3日至4月5日休市。
    劳动节：5月1日至5月5日休市。
    端午节：6月9日至6月11日休市。
    中秋节：9月15日至9月17日休市。
    国庆节：10月1日至10月7日休市。
    <h2>相关公告</h2>
    """

    ranges = parse_sse_holiday_ranges(html, 2027)

    assert ranges[0] == ["2027-01-01", "2027-01-03"]
    assert ranges[-1] == ["2027-10-01", "2027-10-07"]

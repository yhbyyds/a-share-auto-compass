from market_forecast.official_events import (
    parse_bea_events,
    parse_fomc_events,
)


def test_fomc_parser_maps_beijing_release_to_a_share_session() -> None:
    html = """
    <a id="x">2026 FOMC Meetings</a>
    <div class="fomc-meeting__month"><strong>July</strong></div>
    <div class="fomc-meeting__date">28-29</div>
    <a id="y">2025 FOMC Meetings</a>
    """

    events = parse_fomc_events(html, {2026})

    assert len(events) == 1
    assert events[0]["scheduled_date"] == "2026-07-29"
    assert events[0]["impact_date"] == "2026-07-30"
    assert events[0]["status"] == "已确认"


def test_bea_parser_groups_gdp_and_pce_and_uses_next_session() -> None:
    html = """
    <tr>
      <td><div class="release-date">July 30</div></td>
      <td class="release-title">GDP (Advance Estimate), 2nd Quarter 2026</td>
    </tr>
    <tr>
      <td><div class="release-date">July 30</div></td>
      <td class="release-title">Personal Income and Outlays, June 2026</td>
    </tr>
    """

    events = parse_bea_events(html, 2026)

    assert len(events) == 1
    assert events[0]["title"] == "美国GDP与PCE数据"
    assert events[0]["impact_date"] == "2026-07-31"
    assert len(events[0]["release_items"]) == 2

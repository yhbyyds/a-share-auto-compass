"""Collect one timestamped A-share intraday research snapshot.

Schedule this script at the fixed collection buckets defined in
``market_forecast.intraday``.  It records data only; it never sends orders or
turns an unvalidated live signal into a trade instruction.
"""
from __future__ import annotations

import argparse
from datetime import datetime

import requests

from market_forecast.data import MarketDataError
from market_forecast.intraday import BEIJING, collect_intraday_snapshot, collection_window, intraday_research_status


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect one intraday research snapshot")
    parser.add_argument("--status", action="store_true", help="Print training status without collection")
    parser.add_argument(
        "--defer-on-source-error",
        action="store_true",
        help="Exit successfully when a hosted collector is late or a public data source is temporarily unavailable",
    )
    args = parser.parse_args()
    if args.status:
        print(intraday_research_status())
        return
    now = datetime.now(BEIJING)
    window = collection_window(now)
    if window is None:
        print(f"deferred {now.isoformat(timespec='minutes')}: outside the fixed collection window")
        return
    try:
        row = collect_intraday_snapshot(now=now)
    except (requests.RequestException, MarketDataError, ValueError, TypeError, KeyError, IndexError) as exc:
        if not args.defer_on_source_error:
            raise
        print(f"deferred {now.isoformat(timespec='minutes')} {window}: {type(exc).__name__}")
        return
    print(f"saved {row['date']} {row['bucket']}: {len(row['themes'])} micro-themes")


if __name__ == "__main__":
    main()

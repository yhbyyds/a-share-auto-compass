"""Collect one timestamped A-share intraday research snapshot.

Schedule this script at the fixed collection buckets defined in
``market_forecast.intraday``.  It records data only; it never sends orders or
turns an unvalidated live signal into a trade instruction.
"""
from __future__ import annotations

import argparse

from market_forecast.intraday import collect_intraday_snapshot, intraday_research_status


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect one intraday research snapshot")
    parser.add_argument("--status", action="store_true", help="Print training status without collection")
    args = parser.parse_args()
    if args.status:
        print(intraday_research_status())
        return
    row = collect_intraday_snapshot()
    print(f"saved {row['date']} {row['bucket']}: {len(row['themes'])} micro-themes")


if __name__ == "__main__":
    main()

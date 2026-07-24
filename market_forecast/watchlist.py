from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

from market_forecast.data import _session


SINA_SPOT_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeData"
)
TENCENT_FQ_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"


def _number(value: Any, default: float = math.nan) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _fetch_liquid_universe(limit: int = 100) -> list[dict[str, Any]]:
    response = _session().get(
        SINA_SPOT_URL,
        params={
            "page": 1,
            "num": min(limit, 100),
            "sort": "amount",
            "asc": 0,
            "node": "hs_a",
            "symbol": "",
            "_s_r_a": "page",
        },
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; AShareCompass/1.1)",
            "Referer": "https://finance.sina.com.cn/",
        },
        timeout=25,
    )
    response.raise_for_status()
    rows = response.json()
    return [
        row
        for row in rows
        if row.get("symbol", "").startswith(("sh", "sz"))
        and not any(flag in row.get("name", "") for flag in ("ST", "退", "B"))
        and _number(row.get("amount"), 0) >= 5e8
        and _number(row.get("trade"), 0) >= 3
        and abs(_number(row.get("changepercent"), 99)) < 9.5
        and 0.2 <= _number(row.get("turnoverratio"), 0) <= 12
    ]


def _fetch_history(symbol: str, count: int = 260) -> pd.DataFrame:
    response = requests.get(
        TENCENT_FQ_URL,
        params={"param": f"{symbol},day,,,{count},qfq"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=20,
    )
    response.raise_for_status()
    item = (response.json().get("data", {}) or {}).get(symbol, {}) or {}
    rows = item.get("qfqday", []) or item.get("day", [])
    if len(rows) < 120:
        raise ValueError(f"{symbol}历史数据不足")
    frame = pd.DataFrame(
        [row[:6] for row in rows],
        columns=["date", "open", "close", "high", "low", "volume"],
    )
    frame["date"] = pd.to_datetime(frame["date"])
    for column in ("open", "close", "high", "low", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.dropna().set_index("date").sort_index()


def _rsi(series: pd.Series, window: int = 14) -> float:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(window).mean()
    loss = -delta.clip(upper=0).rolling(window).mean()
    rs = gain / loss.replace(0, np.nan)
    value = 100 - 100 / (1 + rs)
    return float(value.iloc[-1])


def _metrics(row: dict[str, Any], history: pd.DataFrame, benchmark: pd.Series) -> dict:
    close = history["close"]
    returns = close.pct_change()
    ma20 = float(close.rolling(20).mean().iloc[-1])
    ma60 = float(close.rolling(60).mean().iloc[-1])
    momentum5 = float(close.pct_change(5).iloc[-1])
    momentum20 = float(close.pct_change(20).iloc[-1])
    momentum60 = float(close.pct_change(60).iloc[-1])
    benchmark_aligned = benchmark.reindex(history.index).ffill()
    relative20 = momentum20 - float(benchmark_aligned.pct_change(20).iloc[-1])
    volatility20 = float(returns.rolling(20).std().iloc[-1])
    drawdown60 = float(close.iloc[-1] / close.tail(60).max() - 1)
    volume = np.log1p(history["volume"])
    volume_z20 = float(
        (volume.iloc[-1] - volume.tail(20).mean()) / volume.tail(20).std()
    )
    invalid_level = float(max(ma20 * 0.985, history["low"].tail(10).min()))
    return {
        "symbol": row["symbol"],
        "code": row["code"],
        "name": row["name"],
        "price": float(close.iloc[-1]),
        "change": _number(row.get("changepercent"), 0),
        "amount": _number(row.get("amount"), 0),
        "turnover": _number(row.get("turnoverratio"), 0),
        "pe": _number(row.get("per")),
        "pb": _number(row.get("pb")),
        "ma20": ma20,
        "ma60": ma60,
        "momentum5": momentum5,
        "momentum20": momentum20,
        "momentum60": momentum60,
        "relative20": relative20,
        "volatility20": volatility20,
        "drawdown60": drawdown60,
        "volume_z20": volume_z20,
        "rsi14": _rsi(close),
        "invalid_level": invalid_level,
        "data_date": history.index[-1].strftime("%Y-%m-%d"),
    }


def _rank_candidates(metrics: list[dict], count: int) -> list[dict]:
    frame = pd.DataFrame(metrics).replace([np.inf, -np.inf], np.nan).dropna(
        subset=[
            "price",
            "ma20",
            "ma60",
            "momentum20",
            "relative20",
            "volatility20",
            "rsi14",
        ]
    )
    filtered = frame[
        (frame["price"] > frame["ma20"])
        & (frame["ma20"] > frame["ma60"])
        & frame["momentum20"].between(0.01, 0.25)
        & frame["momentum5"].between(-0.06, 0.12)
        & frame["rsi14"].between(48, 75)
        & (frame["drawdown60"] > -0.16)
        & (frame["volatility20"] < 0.055)
    ].copy()
    if len(filtered) < count:
        filtered = frame[
            (frame["price"] > frame["ma20"] * 0.99)
            & (frame["ma20"] > frame["ma60"] * 0.985)
            & frame["momentum20"].between(0.0, 0.35)
            & frame["momentum5"].between(-0.08, 0.18)
            & frame["rsi14"].between(42, 76)
            & (frame["drawdown60"] > -0.20)
            & (frame["volatility20"] < 0.07)
        ].copy()
    if filtered.empty:
        return []

    filtered["score"] = 100 * (
        0.24 * filtered["momentum20"].rank(pct=True)
        + 0.18 * filtered["relative20"].rank(pct=True)
        + 0.12 * filtered["momentum5"].rank(pct=True)
        + 0.14 * (filtered["price"] / filtered["ma20"] - 1).rank(pct=True)
        + 0.12 * filtered["amount"].rank(pct=True)
        + 0.10 * (1 - filtered["volatility20"].rank(pct=True))
        + 0.10 * (1 - (filtered["rsi14"] - 60).abs().rank(pct=True))
    )
    filtered = filtered.sort_values("score", ascending=False).head(count)
    output: list[dict] = []
    for _, item in filtered.iterrows():
        output.append(
            {
                "code": item["code"],
                "name": item["name"],
                "price": round(float(item["price"]), 2),
                "change": round(float(item["change"]), 2),
                "score": round(float(item["score"]), 1),
                "momentum_5d": round(float(item["momentum5"]) * 100, 2),
                "momentum_20d": round(float(item["momentum20"]) * 100, 2),
                "relative_20d": round(float(item["relative20"]) * 100, 2),
                "rsi_14": round(float(item["rsi14"]), 1),
                "volatility_20d": round(float(item["volatility20"]) * 100, 2),
                "turnover": round(float(item["turnover"]), 2),
                "amount_yi": round(float(item["amount"]) / 1e8, 1),
                "ma20": round(float(item["ma20"]), 2),
                "invalid_level": round(float(item["invalid_level"]), 2),
                "data_date": item["data_date"],
                "reason": (
                    f"20日动量{item['momentum20'] * 100:+.1f}%，"
                    f"跑赢沪深300 {item['relative20'] * 100:+.1f}%，"
                    f"成交额{item['amount'] / 1e8:.0f}亿元"
                ),
                "trigger": "仅在大盘未出现放量破位、个股维持20日线上方时观察",
                "invalid": f"收盘跌破约{item['invalid_level']:.2f}元则量价结构失效",
            }
        )
    return output


def generate_watchlist(
    market_data: dict[str, pd.DataFrame],
    count: int = 6,
    cache_dir: str | Path = "data/cache",
) -> list[dict]:
    """Create a research watchlist; it is not a buy recommendation."""
    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)
    cache_file = cache_path / "watchlist.json"
    try:
        universe = _fetch_liquid_universe()
        histories: dict[str, pd.DataFrame] = {}
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {
                executor.submit(_fetch_history, row["symbol"]): row["symbol"]
                for row in universe[:70]
            }
            for future in as_completed(futures):
                try:
                    histories[futures[future]] = future.result()
                except (requests.RequestException, ValueError):
                    continue
        benchmark = market_data["csi300"]["close"]
        metrics = [
            _metrics(row, histories[row["symbol"]], benchmark)
            for row in universe
            if row["symbol"] in histories
        ]
        result = _rank_candidates(metrics, count)
        if not result:
            raise ValueError("没有通过风险过滤的候选")
        cache_file.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return result
    except (requests.RequestException, ValueError, KeyError):
        if cache_file.exists():
            return json.loads(cache_file.read_text(encoding="utf-8"))
        return []

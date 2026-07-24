from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
import math
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


EASTMONEY_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"


@dataclass(frozen=True)
class IndexSpec:
    key: str
    name: str
    secid: str
    tencent: str
    domestic: bool = True


INDEXES: tuple[IndexSpec, ...] = (
    IndexSpec("sse", "上证指数", "1.000001", "sh000001"),
    IndexSpec("sse50", "上证50", "1.000016", "sh000016"),
    IndexSpec("csi300", "沪深300", "1.000300", "sh000300"),
    IndexSpec("csi500", "中证500", "1.000905", "sh000905"),
    IndexSpec("csi1000", "中证1000", "1.000852", "sh000852"),
    IndexSpec("chinext", "创业板指", "0.399006", "sz399006"),
    IndexSpec("hsi", "恒生指数", "", "hkHSI", False),
    IndexSpec("sp500", "标普500", "", "us.INX", False),
    IndexSpec("nasdaq", "纳斯达克", "", "us.IXIC", False),
    IndexSpec("dow", "道琼斯", "", "us.DJI", False),
)

DOMESTIC_KEYS = frozenset(spec.key for spec in INDEXES if spec.domestic)
GLOBAL_KEYS = frozenset(spec.key for spec in INDEXES if not spec.domestic)

SPOT_URL = "https://push2.eastmoney.com/api/qt/clist/get"
TENCENT_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/kline/kline"


class MarketDataError(RuntimeError):
    pass


def _session() -> requests.Session:
    session = requests.Session()
    retries = Retry(
        total=4,
        connect=4,
        read=4,
        backoff_factor=0.7,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
    )
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session


def _fetch_one(spec: IndexSpec, start: str = "20100101") -> pd.DataFrame:
    if not spec.secid:
        raise MarketDataError(f"{spec.name}没有东方财富备用代码")
    params = {
        "secid": spec.secid,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101",
        "fqt": "1",
        "beg": start,
        "end": "20500101",
        "lmt": "10000",
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; AShareCompass/1.0)",
        "Referer": "https://quote.eastmoney.com/",
    }
    try:
        response = _session().get(
            EASTMONEY_URL, params=params, headers=headers, timeout=20
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("data", {}).get("klines", [])
    except (requests.RequestException, ValueError, AttributeError) as exc:
        raise MarketDataError(f"{spec.name}数据获取失败: {exc}") from exc

    if len(rows) < 500:
        raise MarketDataError(f"{spec.name}有效历史数据不足")

    columns = [
        "date",
        "open",
        "close",
        "high",
        "low",
        "volume",
        "amount",
        "amplitude",
        "pct",
        "change",
        "turnover",
    ]
    frame = pd.DataFrame([row.split(",") for row in rows], columns=columns)
    frame["date"] = pd.to_datetime(frame["date"])
    for column in columns[1:]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.set_index("date").sort_index()
    return frame


def _fetch_one_tencent(spec: IndexSpec) -> pd.DataFrame:
    params = {
        "param": f"{spec.tencent},day,2010-01-01,2050-01-01,2000",
    }
    try:
        response = _session().get(
            TENCENT_KLINE_URL,
            params=params,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        item = payload.get("data", {}).get(spec.tencent, {}) or {}
        rows = item.get("day", []) or item.get("qfqday", [])
    except (requests.RequestException, ValueError, AttributeError) as exc:
        raise MarketDataError(f"{spec.name}备用数据获取失败: {exc}") from exc
    if len(rows) < 500:
        raise MarketDataError(f"{spec.name}备用历史数据不足")

    frame = pd.DataFrame(
        [row[:6] for row in rows],
        columns=["date", "open", "close", "high", "low", "volume"],
    )
    frame["date"] = pd.to_datetime(frame["date"])
    for column in ("open", "close", "high", "low", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["amount"] = frame["volume"] * frame["close"]
    frame["amplitude"] = (frame["high"] - frame["low"]) / frame["close"] * 100
    frame["pct"] = frame["close"].pct_change() * 100
    frame["change"] = frame["close"].diff()
    frame["turnover"] = np.nan
    return frame.set_index("date").sort_index()


def fetch_market_data(
    cache_dir: str | Path = "data/cache",
    specs: Iterable[IndexSpec] = INDEXES,
) -> dict[str, pd.DataFrame]:
    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)
    output: dict[str, pd.DataFrame] = {}

    for spec in specs:
        file_path = cache_path / f"{spec.key}.csv"
        try:
            try:
                frame = _fetch_one_tencent(spec)
            except MarketDataError:
                frame = _fetch_one(spec)
            frame.to_csv(file_path, encoding="utf-8")
        except MarketDataError:
            if not file_path.exists():
                raise
            frame = pd.read_csv(file_path, parse_dates=["date"], index_col="date")
            if frame.index.max().date() < date.today():
                age = (date.today() - frame.index.max().date()).days
                if age > 7:
                    raise MarketDataError(f"{spec.name}缓存已过期 {age} 天")
        output[spec.key] = frame
    return output


def fetch_market_breadth(cache_dir: str | Path = "data/cache") -> dict:
    """Fetch a point-in-time breadth snapshot for context, not model training."""
    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)
    cache_file = cache_path / "breadth.json"
    fields = "f3,f6,f12,f14,f20"
    base_params = {
        "pz": "500",
        "po": "1",
        "np": "1",
        "fltt": "2",
        "invt": "2",
        "fid": "f3",
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
        "fields": fields,
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; AShareCompass/1.0)",
        "Referer": "https://quote.eastmoney.com/",
    }
    try:
        session = _session()
        stocks: list[dict] = []
        total = 1
        page = 1
        while len(stocks) < total:
            params = {**base_params, "pn": str(page)}
            response = session.get(
                SPOT_URL, params=params, headers=headers, timeout=20
            )
            response.raise_for_status()
            payload = response.json().get("data") or {}
            total = int(payload.get("total", 0))
            rows = payload.get("diff") or []
            if not rows:
                break
            stocks.extend(rows)
            page += 1
            if page > math.ceil(max(total, 1) / 500) + 1:
                break

        changes = pd.Series(
            [row.get("f3") for row in stocks], dtype="float64"
        ).dropna()
        amounts = pd.Series(
            [row.get("f6") for row in stocks], dtype="float64"
        ).fillna(0)
        if len(changes) < 1000:
            raise MarketDataError("全市场广度数据不足")
        breadth = {
            "stocks": int(len(changes)),
            "advancers": int((changes > 0.05).sum()),
            "decliners": int((changes < -0.05).sum()),
            "flat": int((changes.abs() <= 0.05).sum()),
            "limit_up_proxy": int((changes >= 9.8).sum()),
            "limit_down_proxy": int((changes <= -9.8).sum()),
            "median_change": round(float(changes.median()), 2),
            "turnover_yi": round(float(amounts.sum() / 1e8), 0),
            "advance_ratio": round(float((changes > 0.05).mean() * 100), 1),
        }
        cache_file.write_text(
            json.dumps(breadth, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return breadth
    except (requests.RequestException, ValueError, MarketDataError):
        if cache_file.exists():
            return json.loads(cache_file.read_text(encoding="utf-8"))
        return {
            "stocks": 0,
            "advancers": 0,
            "decliners": 0,
            "flat": 0,
            "limit_up_proxy": 0,
            "limit_down_proxy": 0,
            "median_change": 0,
            "turnover_yi": 0,
            "advance_ratio": 0,
        }

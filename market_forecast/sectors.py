from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests
from urllib3.exceptions import InsecureRequestWarning
from sklearn.base import clone
from sklearn.ensemble import (
    HistGradientBoostingClassifier,
    HistGradientBoostingRegressor,
)
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import accuracy_score, brier_score_loss, roc_auc_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from market_forecast.data import MarketDataError, _session
from market_forecast.model import _next_weekdays, _rsi, build_features


SW_TREND_URL = "https://www.swsresearch.com/institute-sw/api/index_publish/trend/"
RANDOM_STATE = 20260725
requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)


@dataclass(frozen=True)
class SectorSpec:
    key: str
    name: str
    code: str
    group: str


SECTORS: tuple[SectorSpec, ...] = (
    SectorSpec("electronics", "电子", "801080", "科技"),
    SectorSpec("computer", "计算机", "801750", "科技"),
    SectorSpec("telecom", "通信", "801770", "科技"),
    SectorSpec("power_equipment", "电力设备", "801730", "制造"),
    SectorSpec("utilities", "公用事业（含电力）", "801160", "能源"),
    SectorSpec("defense", "国防军工", "801740", "制造"),
    SectorSpec("auto", "汽车", "801880", "制造"),
    SectorSpec("healthcare", "医药生物", "801150", "消费"),
    SectorSpec("bank", "银行", "801780", "金融"),
    SectorSpec("nonbank", "非银金融", "801790", "金融"),
    SectorSpec("nonferrous", "有色金属", "801050", "周期"),
    SectorSpec("food_beverage", "食品饮料", "801120", "消费"),
)


def _fetch_sw_sector(spec: SectorSpec) -> pd.DataFrame:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; AShareCompass/1.1)",
        "Referer": (
            "https://www.swsresearch.com/institute_sw/allIndex/"
            f"releasedIndex/releasedetail?code={spec.code}"
        ),
    }
    try:
        response = _session().get(
            SW_TREND_URL,
            params={"swindexcode": spec.code, "period": "DAY"},
            headers=headers,
            timeout=30,
            verify=False,
        )
        response.raise_for_status()
        rows = response.json().get("data") or []
    except (requests.RequestException, ValueError, AttributeError) as exc:
        raise MarketDataError(f"{spec.name}行业指数获取失败: {exc}") from exc

    if len(rows) < 800:
        raise MarketDataError(f"{spec.name}行业指数历史数据不足")
    frame = pd.DataFrame(rows).rename(
        columns={
            "bargaindate": "date",
            "openindex": "open",
            "closeindex": "close",
            "maxindex": "high",
            "minindex": "low",
            "bargainamount": "volume",
            "bargainsum": "amount",
        }
    )
    required = ["date", "open", "close", "high", "low", "volume", "amount"]
    frame = frame[required]
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    for column in required[1:]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["date", "close"]).set_index("date").sort_index()
    frame["pct"] = frame["close"].pct_change() * 100
    return frame


def fetch_sector_data(
    cache_dir: str | Path = "data/cache/sectors",
) -> dict[str, pd.DataFrame]:
    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)
    output: dict[str, pd.DataFrame] = {}

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(_fetch_sw_sector, spec): spec for spec in SECTORS}
        for future in as_completed(futures):
            spec = futures[future]
            file_path = cache_path / f"{spec.key}.csv"
            try:
                frame = future.result()
                frame.to_csv(file_path, encoding="utf-8")
            except MarketDataError:
                if not file_path.exists():
                    raise
                frame = pd.read_csv(
                    file_path, parse_dates=["date"], index_col="date"
                )
                age = (date.today() - frame.index.max().date()).days
                if age > 7:
                    raise MarketDataError(f"{spec.name}行业指数缓存已过期 {age} 天")
            output[spec.key] = frame
    return output


def _sector_features(
    frame: pd.DataFrame,
    benchmark_close: pd.Series,
    index: pd.DatetimeIndex,
) -> pd.DataFrame:
    close = frame["close"].reindex(index).ffill()
    benchmark = benchmark_close.reindex(index).ffill()
    daily = close.pct_change()
    features = pd.DataFrame(index=index)
    for window in (1, 3, 5, 10, 20, 60):
        features[f"s_return_{window}"] = close.pct_change(window)
    for window in (5, 20, 60):
        features[f"s_ma_gap_{window}"] = close / close.rolling(window).mean() - 1
        features[f"s_relative_{window}"] = (
            close.pct_change(window) - benchmark.pct_change(window)
        )
    for window in (5, 20, 60):
        features[f"s_volatility_{window}"] = daily.rolling(window).std()
    features["s_rsi_14"] = _rsi(close) / 100
    features["s_drawdown_60"] = close / close.rolling(60).max() - 1
    amount = frame["amount"].reindex(index).ffill()
    log_amount = np.log1p(amount.clip(lower=0))
    features["s_amount_z20"] = (
        (log_amount - log_amount.rolling(20).mean())
        / log_amount.rolling(20).std()
    )
    return features.replace([np.inf, -np.inf], np.nan)


def _build_panel(
    market_data: dict[str, pd.DataFrame],
    sector_data: dict[str, pd.DataFrame],
) -> tuple[pd.DataFrame, dict[str, pd.Series], pd.Series]:
    market_features, _ = build_features(market_data)
    benchmark = market_data["csi300"]["close"].reindex(market_features.index).ffill()
    rows: list[pd.DataFrame] = []
    closes: dict[str, pd.Series] = {}
    for spec in SECTORS:
        close = sector_data[spec.key]["close"].reindex(market_features.index).ffill()
        closes[spec.key] = close
        own = _sector_features(
            sector_data[spec.key], benchmark, market_features.index
        )
        combined = pd.concat(
            [market_features.add_prefix("m_"), own], axis=1
        ).dropna()
        combined["date"] = combined.index
        combined["sector"] = spec.key
        rows.append(combined.reset_index(drop=True))

    panel = pd.concat(rows, ignore_index=True)
    panel = panel.sort_values(["date", "sector"]).reset_index(drop=True)
    dummies = pd.get_dummies(panel["sector"], prefix="sector", dtype=float)
    panel = pd.concat([panel, dummies], axis=1)
    return panel, closes, benchmark


def _classifiers() -> dict[str, Any]:
    return {
        "logistic": make_pipeline(
            StandardScaler(),
            LogisticRegression(
                C=0.18,
                max_iter=800,
                class_weight="balanced",
                random_state=RANDOM_STATE,
            ),
        ),
        "boost": HistGradientBoostingClassifier(
            max_iter=75,
            max_depth=3,
            learning_rate=0.045,
            l2_regularization=3.0,
            min_samples_leaf=45,
            random_state=RANDOM_STATE,
        ),
    }


def _relative_classifier() -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(
        max_iter=75,
        max_depth=3,
        learning_rate=0.045,
        l2_regularization=3.0,
        min_samples_leaf=45,
        random_state=RANDOM_STATE + 1,
    )


def _regressors() -> tuple[Any, Any]:
    return (
        make_pipeline(StandardScaler(), Ridge(alpha=30.0)),
        HistGradientBoostingRegressor(
            max_iter=75,
            max_depth=3,
            learning_rate=0.04,
            l2_regularization=4.0,
            min_samples_leaf=45,
            loss="absolute_error",
            random_state=RANDOM_STATE,
        ),
    )


def _sector_metrics(
    actual: pd.Series,
    probability: pd.Series,
    dates: pd.Series,
) -> dict[str, float | int | None]:
    order = np.argsort(dates.to_numpy())
    actual = actual.iloc[order]
    probability = probability.iloc[order]
    recent_count = min(120, len(actual))
    recent_actual = actual.iloc[-recent_count:]
    recent_probability = probability.iloc[-recent_count:]
    accuracy = float(
        accuracy_score(recent_actual, recent_probability >= 0.5)
    )
    baseline = float(max(recent_actual.mean(), 1 - recent_actual.mean()))
    try:
        auc = float(roc_auc_score(recent_actual, recent_probability))
    except ValueError:
        auc = 0.5
    confident = (recent_probability >= 0.57) | (recent_probability <= 0.43)
    confident_accuracy = (
        float(
            accuracy_score(
                recent_actual.loc[confident],
                recent_probability.loc[confident] >= 0.5,
            )
        )
        if confident.any()
        else None
    )
    return {
        "accuracy": accuracy,
        "baseline": baseline,
        "edge": accuracy - baseline,
        "auc": auc,
        "brier": float(brier_score_loss(recent_actual, recent_probability)),
        "high_conf_accuracy": confident_accuracy,
        "high_conf_count": int(confident.sum()),
        "samples": int(recent_count),
    }


def _fit_horizon_panel(
    panel: pd.DataFrame,
    closes: dict[str, pd.Series],
    benchmark: pd.Series,
    horizon: int,
) -> dict[str, dict[str, Any]]:
    feature_columns = [
        column
        for column in panel.columns
        if column not in {"date", "sector"}
    ]
    work = panel.copy()
    absolute_target = pd.Series(index=work.index, dtype=float)
    relative_target = pd.Series(index=work.index, dtype=float)
    benchmark_future = benchmark.pct_change().shift(-horizon)
    for spec in SECTORS:
        mask = work["sector"].eq(spec.key)
        dates = pd.DatetimeIndex(work.loc[mask, "date"])
        sector_future = closes[spec.key].pct_change().shift(-horizon)
        absolute_target.loc[mask] = sector_future.reindex(dates).to_numpy()
        relative_target.loc[mask] = (
            sector_future - benchmark_future
        ).reindex(dates).to_numpy()

    valid = absolute_target.notna() & relative_target.notna()
    train = work.loc[valid].copy()
    y_abs = (absolute_target.loc[valid] > 0).astype(int)
    y_rel = (relative_target.loc[valid] > 0).astype(int)
    y_excess = relative_target.loc[valid]
    unique_dates = pd.Index(train["date"].drop_duplicates().sort_values())
    test_size = min(180, max(90, len(unique_dates) // 9))
    splitter = TimeSeriesSplit(
        n_splits=4, test_size=test_size, gap=max(5, horizon)
    )
    oof_abs = pd.Series(index=train.index, dtype=float)
    oof_rel = pd.Series(index=train.index, dtype=float)
    classifiers = _classifiers()

    for train_date_idx, test_date_idx in splitter.split(unique_dates):
        train_dates = unique_dates[train_date_idx]
        test_dates = unique_dates[test_date_idx]
        train_mask = train["date"].isin(train_dates)
        test_mask = train["date"].isin(test_dates)
        X_train = train.loc[train_mask, feature_columns]
        X_test = train.loc[test_mask, feature_columns]
        abs_fold_predictions: list[np.ndarray] = []
        for model in classifiers.values():
            fitted = clone(model).fit(X_train, y_abs.loc[train_mask])
            abs_fold_predictions.append(fitted.predict_proba(X_test)[:, 1])
        oof_abs.loc[train.index[test_mask]] = np.mean(
            abs_fold_predictions, axis=0
        )
        relative = _relative_classifier().fit(X_train, y_rel.loc[train_mask])
        oof_rel.loc[train.index[test_mask]] = relative.predict_proba(X_test)[:, 1]

    current_date = work["date"].max()
    current = work.loc[work["date"].eq(current_date)].copy()
    X_train = train[feature_columns]
    X_current = current[feature_columns]
    live_abs_parts = []
    for model in classifiers.values():
        fitted = clone(model).fit(X_train, y_abs)
        live_abs_parts.append(fitted.predict_proba(X_current)[:, 1])
    live_abs = np.mean(live_abs_parts, axis=0)
    relative = _relative_classifier().fit(X_train, y_rel)
    live_rel = relative.predict_proba(X_current)[:, 1]

    excess_predictions = []
    for model in _regressors():
        fitted = clone(model).fit(X_train, y_excess)
        excess_predictions.append(fitted.predict(X_current))
    live_excess = np.mean(excess_predictions, axis=0)
    cap = float(np.quantile(np.abs(y_excess), 0.95))
    live_excess = np.clip(live_excess, -cap, cap)

    output: dict[str, dict[str, Any]] = {}
    for position, (_, row) in enumerate(current.iterrows()):
        sector = row["sector"]
        sector_mask = train["sector"].eq(sector) & oof_abs.notna()
        abs_metrics = _sector_metrics(
            y_abs.loc[sector_mask],
            oof_abs.loc[sector_mask],
            train.loc[sector_mask, "date"],
        )
        rel_metrics = _sector_metrics(
            y_rel.loc[sector_mask],
            oof_rel.loc[sector_mask],
            train.loc[sector_mask, "date"],
        )
        abs_skill = float(np.clip(float(abs_metrics["edge"]) / 0.05, 0, 1))
        rel_skill = float(np.clip(float(rel_metrics["edge"]) / 0.05, 0, 1))
        abs_probability = 0.5 + (live_abs[position] - 0.5) * (
            0.35 + 0.65 * abs_skill
        )
        rel_probability = 0.5 + (live_rel[position] - 0.5) * (
            0.35 + 0.65 * rel_skill
        )
        output[sector] = {
            "up_probability": float(np.clip(abs_probability, 0.35, 0.65)),
            "outperform_probability": float(
                np.clip(rel_probability, 0.35, 0.65)
            ),
            "expected_excess": float(live_excess[position]),
            "absolute_validation": abs_metrics,
            "relative_validation": rel_metrics,
        }
    return output


def _direction(probability: float, expected: float, quality: bool) -> str:
    if quality and probability >= 0.56 and expected > 0:
        return "偏强"
    if quality and probability <= 0.44 and expected < 0:
        return "偏弱"
    return "震荡"


def _outlook(probability: float, expected: float) -> str:
    if probability >= 0.55 and expected > 0:
        return "相对领先"
    if probability <= 0.45 and expected < 0:
        return "相对落后"
    return "相对中性"


def _drivers(
    spec: SectorSpec,
    frame: pd.DataFrame,
    benchmark: pd.Series,
) -> list[str]:
    close = frame["close"]
    benchmark_close = benchmark.reindex(close.index).ffill()
    momentum = float(close.pct_change(20).iloc[-1])
    relative = float(
        close.pct_change(20).iloc[-1] - benchmark_close.pct_change(20).iloc[-1]
    )
    volatility = float(close.pct_change().rolling(20).std().iloc[-1])
    items = [
        f"20日动量 {momentum * 100:+.1f}%",
        f"20日相对沪深300 {relative * 100:+.1f}%",
        f"20日波动 {volatility * np.sqrt(242) * 100:.1f}%年化",
    ]
    if spec.key == "utilities":
        items.append("公用事业指数同时包含电力等公用板块")
    return items


def _composite_technology(
    sectors: list[dict[str, Any]],
) -> dict[str, Any]:
    members = [
        item for item in sectors
        if item["key"] in {"electronics", "computer", "telecom"}
    ]
    days = []
    for day_index in range(5):
        rows = [item["days"][day_index] for item in members]
        probability = float(np.mean([row["up_probability"] for row in rows]))
        out_probability = float(
            np.mean([row["outperform_probability"] for row in rows])
        )
        expected = float(np.mean([row["expected_return"] for row in rows]))
        excess = float(np.mean([row["expected_excess"] for row in rows]))
        quality = sum(row["confidence"] != "低" for row in rows) >= 2
        days.append(
            {
                "date": rows[0]["date"],
                "weekday": rows[0]["weekday"],
                "direction": _direction(
                    probability / 100, expected / 100, quality
                ),
                "up_probability": round(probability, 1),
                "outperform_probability": round(out_probability, 1),
                "expected_return": round(expected, 2),
                "expected_excess": round(excess, 2),
                "confidence": "中" if quality else "低",
            }
        )
    return {
        "key": "technology",
        "name": "科技综合",
        "code": "电子+计算机+通信",
        "group": "科技",
        "is_composite": True,
        "days": days,
        "drivers": [
            "由申万电子、计算机、通信三个一级行业等权合成",
            "用于判断科技主线，不代表任一只科技股",
        ],
        "invalidation": "若三大子行业中两个以上跌破20日均线，科技综合信号失效。",
        "validation": {
            "is_proxy": True,
            "accuracy": round(
                float(np.mean([item["validation"]["accuracy"] for item in members])),
                1,
            ),
            "baseline": round(
                float(np.mean([item["validation"]["baseline"] for item in members])),
                1,
            ),
            "relative_accuracy": round(
                float(
                    np.mean(
                        [item["validation"]["relative_accuracy"] for item in members]
                    )
                ),
                1,
            ),
            "samples": int(
                min(item["validation"]["samples"] for item in members)
            ),
        },
    }


def generate_sector_forecast(
    market_data: dict[str, pd.DataFrame],
    sector_data: dict[str, pd.DataFrame],
    market_days: list[dict[str, Any]],
) -> dict[str, Any]:
    panel, closes, benchmark = _build_panel(market_data, sector_data)
    horizon_results = {
        horizon: _fit_horizon_panel(
            panel, closes, benchmark, horizon
        )
        for horizon in range(1, 6)
    }
    last_date = panel["date"].max()
    forecast_dates = _next_weekdays(last_date)
    sectors: list[dict[str, Any]] = []

    for spec in SECTORS:
        days = []
        for horizon, (forecast_date, market_day) in enumerate(
            zip(forecast_dates, market_days), start=1
        ):
            result = horizon_results[horizon][spec.key]
            expected = (
                float(market_day["expected_return"]) / 100
                + result["expected_excess"]
            )
            metrics = result["absolute_validation"]
            quality = (
                float(metrics["edge"]) >= 0.015
                and float(metrics["auc"]) >= 0.51
            )
            days.append(
                {
                    "date": forecast_date.strftime("%Y-%m-%d"),
                    "weekday": "一二三四五"[forecast_date.weekday()],
                    "direction": _direction(
                        result["up_probability"], expected, quality
                    ),
                    "up_probability": round(
                        result["up_probability"] * 100, 1
                    ),
                    "outperform_probability": round(
                        result["outperform_probability"] * 100, 1
                    ),
                    "expected_return": round(expected * 100, 2),
                    "expected_excess": round(
                        result["expected_excess"] * 100, 2
                    ),
                    "confidence": "中" if quality else "低",
                }
            )
        h1_abs = horizon_results[1][spec.key]["absolute_validation"]
        h1_rel = horizon_results[1][spec.key]["relative_validation"]
        sectors.append(
            {
                "key": spec.key,
                "name": spec.name,
                "code": spec.code,
                "group": spec.group,
                "is_composite": False,
                "days": days,
                "drivers": _drivers(
                    spec, sector_data[spec.key], benchmark
                ),
                "invalidation": (
                    "若行业收盘跌破20日均线且相对沪深300的5日强弱转负，"
                    "则领先判断失效；反向信号同理。"
                ),
                "validation": {
                    "is_proxy": False,
                    "accuracy": round(float(h1_abs["accuracy"]) * 100, 1),
                    "baseline": round(float(h1_abs["baseline"]) * 100, 1),
                    "auc": round(float(h1_abs["auc"]), 3),
                    "brier": round(float(h1_abs["brier"]), 3),
                    "relative_accuracy": round(
                        float(h1_rel["accuracy"]) * 100, 1
                    ),
                    "relative_baseline": round(
                        float(h1_rel["baseline"]) * 100, 1
                    ),
                    "samples": int(h1_abs["samples"]),
                },
            }
        )

    sectors.insert(0, _composite_technology(sectors))
    for item in sectors:
        weekly_return = float(
            np.prod(
                [1 + day["expected_return"] / 100 for day in item["days"]]
            )
            - 1
        )
        weekly_excess = float(
            np.sum([day["expected_excess"] / 100 for day in item["days"]])
        )
        average_outperformance = float(
            np.mean([day["outperform_probability"] for day in item["days"]])
        )
        item["weekly_expected_return"] = round(weekly_return * 100, 2)
        item["weekly_expected_excess"] = round(weekly_excess * 100, 2)
        item["outperform_probability"] = round(average_outperformance, 1)
        item["weekly_outlook"] = _outlook(
            average_outperformance / 100, weekly_excess
        )
        item["_rank_score"] = (
            weekly_excess * 100 + (average_outperformance - 50) * 0.08
        )

    ordered = sorted(
        sectors, key=lambda item: item["_rank_score"], reverse=True
    )
    for rank, item in enumerate(ordered, start=1):
        item["rank"] = rank
        item.pop("_rank_score", None)

    return {
        "benchmark": "沪深300",
        "data_source": "申万宏源研究·申万一级行业指数",
        "data_through": last_date.strftime("%Y-%m-%d"),
        "method": (
            "行业绝对方向使用逻辑回归与梯度提升的滚动样本外集成；"
            "行业相对强弱使用独立梯度提升模型；收益预测为行业相对"
            "沪深300的回归结果叠加大盘路径。"
        ),
        "sectors": ordered,
    }

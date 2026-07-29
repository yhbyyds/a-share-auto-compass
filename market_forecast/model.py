from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.ensemble import (
    HistGradientBoostingClassifier,
    HistGradientBoostingRegressor,
    RandomForestClassifier,
)
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    brier_score_loss,
    roc_auc_score,
)
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from market_forecast.data import DOMESTIC_KEYS, GLOBAL_KEYS
from market_forecast.trading_calendar import trading_sessions_after


RANDOM_STATE = 20260724
ANALOG_FEATURES = (
    "return_5d",
    "return_20d",
    "return_60d",
    "ma_gap_20",
    "ma_gap_60",
    "volatility_20",
    "rsi_14",
    "volume_z20",
    "drawdown_60",
    "breakout_20d",
    "atr14_pct",
    "trend_efficiency_20",
    "market_dispersion",
    "breadth_proxy",
    "small_vs_large_5d",
    "relative_strength_csi300_20d",
    "global_risk_on_1d",
)


@dataclass
class ModelResult:
    probability: float
    expected_return: float
    low_return: float
    high_return: float
    accuracy: float
    raw_probability: float
    model_probabilities: dict[str, float]
    model_weights: dict[str, float]


def _rsi(series: pd.Series, window: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(window).mean()
    loss = -delta.clip(upper=0).rolling(window).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def build_features(data: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, pd.Series]:
    base = data["sse"].copy()
    close = base["close"]
    features = pd.DataFrame(index=base.index)
    daily_return = close.pct_change()

    for window in (1, 2, 3, 5, 10, 20, 60):
        features[f"return_{window}d"] = close.pct_change(window)
    for window in (5, 10, 20, 60):
        features[f"ma_gap_{window}"] = close / close.rolling(window).mean() - 1
    for window in (5, 10, 20, 60):
        features[f"volatility_{window}"] = daily_return.rolling(window).std()

    features["rsi_14"] = _rsi(close) / 100
    features["range"] = (base["high"] - base["low"]) / base["close"]
    features["close_location"] = (
        (base["close"] - base["low"])
        / (base["high"] - base["low"]).replace(0, np.nan)
        - 0.5
    )
    features["gap"] = base["open"] / base["close"].shift(1) - 1
    log_volume = np.log1p(base["volume"])
    features["volume_z20"] = (
        (log_volume - log_volume.rolling(20).mean())
        / log_volume.rolling(20).std()
    )
    features["amount_change_5"] = base["amount"].pct_change(5)
    features["drawdown_60"] = close / close.rolling(60).max() - 1

    # Research priors translated into leak-free, next-session features:
    # trend/momentum (Livermore/O'Neil/Minervini style), price-volume
    # confirmation, and relative strength.  The rolling extrema are shifted
    # so today's close is compared only with information available before it.
    for window in (20, 60):
        prior_high = close.rolling(window).max().shift(1)
        prior_low = close.rolling(window).min().shift(1)
        features[f"breakout_{window}d"] = close / prior_high - 1
        features[f"rebound_from_low_{window}d"] = close / prior_low - 1
    true_range = pd.concat(
        [
            base["high"] - base["low"],
            (base["high"] - close.shift(1)).abs(),
            (base["low"] - close.shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    features["atr14_pct"] = true_range.rolling(14).mean() / close
    features["trend_efficiency_20"] = (
        close.pct_change(20).abs()
        / daily_return.abs().rolling(20).sum().replace(0, np.nan)
    )
    features["volume_price_confirmation"] = (
        daily_return * features["volume_z20"]
    )

    index_returns: dict[str, pd.Series] = {}
    for key, frame in data.items():
        aligned_close = frame["close"].reindex(features.index).ffill()
        index_returns[key] = aligned_close.pct_change()
        if key != "sse":
            features[f"{key}_return_1d"] = aligned_close.pct_change()
            features[f"{key}_return_5d"] = aligned_close.pct_change(5)
            features[f"{key}_return_20d"] = aligned_close.pct_change(20)
            features[f"{key}_ma_gap_20"] = (
                aligned_close / aligned_close.rolling(20).mean() - 1
            )

    return_frame = pd.DataFrame(
        {key: value for key, value in index_returns.items() if key in DOMESTIC_KEYS}
    )
    features["market_dispersion"] = return_frame.std(axis=1)
    features["breadth_proxy"] = (return_frame > 0).mean(axis=1) - 0.5
    features["small_vs_large_5d"] = (
        data["csi1000"]["close"].reindex(features.index).ffill().pct_change(5)
        - data["csi300"]["close"].reindex(features.index).ffill().pct_change(5)
    )
    csi300_close = data["csi300"]["close"].reindex(features.index).ffill()
    features["relative_strength_csi300_5d"] = (
        close.pct_change(5) - csi300_close.pct_change(5)
    )
    features["relative_strength_csi300_20d"] = (
        close.pct_change(20) - csi300_close.pct_change(20)
    )
    features["weekday_sin"] = np.sin(2 * np.pi * features.index.dayofweek / 5)
    features["weekday_cos"] = np.cos(2 * np.pi * features.index.dayofweek / 5)
    global_returns = pd.DataFrame(
        {key: value for key, value in index_returns.items() if key in GLOBAL_KEYS}
    )
    if not global_returns.empty:
        features["global_risk_on_1d"] = global_returns.mean(axis=1)
        features["global_risk_dispersion"] = global_returns.std(axis=1)

    features = features.replace([np.inf, -np.inf], np.nan).dropna()
    return features, close.reindex(features.index)


def _classifiers() -> dict[str, Any]:
    return {
        "逻辑回归": make_pipeline(
            StandardScaler(),
            LogisticRegression(C=0.25, max_iter=1200, random_state=RANDOM_STATE),
        ),
        "梯度提升": HistGradientBoostingClassifier(
            max_iter=120,
            max_depth=3,
            learning_rate=0.045,
            l2_regularization=2.0,
            min_samples_leaf=25,
            random_state=RANDOM_STATE,
        ),
        "随机森林": RandomForestClassifier(
            n_estimators=140,
            max_depth=6,
            min_samples_leaf=18,
            max_features=0.65,
            class_weight="balanced_subsample",
            n_jobs=-1,
            random_state=RANDOM_STATE,
        ),
    }


def _regressors() -> dict[str, Any]:
    return {
        "ridge": make_pipeline(StandardScaler(), Ridge(alpha=18.0)),
        "boost": HistGradientBoostingRegressor(
            max_iter=110,
            max_depth=3,
            learning_rate=0.04,
            l2_regularization=3.0,
            min_samples_leaf=28,
            loss="absolute_error",
            random_state=RANDOM_STATE,
        ),
    }


def _target_return(close: pd.Series, horizon: int, cumulative: bool) -> pd.Series:
    if cumulative:
        return close.shift(-horizon) / close - 1
    return close.pct_change().shift(-horizon)


def _walk_forward_predictions(
    X: pd.DataFrame, y_binary: pd.Series, models: dict[str, Any]
) -> tuple[pd.DataFrame, dict[str, float]]:
    n_splits = 5
    test_size = min(180, max(80, len(X) // 12))
    splitter = TimeSeriesSplit(n_splits=n_splits, test_size=test_size, gap=5)
    predictions = pd.DataFrame(index=X.index, columns=models.keys(), dtype=float)

    for train_idx, test_idx in splitter.split(X):
        for name, model in models.items():
            fitted = clone(model).fit(X.iloc[train_idx], y_binary.iloc[train_idx])
            predictions.iloc[test_idx, predictions.columns.get_loc(name)] = (
                fitted.predict_proba(X.iloc[test_idx])[:, 1]
            )

    valid = predictions.dropna()
    actual = y_binary.loc[valid.index]
    recent = valid.tail(min(240, len(valid)))
    recent_actual = actual.loc[recent.index]
    weight_scores: dict[str, float] = {}
    for name in valid.columns:
        full_balanced = balanced_accuracy_score(actual, valid[name] >= 0.5)
        recent_balanced = balanced_accuracy_score(
            recent_actual,
            recent[name] >= 0.5,
        )
        probability_quality = float(
            np.clip(
                0.5 + (0.25 - brier_score_loss(actual, valid[name])),
                0.35,
                0.65,
            )
        )
        # Prefer models that remain useful in the recent regime, while keeping
        # a full-history and probability-quality anchor to avoid hot-hand
        # chasing after a short streak.
        weight_scores[name] = float(
            0.35 * full_balanced
            + 0.45 * recent_balanced
            + 0.20 * probability_quality
        )
    return valid, weight_scores


def _weights_from_accuracy(accuracies: dict[str, float]) -> dict[str, float]:
    raw = {name: math.exp(18 * (score - 0.5)) for name, score in accuracies.items()}
    total = sum(raw.values())
    return {name: value / total for name, value in raw.items()}


def _sigmoid_calibrate(
    train_probability: pd.Series,
    train_actual: pd.Series,
    probability: pd.Series | np.ndarray,
) -> np.ndarray:
    clipped = np.clip(train_probability.to_numpy(dtype=float), 1e-4, 1 - 1e-4)
    train_logit = np.log(clipped / (1 - clipped)).reshape(-1, 1)
    target = train_actual.loc[train_probability.index].to_numpy(dtype=int)
    requested = np.asarray(probability, dtype=float)
    requested = np.clip(requested, 1e-4, 1 - 1e-4)
    if len(np.unique(target)) < 2:
        return requested
    calibrator = LogisticRegression(C=1.0, max_iter=1000)
    calibrator.fit(train_logit, target)
    requested_logit = np.log(requested / (1 - requested)).reshape(-1, 1)
    return calibrator.predict_proba(requested_logit)[:, 1]


def _cross_calibrate(
    raw: pd.DataFrame,
    actual: pd.Series,
) -> pd.DataFrame:
    calibrated = pd.DataFrame(index=raw.index, columns=raw.columns, dtype=float)
    splitter = TimeSeriesSplit(n_splits=3)
    for train_idx, test_idx in splitter.split(raw):
        for name in raw.columns:
            calibrated.iloc[test_idx, calibrated.columns.get_loc(name)] = (
                _sigmoid_calibrate(
                    raw[name].iloc[train_idx],
                    actual,
                    raw[name].iloc[test_idx],
                )
            )
    return calibrated.dropna()


def _nearest_analogs(
    X: pd.DataFrame, target: pd.Series, neighbors: int = 80
) -> np.ndarray:
    columns = [column for column in ANALOG_FEATURES if column in X.columns]
    history = X.loc[:, columns].iloc[:-5]
    target = target.reindex(history.index)
    valid = target.notna()
    history = history.loc[valid]
    target = target.loc[valid]
    means = history.mean()
    scales = history.std().replace(0, 1)
    standardized = (history - means) / scales
    current = (X.loc[:, columns].iloc[-1] - means) / scales
    distances = ((standardized - current) ** 2).mean(axis=1)
    # A mild age penalty breaks near-ties in favour of more recent market
    # structures without discarding genuinely close older analogues.
    distances = distances * np.linspace(1.08, 1.0, len(distances))
    selected = distances.nsmallest(min(neighbors, len(distances))).index
    return target.loc[selected].to_numpy()


def fit_horizon(
    X: pd.DataFrame,
    close: pd.Series,
    horizon: int,
    cumulative: bool = False,
) -> tuple[ModelResult, dict[str, Any]]:
    continuous = _target_return(close, horizon, cumulative).reindex(X.index)
    valid = continuous.notna()
    train_X = X.loc[valid]
    target = continuous.loc[valid]
    binary = (target > 0).astype(int)

    classifiers = _classifiers()
    oof, accuracies = _walk_forward_predictions(train_X, binary, classifiers)
    weights = _weights_from_accuracy(accuracies)
    raw_model_probs: dict[str, float] = {}
    calibrated_model_probs: dict[str, float] = {}
    actual_raw_oof = binary.loc[oof.index]
    for name, model in classifiers.items():
        fitted = clone(model).fit(train_X, binary)
        raw_live = float(fitted.predict_proba(X.iloc[[-1]])[0, 1])
        raw_model_probs[name] = raw_live
        calibrated_model_probs[name] = float(
            _sigmoid_calibrate(
                oof[name],
                actual_raw_oof,
                np.array([raw_live]),
            )[0]
        )

    calibrated_oof = _cross_calibrate(oof, actual_raw_oof)
    actual_oof = binary.loc[calibrated_oof.index]
    raw_ensemble_oof = oof.loc[calibrated_oof.index].mean(axis=1)
    calibrated_ensemble_oof = calibrated_oof.mean(axis=1)
    raw_brier = float(brier_score_loss(actual_oof, raw_ensemble_oof))
    calibrated_brier = float(
        brier_score_loss(actual_oof, calibrated_ensemble_oof)
    )
    calibration_applied = calibrated_brier < raw_brier - 0.001
    if calibration_applied:
        model_probs = calibrated_model_probs
        ensemble_oof = calibrated_ensemble_oof
    else:
        model_probs = raw_model_probs
        ensemble_oof = raw_ensemble_oof
    raw_probability = sum(
        model_probs[name] * weights[name] for name in weights
    )
    ensemble_accuracy = float(accuracy_score(actual_oof, ensemble_oof >= 0.5))
    baseline_accuracy = float(max(actual_oof.mean(), 1 - actual_oof.mean()))
    # Directional accuracy must beat the majority-class baseline, not merely
    # 50%, before the live probability is allowed to move farther from neutral.
    skill = float(
        np.clip((ensemble_accuracy - baseline_accuracy) / 0.05, 0, 1)
    )
    live_model_spread = float(np.std(list(model_probs.values())))
    agreement_factor = float(
        np.clip(1 - live_model_spread / 0.12, 0.45, 1.0)
    )
    probability = (
        0.5
        + (raw_probability - 0.5)
        * (0.45 + 0.55 * skill)
        * agreement_factor
    )
    probability = float(np.clip(probability, 0.35, 0.65))

    regression_predictions: list[float] = []
    for model in _regressors().values():
        fitted = clone(model).fit(train_X, target)
        regression_predictions.append(float(fitted.predict(X.iloc[[-1]])[0]))
    analogs = _nearest_analogs(X, continuous)
    analog_median = float(np.median(analogs))
    expected_return = float(
        0.35 * regression_predictions[0]
        + 0.30 * regression_predictions[1]
        + 0.35 * analog_median
    )
    cap = float(np.quantile(np.abs(target), 0.94))
    expected_return = float(np.clip(expected_return, -cap, cap))

    residual_scale = float(np.std(analogs))
    low = float(np.quantile(analogs, 0.2))
    high = float(np.quantile(analogs, 0.8))
    low = min(low, expected_return - 0.35 * residual_scale)
    high = max(high, expected_return + 0.35 * residual_scale)

    try:
        auc = float(roc_auc_score(actual_oof, ensemble_oof))
    except ValueError:
        auc = 0.5
    diagnostics = {
        "baseline_accuracy": baseline_accuracy,
        "brier": float(brier_score_loss(actual_oof, ensemble_oof)),
        "raw_brier": raw_brier,
        "calibrated_brier": calibrated_brier,
        "calibration_applied": calibration_applied,
        "auc": auc,
        "model_weight_scores": accuracies,
        "live_model_spread": live_model_spread,
        "agreement_factor": agreement_factor,
        "oof_count": int(len(oof)),
        "oof_probability": ensemble_oof,
        "oof_actual": actual_oof,
        "calibration": (
            "expanding-window sigmoid applied"
            if calibration_applied
            else "expanding-window sigmoid rejected"
        ),
    }
    confidence_mask = (ensemble_oof >= 0.57) | (ensemble_oof <= 0.43)
    if confidence_mask.any():
        diagnostics["high_conf_accuracy"] = float(
            accuracy_score(
                actual_oof.loc[confidence_mask],
                ensemble_oof.loc[confidence_mask] >= 0.5,
            )
        )
        diagnostics["high_conf_count"] = int(confidence_mask.sum())
    else:
        diagnostics["high_conf_accuracy"] = None
        diagnostics["high_conf_count"] = 0
    recent_index = ensemble_oof.index[-min(120, len(ensemble_oof)) :]
    recent_actual = actual_oof.loc[recent_index]
    diagnostics["recent_accuracy"] = float(
        accuracy_score(
            recent_actual,
            ensemble_oof.loc[recent_index] >= 0.5,
        )
    )
    diagnostics["recent_baseline"] = float(
        max(recent_actual.mean(), 1 - recent_actual.mean())
    )
    return (
        ModelResult(
            probability=probability,
            expected_return=expected_return,
            low_return=low,
            high_return=high,
            accuracy=ensemble_accuracy,
            raw_probability=raw_probability,
            model_probabilities=model_probs,
            model_weights=weights,
        ),
        diagnostics,
    )


def _max_drawdown(returns: pd.Series) -> float:
    equity = (1 + returns).cumprod()
    return float((equity / equity.cummax() - 1).min())


def _backtest(diagnostics: dict[str, Any], close: pd.Series) -> dict[str, float]:
    probabilities: pd.Series = diagnostics["oof_probability"]
    actual_returns = close.pct_change().reindex(probabilities.index)
    exposure = pd.Series(
        np.where(probabilities >= 0.55, 1.0, np.where(probabilities <= 0.45, 0.0, 0.5)),
        index=probabilities.index,
    )
    turnover = exposure.diff().abs().fillna(exposure.iloc[0])
    strategy = exposure.shift(1).fillna(0) * actual_returns - turnover * 0.0003
    benchmark = actual_returns.fillna(0)
    years = max(len(strategy) / 242, 1 / 242)
    annual_return = float((1 + strategy).prod() ** (1 / years) - 1)
    benchmark_return = float((1 + benchmark).prod() ** (1 / years) - 1)
    return {
        "annual_return": annual_return,
        "benchmark_annual_return": benchmark_return,
        "max_drawdown": _max_drawdown(strategy),
        "benchmark_max_drawdown": _max_drawdown(benchmark),
        "active_days": float((exposure > 0).mean()),
    }


def _next_weekdays(last_date: pd.Timestamp, count: int = 5) -> list[pd.Timestamp]:
    return trading_sessions_after(last_date, count)


def _pct(value: float) -> float:
    return round(value * 100, 2)


def _direction(probability: float, expected: float) -> str:
    if probability >= 0.56 and expected > 0:
        return "偏强"
    if probability <= 0.44 and expected < 0:
        return "偏弱"
    return "震荡"


def _evidence_score(
    diagnostics: dict[str, Any],
    model_probabilities: dict[str, float],
) -> dict[str, Any]:
    """Summarize validation strength without changing the model probability."""
    recent_edge = float(
        diagnostics["recent_accuracy"] - diagnostics["recent_baseline"]
    )
    high_conf_accuracy = diagnostics.get("high_conf_accuracy")
    spread = float(np.std(list(model_probabilities.values())))
    components = {
        "recent_edge": float(np.clip(recent_edge / 0.05, 0, 1)),
        "auc": float(np.clip((float(diagnostics["auc"]) - 0.5) / 0.08, 0, 1)),
        "brier": float(
            np.clip((0.26 - float(diagnostics["brier"])) / 0.06, 0, 1)
        ),
        "high_confidence": float(
            np.clip(
                (
                    (float(high_conf_accuracy) - 0.5) / 0.08
                    if high_conf_accuracy is not None
                    else 0
                ),
                0,
                1,
            )
        ),
        "agreement": float(np.clip(1 - spread / 0.12, 0, 1)),
    }
    score = round(
        100
        * (
            0.30 * components["recent_edge"]
            + 0.20 * components["auc"]
            + 0.20 * components["brier"]
            + 0.20 * components["high_confidence"]
            + 0.10 * components["agreement"]
        ),
        1,
    )
    label = "较高" if score >= 70 else "中等" if score >= 50 else "较低"
    reasons = [
        f"近期相对基线 {recent_edge * 100:+.1f}pp",
        f"AUC {float(diagnostics['auc']):.3f}",
        f"Brier {float(diagnostics['brier']):.3f}",
        f"模型分歧 {spread * 100:.1f}pp",
    ]
    return {
        "score": score,
        "label": label,
        "components": components,
        "reasons": reasons,
    }


def _drivers(features: pd.DataFrame) -> list[dict[str, str]]:
    row = features.iloc[-1]
    output: list[dict[str, str]] = []
    momentum = row["return_20d"]
    output.append(
        {
            "name": "20日动量",
            "value": f"{momentum * 100:+.2f}%",
            "impact": "positive" if momentum > 0 else "negative",
            "detail": "中期趋势仍在上方" if momentum > 0 else "中期趋势转弱",
        }
    )
    gap = row["ma_gap_20"]
    output.append(
        {
            "name": "均线位置",
            "value": f"{gap * 100:+.2f}%",
            "impact": "positive" if gap > 0 else "negative",
            "detail": "收盘位于20日均线上方" if gap > 0 else "收盘跌至20日均线下方",
        }
    )
    volume = row["volume_z20"]
    output.append(
        {
            "name": "成交活跃度",
            "value": f"{volume:+.2f}σ",
            "impact": "neutral" if abs(volume) < 0.7 else ("positive" if volume > 0 else "negative"),
            "detail": "量能接近常态" if abs(volume) < 0.7 else ("量能明显放大" if volume > 0 else "量能明显收缩"),
        }
    )
    style = row["small_vs_large_5d"]
    output.append(
        {
            "name": "大小盘风格",
            "value": f"{style * 100:+.2f}%",
            "impact": "positive" if style > 0 else "negative",
            "detail": "中小盘相对占优" if style > 0 else "大盘权重相对占优",
        }
    )
    return output


def generate_forecast(
    data: dict[str, pd.DataFrame],
    breadth: dict[str, Any] | None = None,
    watchlist: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    features, close = build_features(data)
    last_date = features.index[-1]
    last_close = float(close.iloc[-1])
    day_results: list[ModelResult] = []
    day_diagnostics: list[dict[str, Any]] = []
    for horizon in range(1, 6):
        result, diagnostics = fit_horizon(features, close, horizon, cumulative=False)
        day_results.append(result)
        day_diagnostics.append(diagnostics)

    weekly, weekly_diagnostics = fit_horizon(
        features, close, horizon=5, cumulative=True
    )
    forecast_dates = _next_weekdays(last_date)
    path = last_close
    days: list[dict[str, Any]] = []
    for date_value, result in zip(forecast_dates, day_results):
        diagnostics = day_diagnostics[len(days)]
        high_conf_accuracy = diagnostics.get("high_conf_accuracy")
        evidence = _evidence_score(
            diagnostics,
            result.model_probabilities,
        )
        quality_ok = (
            diagnostics["recent_accuracy"]
            >= diagnostics["recent_baseline"] + 0.02
            and high_conf_accuracy is not None
            and high_conf_accuracy >= 0.54
            and diagnostics["auc"] >= 0.51
            and diagnostics["brier"] <= 0.255
            and evidence["score"] >= 50
        )
        direction = (
            _direction(result.probability, result.expected_return)
            if quality_ok
            else "震荡"
        )
        path *= 1 + result.expected_return
        days.append(
            {
                "date": date_value.strftime("%Y-%m-%d"),
                "weekday": "一二三四五"[date_value.weekday()],
                "direction": direction,
                "up_probability": round(result.probability * 100, 1),
                "expected_return": _pct(result.expected_return),
                "low_return": _pct(result.low_return),
                "high_return": _pct(result.high_return),
                "path_close": round(path, 2),
                "confidence": (
                    "中"
                    if quality_ok and abs(result.probability - 0.5) >= 0.07
                    else "低"
                ),
                "model_spread": round(
                    np.std(list(result.model_probabilities.values())) * 100, 1
                ),
                "validation_accuracy": round(
                    diagnostics["recent_accuracy"] * 100, 1
                ),
                "validation_edge": round(
                    (
                        diagnostics["recent_accuracy"]
                        - diagnostics["recent_baseline"]
                    )
                    * 100,
                    1,
                ),
                "evidence_score": evidence["score"],
                "evidence_label": evidence["label"],
                "evidence_reasons": evidence["reasons"],
            }
        )

    total_expected = path / last_close - 1
    weekly_direction = _direction(weekly.probability, total_expected)
    validation_degraded = (
        weekly.accuracy <= weekly_diagnostics["baseline_accuracy"]
        or weekly_diagnostics["brier"] >= 0.25
    )
    breadth_stocks = int((breadth or {}).get("stocks", 0) or 0)
    breadth_degraded = breadth_stocks < 4000
    if breadth_degraded or validation_degraded:
        weekly_direction = "震荡"
        for day in days:
            day["direction"] = "震荡"
            day["confidence"] = "低"
    h1_backtest = _backtest(day_diagnostics[0], close)
    recent = data["sse"].loc[:last_date].tail(90)
    recent_chart = [
        {"date": idx.strftime("%Y-%m-%d"), "close": round(float(value), 2)}
        for idx, value in recent["close"].items()
    ]
    model_details = []
    for name, probability in weekly.model_probabilities.items():
        model_details.append(
            {
                "name": name,
                "probability": round(probability * 100, 1),
                "weight": round(weekly.model_weights[name] * 100, 1),
            }
        )
    weekly_evidence = _evidence_score(
        weekly_diagnostics,
        weekly.model_probabilities,
    )

    return {
        "meta": {
            "generated_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
            "data_through": last_date.strftime("%Y-%m-%d"),
            "forecast_window": f"{forecast_dates[0]:%Y-%m-%d} 至 {forecast_dates[-1]:%Y-%m-%d}",
            "version": "1.0.0",
            "data_source": "腾讯证券主源 / 东方财富备用",
        },
        "market": {
            "name": "上证指数",
            "last_close": round(last_close, 2),
            "last_change": round(float(data["sse"].loc[last_date, "pct"]), 2),
            "weekly_direction": weekly_direction,
            "weekly_up_probability": round(weekly.probability * 100, 1),
            "weekly_expected_return": _pct(total_expected),
            "weekly_range": [
                _pct(weekly.low_return),
                _pct(weekly.high_return),
            ],
            "risk_level": "较高" if features.iloc[-1]["volatility_20"] > features["volatility_20"].quantile(0.7) else "中等",
            "breadth_guard": (
                "degraded" if breadth_degraded else "healthy"
            ),
            "validation_guard": (
                "degraded" if validation_degraded else "healthy"
            ),
        },
        "days": days,
        "drivers": _drivers(features),
        "breadth": breadth or {},
        "models": model_details,
        "horizon_validation": [
            {
                "horizon": horizon,
                "label": f"第{horizon}个交易日",
                "accuracy": round(day_results[horizon - 1].accuracy * 100, 1),
                "recent_accuracy": round(
                    day_diagnostics[horizon - 1]["recent_accuracy"] * 100, 1
                ),
                "baseline": round(
                    day_diagnostics[horizon - 1]["recent_baseline"] * 100, 1
                ),
                "full_baseline": round(
                    day_diagnostics[horizon - 1]["baseline_accuracy"] * 100, 1
                ),
                "auc": round(day_diagnostics[horizon - 1]["auc"], 3),
                "brier": round(day_diagnostics[horizon - 1]["brier"], 3),
                "raw_brier": round(
                    day_diagnostics[horizon - 1]["raw_brier"], 3
                ),
                "calibrated_brier": round(
                    day_diagnostics[horizon - 1]["calibrated_brier"], 3
                ),
                "calibration_applied": day_diagnostics[horizon - 1][
                    "calibration_applied"
                ],
                "high_conf_accuracy": (
                    round(
                        day_diagnostics[horizon - 1]["high_conf_accuracy"] * 100,
                        1,
                    )
                    if day_diagnostics[horizon - 1]["high_conf_accuracy"]
                    is not None
                    else None
                ),
                "high_conf_coverage": round(
                    day_diagnostics[horizon - 1]["high_conf_count"]
                    / day_diagnostics[horizon - 1]["oof_count"]
                    * 100,
                    1,
                ),
            }
            for horizon in range(1, 6)
        ],
        "validation": {
            "daily_direction_accuracy": round(day_results[0].accuracy * 100, 1),
            "weekly_direction_accuracy": round(weekly.accuracy * 100, 1),
            "baseline_accuracy": round(
                weekly_diagnostics["baseline_accuracy"] * 100, 1
            ),
            "auc": round(weekly_diagnostics["auc"], 3),
            "brier": round(weekly_diagnostics["brier"], 3),
            "raw_brier": round(weekly_diagnostics["raw_brier"], 3),
            "calibrated_brier": round(
                weekly_diagnostics["calibrated_brier"], 3
            ),
            "calibration_applied": weekly_diagnostics[
                "calibration_applied"
            ],
            "samples": weekly_diagnostics["oof_count"],
            "strategy_annual_return": round(h1_backtest["annual_return"] * 100, 1),
            "strategy_max_drawdown": round(h1_backtest["max_drawdown"] * 100, 1),
            "benchmark_annual_return": round(
                h1_backtest["benchmark_annual_return"] * 100, 1
            ),
            "benchmark_max_drawdown": round(
                h1_backtest["benchmark_max_drawdown"] * 100, 1
            ),
            "active_days": round(h1_backtest["active_days"] * 100, 1),
            "reliability": weekly_evidence,
            "calibration": "扩展窗口样本外预测 + 时间顺序Sigmoid校准",
            "method": "5折扩展窗口时序验证；独立时间段概率校准；预测与实盘收益错开一日；单边成本3bp",
        },
        "recent_chart": recent_chart,
        "watchlist": watchlist or [],
        "events": [
            {
                "date": forecast_dates[2].strftime("%m-%d"),
                "title": "美联储议息会议",
                "risk": "高",
                "detail": "FOMC 7月28–29日会议，结果在北京时间周四凌晨传导。",
                "url": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
            },
            {
                "date": forecast_dates[3].strftime("%m-%d"),
                "title": "美国二季度GDP / PCE",
                "risk": "高",
                "detail": "美国BEA于7月30日发布初值，可能影响全球风险偏好。",
                "url": "https://www.bea.gov/news/schedule",
            },
            {
                "date": forecast_dates[4].strftime("%m-%d"),
                "title": "中国官方PMI",
                "risk": "高",
                "detail": "7月采购经理指数月度报告，关注内需与制造业景气。",
                "url": "https://www.stats.gov.cn/xw/tjxw/tzgg/202512/t20251224_1962137.html",
            },
        ],
        "playbook": {
            "base": "只用宽基指数工具，分批而非一次性押注；不使用杠杆。",
            "bull": "若沪指重新站回20日均线且两市量能同步放大，可将宽基仓位提高10%–15%。",
            "bear": "若放量跌破本周低点，模型信号视为失效，优先降仓并保留现金。",
            "neutral": "当上涨概率处于45%–55%，保持观望，等待方向确认。",
        },
        "risk_guard": {
            "capital_rule": "只使用即使全部亏损也不影响生活的闲置资金；禁止借贷、融资和杠杆。",
            "position_cap": "模型置信度为低时建议0%–20%观察仓；中等时宽基总仓位也不超过50%。",
            "loss_rule": "单周账户回撤达到2%即停止新增交易，重新评估；不要用加仓摊薄代替止损。",
            "human_rule": "若交易结果与生命安全、房租、债务或基本生活绑定，系统自动建议停止交易并寻求现实支持。",
        },
        "sources": [
            {
                "name": "Microsoft Qlib",
                "detail": "参考其数据—训练—回测完整研究流程",
                "url": "https://github.com/microsoft/qlib",
            },
            {
                "name": "Darts",
                "detail": "参考其模型集成与历史回测方法",
                "url": "https://github.com/unit8co/darts",
            },
            {
                "name": "AKShare",
                "detail": "开源中文财经数据生态参考",
                "url": "https://github.com/akfamily/akshare",
            },
            {
                "name": "RQAlpha",
                "detail": "事件驱动回测、交易成本与组合评估工程参考",
                "url": "https://github.com/ricequant/rqalpha",
            },
            {
                "name": "FinRL",
                "detail": "强化学习研究路线参考，当前主模型仍采用监督学习",
                "url": "https://github.com/AI4Finance-Foundation/FinRL",
            },
            {
                "name": "VeighNa vn.py",
                "detail": "因子研究与实盘风控分层参考",
                "url": "https://github.com/vnpy/vnpy",
            },
        ],
        "disclaimer": "本系统是研究与风险管理工具，不构成投资建议。样本外回测不等于未来表现，交易成本、滑点、政策与突发事件均可能使预测失效；不存在可承诺的稳定盈利模型。",
    }

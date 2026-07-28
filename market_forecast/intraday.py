"""Intraday data collection and evidence-gated micro-theme research.

This module deliberately separates *collection* from *prediction*.  A daily
model cannot honestly be presented as a 30-minute model.  We first preserve
timestamped intraday observations, settle them against the same-day close, and
only enable a classifier after enough independent trading sessions exist.
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from market_forecast.data import _session, fetch_market_breadth


BEIJING = ZoneInfo("Asia/Shanghai")
SNAPSHOT_FILE = Path("data/intraday/snapshots.json")
MIN_TRAINED_SESSIONS = 60
MIN_TRAINED_SAMPLES = 240
CONCEPT_URL = "https://push2.eastmoney.com/api/qt/clist/get"
QUOTE_URL = "https://qt.gtimg.cn/q=sh000001,sh000300,sz399006"

# This is a coverage taxonomy, not a stock recommendation list.  A theme can
# be observed immediately, but receives a direction prediction only after it
# accumulates enough labelled intraday sessions.
MICRO_THEMES: tuple[dict[str, str], ...] = (
    {"key": "chip_equipment", "name": "半导体设备", "parent": "electronics", "keywords": "半导体设备,光刻机,刻蚀,芯片设备"},
    {"key": "memory", "name": "存储芯片", "parent": "electronics", "keywords": "存储芯片,DRAM,HBM,存储"},
    {"key": "cpo_optical", "name": "CPO与光模块", "parent": "telecom", "keywords": "CPO,光模块,光通信,光纤"},
    {"key": "ai_compute", "name": "AI算力", "parent": "computer", "keywords": "算力,人工智能,服务器,AIGC"},
    {"key": "robotics", "name": "人形机器人", "parent": "computer", "keywords": "人形机器人,机器人,减速器"},
    {"key": "data_center_power", "name": "数据中心电源", "parent": "power_equipment", "keywords": "数据中心电源,液冷,UPS,电源设备"},
    {"key": "grid", "name": "智能电网", "parent": "power_equipment", "keywords": "智能电网,特高压,电网设备"},
    {"key": "energy_storage", "name": "储能", "parent": "power_equipment", "keywords": "储能,钠离子电池,固态电池"},
    {"key": "thermal_power", "name": "火电", "parent": "utilities", "keywords": "火电,电力"},
    {"key": "power_reform", "name": "电力改革", "parent": "utilities", "keywords": "电力改革,虚拟电厂,绿电"},
    {"key": "innovative_drug", "name": "创新药", "parent": "healthcare", "keywords": "创新药,生物医药,减肥药"},
    {"key": "medical_service", "name": "医疗服务", "parent": "healthcare", "keywords": "医疗服务,医疗器械,CRO"},
    {"key": "military_electronics", "name": "军工电子", "parent": "defense", "keywords": "军工电子,军工信息化,卫星"},
    {"key": "commercial_aerospace", "name": "商业航天", "parent": "defense", "keywords": "商业航天,航天,卫星互联网"},
    {"key": "copper", "name": "铜", "parent": "nonferrous", "keywords": "铜,有色金属"},
    {"key": "securities", "name": "证券", "parent": "nonbank", "keywords": "证券,金融科技"},
    {"key": "consumer_electronics", "name": "消费电子", "parent": "electronics", "keywords": "消费电子,苹果概念,VR"},
    {"key": "automotive_parts", "name": "汽车零部件", "parent": "auto", "keywords": "汽车零部件,智能驾驶,无人驾驶"},
)


def _bucket(now: datetime) -> str:
    """Use fixed, non-overlapping collection buckets for independent samples."""
    minute = now.hour * 60 + now.minute
    points = ((575, "09:35"), (600, "10:00"), (630, "10:30"),
              (660, "11:00"), (810, "13:30"), (840, "14:00"),
              (870, "14:30"), (890, "14:50"))
    passed = [label for edge, label in points if minute >= edge]
    return passed[-1] if passed else "before_open"


def _quote_snapshot() -> dict[str, dict[str, float]]:
    response = _session().get(QUOTE_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
    response.raise_for_status()
    result: dict[str, dict[str, float]] = {}
    keys = ("sse", "csi300", "chinext")
    for key, line in zip(keys, response.text.splitlines()):
        fields = line.split('"')[1].split("~")
        result[key] = {"price": float(fields[3]), "change": float(fields[32])}
    return result


def _concept_rows() -> list[dict[str, Any]]:
    params = {
        "pn": "1", "pz": "100", "po": "1", "np": "1", "fltt": "2", "invt": "2",
        "fid": "f3", "fs": "m:90+t:3+f:!50", "fields": "f12,f14,f3,f6",
    }
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
    rows: list[dict[str, Any]] = []
    total = 100
    page = 1
    while len(rows) < total and page <= 8:
        response = _session().get(CONCEPT_URL, params={**params, "pn": str(page)}, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json().get("data") or {}
        total = int(data.get("total") or 0)
        batch = data.get("diff") or []
        if not batch:
            break
        rows.extend(batch)
        page += 1
    return rows


def fetch_theme_close_changes() -> dict[str, float]:
    """Return latest concept-board changes for per-theme label settlement."""
    try:
        return {
            str(row.get("f14", "")): float(row.get("f3") or 0)
            for row in _concept_rows()
            if row.get("f14")
        }
    except (requests.RequestException, ValueError, TypeError):
        return {}


def _match_themes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matched: list[dict[str, Any]] = []
    for theme in MICRO_THEMES:
        keywords = [item.lower() for item in theme["keywords"].split(",")]
        candidates = [row for row in rows if any(word in str(row.get("f14", "")).lower() for word in keywords)]
        if not candidates:
            continue
        # Use the most liquid matching board instead of cherry-picking the
        # highest return.  This keeps the snapshot reproducible.
        row = max(candidates, key=lambda item: float(item.get("f6") or 0))
        matched.append({
            **{key: theme[key] for key in ("key", "name", "parent")},
            "board": str(row.get("f14", "")),
            "change": round(float(row.get("f3") or 0), 2),
            "amount": float(row.get("f6") or 0),
        })
    return matched


def collect_intraday_snapshot(path: str | Path = SNAPSHOT_FILE, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(BEIJING)
    snapshot = {
        "timestamp": now.isoformat(), "date": now.date().isoformat(), "bucket": _bucket(now),
        "quotes": _quote_snapshot(), "breadth": fetch_market_breadth(), "themes": _match_themes(_concept_rows()),
        "source": "腾讯指数快照 / 东方财富概念板块快照", "label": None,
    }
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {"schema": 1, "snapshots": []}
    snapshots = [row for row in payload.get("snapshots", []) if not (row.get("date") == snapshot["date"] and row.get("bucket") == snapshot["bucket"])]
    snapshots.append(snapshot)
    payload["snapshots"] = sorted(snapshots, key=lambda item: item["timestamp"])[-5000:]
    file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return snapshot


def settle_intraday_labels(
    close_by_date: pd.Series,
    theme_close_changes: dict[str, float] | None = None,
    path: str | Path = SNAPSHOT_FILE,
) -> int:
    """Label market and per-theme remaining same-session movement."""
    # Preserve the old two-positional-argument API where the second argument
    # was the snapshot path.
    if isinstance(theme_close_changes, (str, Path)):
        path = theme_close_changes
        theme_close_changes = None
    file_path = Path(path)
    if not file_path.exists():
        return 0
    payload = json.loads(file_path.read_text(encoding="utf-8"))
    updated = 0
    for row in payload.get("snapshots", []):
        if row.get("label") is not None or row.get("date") not in close_by_date.index:
            continue
        spot = float(row.get("quotes", {}).get("sse", {}).get("price") or 0)
        close = float(close_by_date.loc[row["date"]])
        if spot > 0:
            label = {
                "remaining_return": round((close / spot - 1) * 100, 4),
                "up": bool(close >= spot),
                "themes": {},
            }
            for theme in row.get("themes", []):
                board = str(theme.get("board", ""))
                if board in (theme_close_changes or {}):
                    final_change = float(theme_close_changes[board])
                    delta = final_change - float(theme.get("change") or 0)
                    label["themes"][theme["key"]] = {
                        "remaining_change": round(delta, 4),
                        "up": bool(delta >= 0),
                    }
            row["label"] = label
            updated += 1
    file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return updated


def _features(rows: list[dict[str, Any]]) -> tuple[pd.DataFrame, pd.Series]:
    data = []
    target = []
    for row in rows:
        breadth = row.get("breadth", {})
        quotes = row.get("quotes", {})
        themes = row.get("themes", [])
        changes = [float(item.get("change", 0)) for item in themes]
        if not row.get("label"):
            continue
        data.append({
            "sse": float(quotes.get("sse", {}).get("change", 0)),
            "csi300": float(quotes.get("csi300", {}).get("change", 0)),
            "chinext": float(quotes.get("chinext", {}).get("change", 0)),
            "advance_ratio": float(breadth.get("advance_ratio", 50)),
            "median_change": float(breadth.get("median_change", 0)),
            "limit_spread": float(breadth.get("limit_up_proxy", 0)) - float(breadth.get("limit_down_proxy", 0)),
            "theme_mean": float(np.mean(changes)) if changes else 0.0,
            "theme_dispersion": float(np.std(changes)) if changes else 0.0,
        })
        target.append(int(bool(row["label"].get("up"))))
    return pd.DataFrame(data), pd.Series(target, dtype=int)


def intraday_research_status(path: str | Path = SNAPSHOT_FILE) -> dict[str, Any]:
    file_path = Path(path)
    try:
        rows = json.loads(file_path.read_text(encoding="utf-8")).get("snapshots", [])
    except (OSError, json.JSONDecodeError):
        rows = []
    labelled = [row for row in rows if row.get("label") is not None]
    sessions = len({row.get("date") for row in labelled})
    status = "ready" if sessions >= MIN_TRAINED_SESSIONS and len(labelled) >= MIN_TRAINED_SAMPLES else "collecting"
    output: dict[str, Any] = {
        "status": status, "snapshot_count": len(rows), "labelled_samples": len(labelled),
        "labelled_sessions": sessions, "minimum_sessions": MIN_TRAINED_SESSIONS,
        "minimum_samples": MIN_TRAINED_SAMPLES,
        "target": "各固定时点至当日收盘的上证方向",
        "method": "固定时点快照、收盘后结算标签、按日期前推验证；未满样本不输出盘中方向。",
    }
    if status != "ready":
        output["reason"] = f"已结算 {sessions}/{MIN_TRAINED_SESSIONS} 个交易日、{len(labelled)}/{MIN_TRAINED_SAMPLES} 个样本；当前只展示细分领域热度，不生成伪精确盘中预测。"
        return output
    x, y = _features(labelled)
    split = max(int(len(x) * 0.7), 1)
    model = make_pipeline(StandardScaler(), LogisticRegression(C=0.3, max_iter=600, class_weight="balanced"))
    model.fit(x.iloc[:split], y.iloc[:split])
    probability = model.predict_proba(x.iloc[split:])[:, 1]
    output.update({
        "validation_samples": int(len(y) - split),
        "validation_accuracy": round(float(accuracy_score(y.iloc[split:], probability >= 0.5)) * 100, 1),
        "brier": round(float(brier_score_loss(y.iloc[split:], probability)), 3),
    })
    return output


def micro_theme_training_status(path: str | Path = SNAPSHOT_FILE) -> dict[str, dict[str, Any]]:
    """Summarize independently labelled coverage for every micro-theme."""
    file_path = Path(path)
    try:
        rows = json.loads(file_path.read_text(encoding="utf-8")).get("snapshots", [])
    except (OSError, json.JSONDecodeError):
        rows = []
    output: dict[str, dict[str, Any]] = {}
    for theme in MICRO_THEMES:
        labelled = [
            row for row in rows
            if (row.get("label") or {}).get("themes", {}).get(theme["key"]) is not None
        ]
        sessions = len({row.get("date") for row in labelled})
        output[theme["key"]] = {
            "status": (
                "ready"
                if sessions >= MIN_TRAINED_SESSIONS
                and len(labelled) >= MIN_TRAINED_SAMPLES
                else "collecting"
            ),
            "labelled_samples": len(labelled),
            "labelled_sessions": sessions,
            "minimum_sessions": MIN_TRAINED_SESSIONS,
            "minimum_samples": MIN_TRAINED_SAMPLES,
        }
    return output


def _market_regime(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    """Classify the live tape before allowing long micro-theme candidates."""
    snapshot = snapshot or {}
    quotes = snapshot.get("quotes") or {}
    breadth = snapshot.get("breadth") or {}
    csi300 = float((quotes.get("csi300") or {}).get("change") or 0)
    chinext = float((quotes.get("chinext") or {}).get("change") or 0)
    advance_ratio = float(breadth.get("advance_ratio") or 50)
    median_change = float(breadth.get("median_change") or 0)
    flags: list[str] = []
    if csi300 <= -1.5:
        flags.append("沪深300跌幅超过1.5%")
    if chinext <= -3.0:
        flags.append("创业板跌幅超过3%")
    if advance_ratio < 45.0:
        flags.append("上涨比例低于45%")
    if median_change <= -0.1:
        flags.append("个股中位数不强")
    risk_off = len(flags) >= 2
    return {
        "key": "risk_off" if risk_off else "normal",
        "label": "风险市况" if risk_off else "常态市况",
        "risk_score": len(flags),
        "flags": flags,
        "benchmark_change": round(csi300, 2),
        "long_candidates": "blocked" if risk_off else "enabled",
        "method": "沪深300、创业板、市场宽度与个股中位数联合过滤",
    }


def _transfer_predictions(
    themes: list[dict[str, Any]],
    sector_forecast: dict[str, Any] | None,
    market_regime: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Produce transparent provisional theme directions from validated priors.

    Before each theme has enough independent labels, the only defensible
    prediction is a transfer estimate: the existing parent-sector day-1 model
    supplies the prior and the current concept-board move supplies a small,
    capped intraday adjustment.  This never rewrites the parent probability.
    """
    parent_days = {
        sector.get("key"): (sector.get("days") or [{}])[0]
        for sector in (sector_forecast or {}).get("sectors", [])
    }
    market_regime = market_regime or _market_regime(None)
    benchmark_change = float(market_regime.get("benchmark_change") or 0)
    risk_off = market_regime.get("key") == "risk_off"
    output: list[dict[str, Any]] = []
    for theme in themes:
        parent = parent_days.get(theme.get("parent"), {})
        parent_probability = float(parent.get("up_probability", 50.0))
        parent_expected = float(parent.get("expected_return", 0.0))
        live_change = float(theme.get("change") or 0.0)
        relative_strength = live_change - benchmark_change
        # Concept-board movement is capped and receives less weight than the
        # historical parent model so a single noisy quote cannot dominate.
        live_bias = float(np.clip(live_change / 2.0, -1.0, 1.0))
        prior_bias = float(np.clip((parent_probability - 50.0) / 10.0, -1.0, 1.0))
        transfer_score = 0.65 * prior_bias + 0.35 * live_bias
        expected = 0.65 * parent_expected + 0.35 * float(np.clip(live_change * 0.20, -1.0, 1.0))
        if transfer_score >= 0.12 and expected > 0:
            direction = "明日临时偏强"
        elif transfer_score <= -0.12 and expected < 0:
            direction = "明日临时偏弱"
        else:
            direction = "明日临时震荡"
        confidence = (
            "中"
            if abs(transfer_score) >= 0.45
            and parent.get("signal_band") == "强"
            else "低"
        )
        # Keep the official direction conservative while exposing a separate
        # ranking layer for users who need cross-sectional selection.  These
        # labels are evidence-gated candidates, not independent theme-model
        # calls: the parent day-1 prior and the capped concept-board move must
        # point in the same direction.
        raw_up_candidate = transfer_score >= 0.05 and expected >= 0.05
        raw_down_candidate = transfer_score <= -0.12 and expected <= -0.05
        long_gate = (
            not risk_off
            and live_change >= 0
            and relative_strength >= 0.5
        )
        if raw_up_candidate and long_gate:
            selection_bucket = "候选偏强"
        elif raw_down_candidate:
            selection_bucket = "候选偏弱"
        elif risk_off and relative_strength >= 0.5:
            selection_bucket = "抗跌观察"
        else:
            selection_bucket = "中性"
        output.append({
            **theme,
            "provisional_direction": direction,
            "day_ahead_direction": direction,
            "provisional_score": round(transfer_score * 100, 1),
            "provisional_expected_return": round(expected, 2),
            "provisional_confidence": confidence,
            "selection_bucket": selection_bucket,
            "raw_up_candidate": raw_up_candidate,
            "relative_strength": round(relative_strength, 2),
            "selection_reason": (
                f"迁移分 {transfer_score * 100:.1f} · "
                f"概念快照 {live_change:+.2f}% · "
                f"父行业期望 {parent_expected:+.2f}% · "
                f"相对沪深300 {relative_strength:+.2f}pp"
            ),
            "prediction_stage": "一级行业次日先验 + 当日概念快照迁移",
            "prediction_horizon": "next_trading_session",
            "prediction_note": "这是今天信息对下一交易日的临时倾向；独立细分样本未达门槛。",
        })
    return output


def build_intraday_brief(
    path: str | Path = SNAPSHOT_FILE,
    sector_forecast: dict[str, Any] | None = None,
) -> dict[str, Any]:
    status = intraday_research_status(path)
    theme_training = micro_theme_training_status(path)
    try:
        rows = json.loads(Path(path).read_text(encoding="utf-8")).get("snapshots", [])
    except (OSError, json.JSONDecodeError):
        rows = []
    latest = rows[-1] if rows else None
    market_regime = _market_regime(latest)
    themes = sorted((latest or {}).get("themes", []), key=lambda item: (item.get("change", 0), item.get("amount", 0)), reverse=True)
    themes = _transfer_predictions(themes, sector_forecast, market_regime)
    selected_up = sorted(
        (theme for theme in themes if theme.get("selection_bucket") == "候选偏强"),
        key=lambda item: (item.get("provisional_score", 0), item.get("provisional_expected_return", 0)),
        reverse=True,
    )[:3]
    selected_down = sorted(
        (theme for theme in themes if theme.get("selection_bucket") == "候选偏弱"),
        key=lambda item: (item.get("provisional_score", 0), item.get("provisional_expected_return", 0)),
    )[:3]
    resilient = sorted(
        (theme for theme in themes if theme.get("selection_bucket") == "抗跌观察"),
        key=lambda item: (
            bool(item.get("raw_up_candidate")),
            item.get("relative_strength", 0),
            item.get("provisional_score", 0),
        ),
        reverse=True,
    )[:3]
    return {
        "status": status, "latest_snapshot": latest, "micro_themes": themes,
        "selection": {
            "up": selected_up,
            "down": selected_down,
            "resilient": resilient,
            "market_regime": market_regime,
            "long_candidate_note": (
                "风险市况下暂不输出普通偏强候选；抗跌观察仅供市场修复后复核。"
                if market_regime["key"] == "risk_off"
                else "偏强候选需同时满足实时上涨与相对沪深300强度门槛。"
            ),
        },
        "theme_training": theme_training,
        "taxonomy_count": len(MICRO_THEMES),
        "disclaimer": "页面主预测对象是下一交易日；盘中快照只作为次日先验和训练数据，不输出当日剩余走势。",
    }

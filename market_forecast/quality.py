from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
import math
from typing import Any
from zoneinfo import ZoneInfo

from market_forecast.trading_calendar import is_trading_session


@dataclass
class QualityResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "errors": self.errors,
            "warnings": self.warnings,
            "metrics": self.metrics,
        }


def _finite_numbers(value: Any, path: str = "root") -> list[str]:
    errors: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            errors.extend(_finite_numbers(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            errors.extend(_finite_numbers(item, f"{path}[{index}]"))
    elif isinstance(value, float) and not math.isfinite(value):
        errors.append(f"{path} 包含非有限数值")
    return errors


def validate_forecast(
    forecast: dict[str, Any],
    *,
    previous: dict[str, Any] | None = None,
    today: date | None = None,
    max_data_age_days: int = 5,
    minimum_sectors: int = 10,
    minimum_validation_samples: int = 100,
    minimum_breadth_stocks: int = 4000,
) -> QualityResult:
    result = QualityResult()
    today = today or datetime.now(ZoneInfo("Asia/Shanghai")).date()

    for key in (
        "meta",
        "market",
        "days",
        "validation",
        "sector_forecast",
        "events",
        "event_radar",
    ):
        if key not in forecast:
            result.errors.append(f"缺少顶层字段: {key}")
    if result.errors:
        return result

    meta = forecast["meta"]
    calendar = meta.get("trading_calendar") or {}
    if calendar.get("name") != "XSHG" or not str(
        calendar.get("url", "")
    ).startswith("http"):
        result.errors.append("缺少可审计的 XSHG 交易日历元数据")
    try:
        calendar_verified_through = date.fromisoformat(
            calendar["verified_through"]
        )
    except (KeyError, TypeError, ValueError):
        calendar_verified_through = None
        result.errors.append("交易日历缺少有效的官方核验截止日")
    try:
        data_through = date.fromisoformat(meta["data_through"])
    except (KeyError, TypeError, ValueError):
        result.errors.append("meta.data_through 不是有效日期")
        return result

    age = (today - data_through).days
    result.metrics["data_through"] = data_through.isoformat()
    result.metrics["data_age_days"] = age
    if age < 0:
        result.errors.append("行情数据日期晚于当前日期")
    elif age > max_data_age_days:
        result.errors.append(
            f"行情数据已过期 {age} 天，超过门限 {max_data_age_days} 天"
        )

    if previous:
        try:
            previous_date = date.fromisoformat(previous["meta"]["data_through"])
            if data_through < previous_date:
                result.errors.append(
                    f"行情日期倒退: {data_through} < {previous_date}"
                )
        except (KeyError, TypeError, ValueError):
            result.warnings.append("上一版日期无法解析，跳过倒退检查")

    days = forecast["days"]
    result.metrics["forecast_days"] = len(days)
    if len(days) != 5:
        result.errors.append(f"逐日预测应为5天，实际为 {len(days)} 天")
    day_dates: list[date] = []
    for index, item in enumerate(days):
        try:
            parsed = date.fromisoformat(item["date"])
            probability = float(item["up_probability"])
        except (KeyError, TypeError, ValueError):
            result.errors.append(f"第 {index + 1} 个逐日预测字段无效")
            continue
        day_dates.append(parsed)
        if not is_trading_session(parsed):
            result.errors.append(f"逐日预测包含非A股交易日: {parsed}")
        if not 0 <= probability <= 100:
            result.errors.append(f"上涨概率越界: {probability}")
    if day_dates != sorted(set(day_dates)):
        result.errors.append("逐日预测日期未严格递增或存在重复")
    if (
        day_dates
        and calendar_verified_through
        and day_dates[-1] > calendar_verified_through
    ):
        result.errors.append(
            "预测窗口超过交易日历官方核验范围: "
            f"{day_dates[-1]} > {calendar_verified_through}"
        )
    if day_dates and day_dates[0] <= today:
        result.errors.append(
            f"预测窗口已过期: 首日 {day_dates[0]} 不晚于当前日期 {today}"
        )

    validation = forecast["validation"]
    if validation.get("calibration") != (
        "扩展窗口样本外预测 + 时间顺序Sigmoid校准"
    ):
        result.errors.append("缺少独立时间顺序概率校准")
    samples = int(validation.get("samples", 0) or 0)
    result.metrics["validation_samples"] = samples
    if samples < minimum_validation_samples:
        result.errors.append(
            f"样本外验证样本不足: {samples} < {minimum_validation_samples}"
        )
    for key in (
        "daily_direction_accuracy",
        "weekly_direction_accuracy",
        "baseline_accuracy",
    ):
        value = float(validation.get(key, -1))
        if not 0 <= value <= 100:
            result.errors.append(f"验证指标 {key} 越界: {value}")
    if forecast["market"].get("validation_guard") == "degraded":
        if forecast["market"].get("weekly_direction") != "震荡":
            result.errors.append("样本外验证失效时周方向必须降为震荡")
        if any(day.get("confidence") != "低" for day in days):
            result.errors.append("样本外验证失效时逐日置信度必须全部降为低")
        result.warnings.append("样本外周模型未超过门限，预测已自动降级")

    sector_block = forecast["sector_forecast"]
    sectors = sector_block.get("sectors", [])
    result.metrics["sector_count"] = len(sectors)
    if len(sectors) < minimum_sectors:
        result.errors.append(
            f"行业覆盖不足: {len(sectors)} < {minimum_sectors}"
        )
    if sector_block.get("data_through") != meta.get("data_through"):
        result.errors.append("行业数据日期与大盘数据日期不一致")
    for sector in sectors:
        sector_days = sector.get("days", [])
        if len(sector_days) != 5:
            result.errors.append(
                f"行业 {sector.get('name', sector.get('key'))} 不是5日预测"
            )
        elif [item.get("date") for item in sector_days] != [
            item.get("date") for item in days
        ]:
            result.errors.append(
                f"行业 {sector.get('name', sector.get('key'))} "
                "与大盘交易日不一致"
            )

    breadth_stocks = int((forecast.get("breadth") or {}).get("stocks", 0) or 0)
    result.metrics["breadth_stocks"] = breadth_stocks
    if breadth_stocks < minimum_breadth_stocks:
        result.warnings.append(
            f"全市场宽度样本不足: {breadth_stocks}；方向与置信度必须降级"
        )
        if forecast["market"].get("breadth_guard") != "degraded":
            result.errors.append("市场宽度不足但未触发模型降级")
        if forecast["market"].get("weekly_direction") != "震荡":
            result.errors.append("市场宽度不足时周方向必须降为震荡")
        if any(day.get("confidence") != "低" for day in days):
            result.errors.append("市场宽度不足时逐日置信度必须全部降为低")

    for event in forecast.get("events", []):
        if event.get("status") != "已确认":
            result.errors.append(
                f"事件未确认却进入预测: {event.get('title', '未命名')}"
            )
        if not str(event.get("url", "")).startswith("http"):
            result.errors.append(
                f"事件缺少可审计来源: {event.get('title', '未命名')}"
            )

    collection = (forecast.get("event_radar") or {}).get("collection") or {}
    collection_status = collection.get("status")
    result.metrics["event_collection_status"] = collection_status
    if collection_status not in {
        "live",
        "partial",
        "cached",
        "manual",
    }:
        result.warnings.append("官方事件自动采集不可用；仅保留已核验静态事件")
    if collection_status in {"partial", "cached"}:
        result.warnings.append(
            f"官方事件采集状态为 {collection_status}；已启用缓存容灾"
        )

    monitor = forecast.get("performance_monitor") or {}
    monitor_status = monitor.get("status")
    result.metrics["performance_status"] = monitor_status
    result.metrics["live_evaluated_samples"] = int(
        monitor.get("evaluated_samples", 0) or 0
    )
    if monitor_status not in {"collecting", "healthy", "watch", "degraded"}:
        result.errors.append("缺少有效的实盘预测效果监控")
    if monitor.get("effective_sample") != "第1日预测的唯一目标交易日":
        result.errors.append("实盘监控未使用第1日唯一目标日口径")
    if monitor_status == "degraded":
        if forecast["market"].get("weekly_direction") != "震荡":
            result.errors.append("模型失效时周方向未自动降级为震荡")
        if any(day.get("confidence") != "低" for day in days):
            result.errors.append("模型失效时逐日置信度未全部降为低")
        result.warnings.append("实盘近期表现低于门限，预测已自动降级")

    result.errors.extend(_finite_numbers(forecast))
    result.metrics["event_count"] = len(forecast.get("events", []))
    return result

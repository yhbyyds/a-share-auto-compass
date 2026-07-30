const $ = (selector) => document.querySelector(selector);
const PUBLIC_FORECAST_URL = "./data/forecast.json?v=1.17.1";
let renderedSnapshotId = "";
let forecastPollInFlight = false;

function snapshotId(data) {
  const meta = data?.meta || {};
  return [meta.generated_at, meta.data_through, meta.release, meta.version]
    .filter(Boolean)
    .join("|");
}
const formatSigned = (value, suffix = "%") => `${Number(value) > 0 ? "+" : ""}${value}${suffix}`;
const impactClass = (impact) => impact === "positive" ? "positive-text" : impact === "negative" ? "negative-text" : "neutral-text";
const cardClass = (direction) => direction?.includes("偏强") ? "positive" : direction?.includes("偏弱") ? "negative" : "neutral";
const relativeSignalClass = (signal) => signal === "相对偏强" ? "positive-text" : signal === "相对偏弱" ? "negative-text" : "neutral-text";
const riskClass = (risk) => risk === "极高" ? "extreme" : risk === "高" ? "high" : risk === "中" ? "medium" : "low";

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.add("visible");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.remove("visible"), 3500);
}

function renderPulse(data) {
  const reliability = data.validation?.reliability || {};
  const score = reliability.score == null ? null : Number(reliability.score);
  $("#pulse-reliability").textContent = score == null
    ? "等待新模型"
    : `${reliability.label || "已评估"} · ${score.toFixed(0)}分`;
  $("#pulse-reliability-note").textContent = reliability.reasons?.slice(0, 2).join(" · ")
    || "下一次收盘重训后显示综合证据";
  $("#pulse-data-date").textContent = data.meta?.data_through || "—";
  $("#pulse-data-state").textContent = data.meta?.automation?.quality_gate?.passed
    ? "发布质量门禁已通过"
    : data.meta?.automation?.status === "manual"
      ? "人工校验快照"
      : "已发布数据快照";

  const today = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
  }).format(new Date());
  const day = (
    data.day_ahead?.date >= today
      ? data.day_ahead
      : data.days?.find((item) => item.date >= today)
  ) || data.days?.[0] || {};
  $("#pulse-day-score").textContent = day.evidence_score == null
    ? `${day.validation_accuracy ?? "—"}%`
    : `${Number(day.evidence_score).toFixed(0)} / 100`;
  $("#pulse-day-label").textContent = day.evidence_score == null
    ? "显示近期方向验证"
    : `${day.evidence_label || "证据"} · ${day.direction || "震荡"}`;

  const selection = data.sector_forecast?.tomorrow_selection || {};
  const sectorFreshness = data.sector_forecast?.freshness || {};
  const sourceSpread = selection.source_spread || {};
  const rankingValidation = selection.selection_validation || {};
  const spread = selection.score_spread;
  if (sectorFreshness.status === "stale") {
    const lag = sectorFreshness.lag_trading_sessions ?? "\u2014";
    $("#pulse-sector-spread").textContent = `\u6ede\u540e ${lag} \u65e5`;
    $("#pulse-sector-label").textContent = sectorFreshness.message || "\u884c\u4e1a\u5019\u9009\u7b49\u5f85\u6536\u76d8\u6570\u636e\u540c\u6b65";
  } else {
    $("#pulse-sector-spread").textContent = spread == null
      ? `${sourceSpread.probability_pp ?? "\u2014"}pp`
      : `${Number(spread).toFixed(1)}\u5206`;
    $("#pulse-sector-label").textContent = sectorFreshness.status === "provisional"
      ? `\u5b9e\u65f6\u677f\u5757\u5feb\u7167 \u00b7 ${sectorFreshness.message || "\u5f85\u65e5\u7ebf\u590d\u6838"}`
      : rankingValidation.samples
        ? `\u6392\u5e8f\u9a8c\u8bc1 ${Number(rankingValidation.spread_hit_rate ?? 0).toFixed(1)}% \u00b7 ${rankingValidation.samples}\u65e5`
        : sourceSpread.separated === false
          ? "\u677f\u5757\u533a\u5206\u504f\u5c0f"
          : `\u5f3a\u5019\u9009 ${selection.validated_up_count ?? "\u2014"} \u00b7 \u5f31\u5019\u9009 ${selection.validated_down_count ?? "\u2014"}`;
  }
  const regime = data.intraday?.selection?.market_regime || {};
  $("#pulse-regime").textContent = regime.label || data.market?.weekly_direction || "—";
  $("#pulse-regime-note").textContent = regime.flags?.slice(0, 2).join(" · ")
    || `风险等级 ${data.market?.risk_level || "—"}`;
}

function renderDays(days) {
  $("#day-grid").innerHTML = days.map((day) => {
    const range = Math.max(day.high_return - day.low_return, 0.01);
    const position = Math.max(4, Math.min(96, (day.expected_return - day.low_return) / range * 100));
    return `
      <article class="day-card panel ${cardClass(day.direction)}">
        <div class="day-date">
          <span>${day.date.slice(5)} · 周${day.weekday}</span>
          <span class="day-badges">
            <span class="confidence">${day.confidence}置信</span>
            ${day.event_count ? `<span class="event-mini ${riskClass(day.event_risk)}">事件${day.event_risk}</span>` : ""}
          </span>
        </div>
        <h3>${day.direction}</h3>
        <span class="day-prob">上涨概率 ${day.up_probability}%</span>
        <span class="day-prob"> · 近120次验证 ${day.validation_accuracy}%</span>
        <div class="range-track"><i class="range-dot" style="left:${position}%"></i></div>
        <div class="day-range"><span>${day.low_return}%</span><span>${formatSigned(day.high_return)}</span></div>
        <div class="day-return">
          <span>中性路径<br>${day.path_close}</span>
          <strong class="${day.expected_return >= 0 ? "positive-text" : "negative-text"}">${formatSigned(day.expected_return)}</strong>
        </div>
      </article>`;
  }).join("");
}

function renderDayAhead(dayAhead, days, meta) {
  const panel = $("#day-ahead-panel");
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai" }).format(new Date());
  const dayAheadActive = Boolean(dayAhead && dayAhead.date >= today);
  const row = dayAheadActive ? dayAhead : days?.find((item) => item.date >= today) || days?.[0];
  if (!panel || !row) return;
  const explicit = dayAheadActive;
  const isToday = row.date === today;
  const direction = row.direction || "震荡";
  const probability = Number(row.up_probability ?? 50);
  const expected = Number(row.expected_return ?? 0);
  const range = row.low_return != null && row.high_return != null
    ? `${row.low_return}% &mdash; ${formatSigned(row.high_return)}`
    : "&mdash;";
  const validation = row.validation_accuracy == null ? "&mdash;" : `${row.validation_accuracy}%`;
  const eventRisk = row.event_risk || "低";
  panel.innerHTML = `
    <div class="day-ahead-head">
      <div>
        <span class="label">NEXT TRADING SESSION</span>
        <h2>${isToday ? "今日A股日间预测" : explicit ? "明日A股日间预测" : "最近一次日间预测"}</h2>
        <p class="day-ahead-date">${row.date || "待收盘更新"} &middot; ${row.weekday ? `周${row.weekday}` : ""} &middot; 基于 ${row.based_on_close_date || meta?.data_through || "最新收盘"}</p>
      </div>
      <span class="day-ahead-stage">${isToday ? "已由上一交易日收盘生成" : explicit ? "收盘后主预测" : "等待收盘生成明日主预测"}</span>
    </div>
    <div class="day-ahead-grid">
      <div class="day-ahead-direction ${cardClass(direction)}">
        <span>方向判断</span><strong>${direction}</strong>
        <small>上涨概率 ${probability}% &middot; ${row.confidence || "中"}置信</small>
      </div>
      <div><span>模型期望</span><strong class="${expected >= 0 ? "positive-text" : "negative-text"}">${formatSigned(expected)}</strong><small>历史相似区间 ${range}</small></div>
      <div><span>样本外验证</span><strong>${validation}</strong><small>${row.evidence_score == null ? `事件风险 ${eventRisk}` : `综合证据 ${row.evidence_score}/100 · ${row.evidence_label}`}</small></div>
    </div>
    <p class="day-ahead-note">数据快照截至 ${meta?.data_through || "&mdash;"}；收盘后自动生成下一交易日正式预测。主产品回答“今天收盘后、下一交易日大方向如何”。盘中快照只用于辅助更新、不替代收盘后的日间预测。</p>`;
}

function renderChart(history, days) {
  const width = 760;
  const height = 305;
  const pad = { top: 14, right: 55, bottom: 24, left: 8 };
  const all = [...history.map((d) => d.close), ...days.map((d) => d.path_close)];
  const min = Math.min(...all);
  const max = Math.max(...all);
  const extent = Math.max(max - min, 1);
  const low = min - extent * .08;
  const high = max + extent * .08;
  const totalPoints = history.length + days.length;
  const x = (i) => pad.left + i / (totalPoints - 1) * (width - pad.left - pad.right);
  const y = (v) => pad.top + (high - v) / (high - low) * (height - pad.top - pad.bottom);
  const historyPath = history.map((d, i) => `${i ? "L" : "M"}${x(i).toFixed(2)},${y(d.close).toFixed(2)}`).join(" ");
  const forecastValues = [{ close: history.at(-1).close }, ...days.map((d) => ({ close: d.path_close }))];
  const forecastPath = forecastValues.map((d, i) => `${i ? "L" : "M"}${x(history.length - 1 + i).toFixed(2)},${y(d.close).toFixed(2)}`).join(" ");
  const area = `${historyPath} L${x(history.length - 1)},${height - pad.bottom} L${x(0)},${height - pad.bottom} Z`;
  const gridValues = [low, low + (high-low)/2, high];
  $("#price-chart").innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="上证指数走势">
      <defs>
        <linearGradient id="areaGradient" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#0c6b4f" stop-opacity=".16"/>
          <stop offset="100%" stop-color="#0c6b4f" stop-opacity="0"/>
        </linearGradient>
      </defs>
      ${gridValues.map(v => `<line class="chart-grid-line" x1="${pad.left}" x2="${width-pad.right}" y1="${y(v)}" y2="${y(v)}"/><text class="chart-label" x="${width-pad.right+8}" y="${y(v)+3}">${Math.round(v)}</text>`).join("")}
      <path class="chart-area" d="${area}"/>
      <path class="history-path" d="${historyPath}"/>
      <path class="forecast-path" d="${forecastPath}"/>
      <circle cx="${x(history.length-1)}" cy="${y(history.at(-1).close)}" r="4" fill="#17211c"/>
      <circle cx="${x(totalPoints-1)}" cy="${y(days.at(-1).path_close)}" r="5" fill="#0c6b4f" stroke="#fff" stroke-width="2"/>
      <text class="chart-label" x="${pad.left}" y="${height-5}">${history[0].date.slice(5)}</text>
      <text class="chart-label" x="${x(history.length-1)-34}" y="${height-5}">${history.at(-1).date.slice(5)}</text>
      <text class="chart-label" x="${width-pad.right-22}" y="${height-5}">${days.at(-1).date.slice(5)}</text>
    </svg>`;
}

function renderBreadth(breadth) {
  if (!breadth || !breadth.stocks) {
    $("#breadth-panel").innerHTML = `<p class="breadth-unavailable">全市场快照接口暂时限流；历史指数模型与回测不受影响。重新计算时会再次尝试。</p>`;
    return;
  }
  const items = [
    ["上涨", breadth.advancers, "positive-text"],
    ["下跌", breadth.decliners, "negative-text"],
    ["平盘", breadth.flat, ""],
    ["上涨占比", `${breadth.advance_ratio}%`, breadth.advance_ratio >= 50 ? "positive-text" : "negative-text"],
    ["中位涨跌", formatSigned(breadth.median_change), breadth.median_change >= 0 ? "positive-text" : "negative-text"],
    ["成交额", `${breadth.turnover_yi}亿`, ""],
    ["数据状态", breadth.status === "live" ? "实时" : "缓存", ""],
  ];
  $("#breadth-panel").innerHTML = `<div class="breadth-stats">${items.map(([name, value, cls]) => `<div class="breadth-stat"><span>${name}</span><strong class="${cls}">${value}</strong></div>`).join("")}</div><p class="breadth-source">来源：${breadth.source || "全A快照"}</p>`;
}

function renderIntradayLab(intraday) {
  const container = $("#intraday-lab");
  if (!intraday) {
    container.innerHTML = `<p class="breadth-unavailable">超短线数据集尚未初始化。</p>`;
    return;
  }
  const status = intraday.status || {};
  const latest = intraday.latest_snapshot;
  const themes = (intraday.micro_themes || []).slice(0, 8);
  const themeTraining = intraday.theme_training || {};
  const selection = intraday.selection || {};
  const selectedUp = selection.up || [];
  const selectedDown = selection.down || [];
  const resilient = selection.resilient || [];
  const marketRegime = selection.market_regime || {};
  const ready = status.status === "ready";
  const renderSelection = (items, tone, empty) => items.length
    ? items.map((theme, index) => `<div class="selection-item ${tone}"><span>#${index + 1} · ${theme.name}</span><strong>${theme.selection_bucket}</strong><small>${theme.selection_reason || `迁移分 ${theme.provisional_score ?? "—"}`}</small></div>`).join("")
    : `<p class="selection-empty">${empty}</p>`;
  container.innerHTML = `
    <div class="intraday-head">
      <div><span class="label">${ready ? "VALIDATED INTRADAY MODEL" : "DATA COLLECTION · NO LIVE CALL"}</span>
      <h3>${ready ? "盘中方向模型已达到训练门槛" : "超短线模型正在积累独立样本"}</h3></div>
      <span class="intraday-status ${ready ? "ready" : "collecting"}">${ready ? "可进入样本外核验" : "采集中"}</span>
    </div>
    <div class="intraday-progress"><div><strong>${status.labelled_sessions || 0}</strong><span>/ ${status.minimum_sessions || 60} 交易日</span></div><div><strong>${status.labelled_samples || 0}</strong><span>/ ${status.minimum_samples || 240} 已结算快照</span></div><div><strong>${intraday.taxonomy_count || 0}</strong><span>细分领域覆盖</span></div></div>
    <p class="intraday-copy">${status.reason || status.method || "按日期前推验证中。"}</p>
    ${latest ? `<p class="intraday-snapshot">最近快照：${new Date(latest.timestamp).toLocaleString("zh-CN", { hour12: false })} · ${latest.bucket} 桶 · ${latest.source}</p>` : ""}
    <div class="micro-regime-banner ${marketRegime.key === "risk_off" ? "risk-off" : "normal"}"><strong>${marketRegime.label || "常态市况"}</strong><span>风险分 ${marketRegime.risk_score || 0} · ${selection.long_candidate_note || "偏强候选需经过市场状态过滤。"}</span></div>
    <div class="micro-selection-grid">
      <div class="micro-selection-column"><h4>${marketRegime.key === "risk_off" ? "风险市况 · 暂停普通偏强候选" : "模型候选偏强 · 优先观察"}</h4><p>${marketRegime.flags?.join("、") || "按父行业次日先验、实时上涨和相对强度排序。"}</p>${renderSelection(selectedUp, "positive", "当前没有通过偏强候选门槛的细分板块。")}${resilient.length ? `<h5>抗跌观察</h5>${renderSelection(resilient, "resilient", "暂无抗跌观察板块。")}` : ""}</div>
      <div class="micro-selection-column"><h4>模型候选偏弱 · 风险回避</h4><p>仅表示相对弱势，需结合开盘后强弱确认。</p>${renderSelection(selectedDown, "negative", "当前没有达到偏弱候选门槛的细分板块。")}</div>
    </div>
    <div class="micro-theme-grid">${themes.length ? themes.map((theme, index) => {
      const training = themeTraining[theme.key] || {};
      const directionClass = theme.provisional_direction?.includes("偏强") ? "positive" : theme.provisional_direction?.includes("偏弱") ? "negative" : "neutral";
      return `<div class="micro-theme ${directionClass}"><span>#${index + 1} · ${theme.parent}</span><strong>${theme.name}</strong><em>${theme.provisional_direction || "明日临时震荡"} · ${theme.provisional_confidence || "低"}置信</em><small>快照 ${formatSigned(Number(theme.change || 0))} · 迁移分 ${theme.provisional_score ?? "—"}</small><small>独立标签 ${training.labelled_samples || 0}/${training.minimum_samples || 240} · ${training.status === "ready" ? "已达训练门槛" : "采集中"}</small></div>`;
    }).join("") : `<p class="breadth-unavailable">尚无细分板块快照；将在下一固定盘中时点采集。</p>`}</div>
    <p class="section-footnote warning">${intraday.disclaimer || "盘中热度不等于买入信号。"}</p>`;
}

function renderHorizonValidation(rows) {
  $("#horizon-validation").innerHTML = rows.map((row) => {
    const edge = row.recent_accuracy - row.baseline;
    const edgeClass = edge >= 2 ? "" : "weak";
    const highConfidence = row.high_conf_accuracy == null ? "无样本" : `${row.high_conf_accuracy}%`;
    return `
      <article class="horizon-card panel">
        <span class="horizon-label">${row.label}</span>
        <strong>${row.recent_accuracy}%</strong>
        <span class="horizon-edge ${edgeClass}">${edge >= 0 ? "+" : ""}${edge.toFixed(1)}pp 对基线</span>
        <div class="horizon-details">
          <div><span>全样本准确率</span><strong>${row.accuracy}%</strong></div>
          <div><span>高置信命中率</span><strong>${highConfidence}</strong></div>
          <div><span>高置信覆盖</span><strong>${row.high_conf_coverage}%</strong></div>
          <div><span>AUC / Brier</span><strong>${row.auc} / ${row.brier}</strong></div>
        </div>
      </article>`;
  }).join("");
}

function linePath(values, width = 280, height = 72, padding = 4) {
  if (!values?.length) return "";
  const low = Math.min(...values);
  const high = Math.max(...values);
  const extent = Math.max(high - low, .01);
  return values.map((value, index) => {
    const x = padding + index / Math.max(values.length - 1, 1) * (width - padding * 2);
    const y = padding + (high - value) / extent * (height - padding * 2);
    return `${index ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
}

function sectorSparkline(sector) {
  const values = (sector.history || []).slice(-30).map((row) => row.sector);
  if (values.length < 2) return "";
  const positive = values.at(-1) >= values[0];
  return `<svg class="sector-sparkline" viewBox="0 0 280 72" aria-label="${sector.name}近30日趋势">
    <path d="${linePath(values)}" class="${positive ? "spark-positive" : "spark-negative"}"/>
    <circle cx="276" cy="${(() => {
      const low = Math.min(...values); const high = Math.max(...values);
      return (4 + (high - values.at(-1)) / Math.max(high - low, .01) * 64).toFixed(1);
    })()}" r="3.5"/>
  </svg>`;
}

function renderSectorTomorrow(selection, sectors, freshness = {}) {
  if (freshness.status === "stale") {
    const note = freshness.message || "\u884c\u4e1a\u6536\u76d8\u5e8f\u5217\u7b49\u5f85\u540c\u6b65\u3002";
    const content = `<p class="selection-empty">${note}</p>`;
    $("#sector-tomorrow-up").innerHTML = content;
    $("#sector-tomorrow-down").innerHTML = content;
    $("#sector-tomorrow-summary").textContent = "\u677f\u5757\u5f53\u65e5\u5019\u9009\u5df2\u6682\u505c";
    $("#sector-tomorrow-method").textContent = note;
    return;
  }
  const rankScore = (rank) => Math.round(
    100
    - (Math.max(Number(rank) - 1, 0) / Math.max(sectors.length - 1, 1))
    * 100,
  );
  const fallback = {
    up: sectors.slice(0, 3).map((sector) => ({
      key: sector.key,
      name: sector.name,
      tomorrow_rank: sector.rank,
      score: sector.days?.[0]?.signal_strength || rankScore(sector.rank),
      priority_score: rankScore(sector.rank),
      status: "周相对领先观察",
      direction: sector.days?.[0]?.direction || "震荡",
      relative_signal: sector.days?.[0]?.relative_signal || "相对中性",
      up_probability: sector.days?.[0]?.up_probability ?? "—",
      directional_win_rate: sector.validation?.accuracy ?? "—",
      expected_excess: sector.days?.[0]?.expected_excess ?? 0,
    })),
    down: sectors.slice(-3).reverse().map((sector) => ({
      key: sector.key,
      name: sector.name,
      tomorrow_rank: sector.rank,
      score: sector.days?.[0]?.signal_strength || rankScore(sector.rank),
      priority_score: rankScore(sector.rank),
      status: "周相对落后观察",
      direction: sector.days?.[0]?.direction || "震荡",
      relative_signal: sector.days?.[0]?.relative_signal || "相对中性",
      up_probability: sector.days?.[0]?.up_probability ?? "—",
      directional_win_rate: sector.validation?.accuracy ?? "—",
      expected_excess: sector.days?.[0]?.expected_excess ?? 0,
    })),
    score_spread: null,
    method: "当前页面使用周行业排序兼容展示；下一次收盘重训后切换为第1日专用分层。",
  };
  const resolved = selection?.up?.length ? selection : fallback;
  const renderList = (items, tone) => items.map((item) => `
    <button class="sector-tomorrow-item" type="button" data-sector="${item.key}">
      <span>#${item.tomorrow_rank ?? "—"}</span>
      <span class="sector-tomorrow-copy">
        <strong>${item.name}</strong>
        <small>${item.status} · ${item.direction} · ${item.relative_signal}</small>
      </span>
      <span class="sector-tomorrow-score">
        <strong class="${tone === "up" ? "positive-text" : "negative-text"}">${Number(item.priority_score ?? item.score ?? 0).toFixed(0)}</strong>
        <small>优先级 · 上涨 ${item.up_probability ?? "—"}% · 胜率 ${item.directional_win_rate ?? "—"}%</small>
        <small>超额 ${formatSigned(item.expected_excess ?? 0)}${item.expected_excess_weight != null ? ` · 回归权重 ${Math.round(Number(item.expected_excess_weight) * 100)}%` : ""}</small>
      </span>
    </button>`).join("");
  $("#sector-tomorrow-up").innerHTML = renderList(resolved.up || [], "up")
    || `<p class="selection-empty">当前没有形成领先侧排序。</p>`;
  $("#sector-tomorrow-down").innerHTML = renderList(resolved.down || [], "down")
    || `<p class="selection-empty">当前没有形成落后侧排序。</p>`;
  const rankValidation = resolved.selection_validation || {};
  const rankEvidence = rankValidation.samples
    ? ` · 排序样本 ${rankValidation.samples}日 · 顶底差 ${formatSigned(rankValidation.top_bottom_excess ?? 0)} · 命中 ${Number(rankValidation.spread_hit_rate ?? 0).toFixed(1)}%`
    : "";
  $("#sector-tomorrow-summary").textContent = resolved.score_spread == null
    ? "兼容展示 · 等待第1日专用分层"
    : `第1日横截面区分度 ${resolved.score_spread}分 · 优先级按行业相对排名归一化 · 正式偏强 ${resolved.validated_up_count} · 正式偏弱 ${resolved.validated_down_count}${rankEvidence}`;
  const freshnessNote = freshness.status === "provisional"
    ? `\u5b9e\u65f6\u677f\u5757\u5feb\u7167：${freshness.message || "\u5f85\u65e5\u7ebf\u590d\u6838"}`
    : "";
  $("#sector-tomorrow-method").textContent = [resolved.method, freshnessNote]
    .filter(Boolean)
    .join(" \u00b7 ");
  document.querySelectorAll(".sector-tomorrow-item").forEach((button) => {
    button.addEventListener("click", () => {
      const select = $("#sector-select");
      if (
        !select
        || ![...select.options].some(
          (option) => option.value === button.dataset.sector,
        )
      ) return;
      select.value = button.dataset.sector;
      select.dispatchEvent(new Event("change"));
      $("#sector-detail-title")?.scrollIntoView({
        behavior: "smooth",
        block: "center",
      });
    });
  });
}

function renderSectorDetail(sector) {
  const history = sector.history || [];
  $("#sector-detail-title").textContent = `${sector.name} · 趋势与相对强弱`;
  if (history.length < 2) {
    $("#sector-detail-chart").innerHTML = `<p class="breadth-unavailable">历史走势暂不可用。</p>`;
    $("#sector-detail-metrics").innerHTML = "";
    return;
  }
  const width = 720;
  const height = 280;
  const pad = { top: 25, right: 24, bottom: 30, left: 42 };
  const allValues = history.flatMap((row) => [row.sector, row.benchmark]);
  const low = Math.min(...allValues) - 1;
  const high = Math.max(...allValues) + 1;
  const x = (i) => pad.left + i / (history.length - 1) * (width - pad.left - pad.right);
  const y = (v) => pad.top + (high - v) / Math.max(high - low, .01) * (height - pad.top - pad.bottom);
  const path = (key) => history.map((row, index) => `${index ? "L" : "M"}${x(index).toFixed(1)},${y(row[key]).toFixed(1)}`).join(" ");
  const ticks = [low, (low + high) / 2, high];
  $("#sector-detail-chart").innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${sector.name}与沪深300近60日走势">
    ${ticks.map((tick) => `<line class="sector-chart-grid" x1="${pad.left}" x2="${width-pad.right}" y1="${y(tick)}" y2="${y(tick)}"/><text class="sector-chart-label" x="4" y="${y(tick)+4}">${tick.toFixed(1)}</text>`).join("")}
    <line class="sector-chart-baseline" x1="${pad.left}" x2="${width-pad.right}" y1="${y(100)}" y2="${y(100)}"/>
    <path class="sector-chart-benchmark" d="${path("benchmark")}"/>
    <path class="sector-chart-main" d="${path("sector")}"/>
    <circle class="sector-chart-dot" cx="${x(history.length-1)}" cy="${y(history.at(-1).sector)}" r="5"/>
    <text class="sector-chart-label" x="${pad.left}" y="${height-7}">${history[0].date.slice(5)}</text>
    <text class="sector-chart-label" x="${width-pad.right-34}" y="${height-7}">${history.at(-1).date.slice(5)}</text>
  </svg>`;
  const sectorChange = history.at(-1).sector - 100;
  const benchmarkChange = history.at(-1).benchmark - 100;
  const relative = history.at(-1).relative - 100;
  $("#sector-detail-metrics").innerHTML = `
    <div><span>行业60日</span><strong class="${sectorChange >= 0 ? "positive-text" : "negative-text"}">${formatSigned(sectorChange.toFixed(2))}</strong></div>
    <div><span>沪深300同期</span><strong>${formatSigned(benchmarkChange.toFixed(2))}</strong></div>
    <div><span>相对强弱</span><strong class="${relative >= 0 ? "positive-text" : "negative-text"}">${formatSigned(relative.toFixed(2))}</strong></div>
    <div><span>模型周路径</span><strong>${formatSigned(sector.weekly_expected_return)}</strong></div>
    <div><span>跑赢概率</span><strong>${sector.outperform_probability}%</strong></div>
    <div><span>第1日验证</span><strong>${sector.validation.accuracy}%</strong></div>
    <div class="sector-chart-legend"><span><i class="legend-sector"></i>行业</span><span><i class="legend-benchmark"></i>沪深300</span></div>`;
}

function renderSectorVisuals(sectors) {
  $("#sector-heatmap").innerHTML = sectors.map((sector) => {
    const value = Number(sector.weekly_expected_return);
    const intensity = Math.min(Math.abs(value) / 1.2, 1);
    const tone = value > .1 ? "heat-positive" : value < -.1 ? "heat-negative" : "heat-neutral";
    return `<button class="sector-heat-cell ${tone}" data-sector="${sector.key}" style="--heat:${intensity.toFixed(2)}">
      <span>${sector.name}</span><strong>${formatSigned(value)}</strong><small>#${sector.rank} · 胜${sector.outperform_probability}%</small>
    </button>`;
  }).join("");
  const positive = sectors.filter((sector) => sector.weekly_expected_return > .1).length;
  const negative = sectors.filter((sector) => sector.weekly_expected_return < -.1).length;
  const neutral = sectors.length - positive - negative;
  const total = Math.max(sectors.length, 1);
  $("#sector-distribution").innerHTML = `
    <div class="distribution-numbers"><span><strong class="positive-text">${positive}</strong>偏强</span><span><strong>${neutral}</strong>中性</span><span><strong class="negative-text">${negative}</strong>偏弱</span></div>
    <div class="distribution-bar"><i class="dist-positive" style="width:${positive/total*100}%"></i><i class="dist-neutral" style="width:${neutral/total*100}%"></i><i class="dist-negative" style="width:${negative/total*100}%"></i></div>
    <p>共 ${sectors.length} 个行业方向 · 基于五日模型路径</p>`;
  const select = $("#sector-select");
  select.innerHTML = sectors.map((sector) => `<option value="${sector.key}">#${sector.rank} ${sector.name}</option>`).join("");
  const selectSector = (key) => {
    const sector = sectors.find((item) => item.key === key) || sectors[0];
    select.value = sector.key;
    renderSectorDetail(sector);
  };
  select.onchange = () => selectSector(select.value);
  $("#sector-heatmap").onclick = (event) => {
    const cell = event.target.closest("[data-sector]");
    if (cell) {
      selectSector(cell.dataset.sector);
      $("#sector-detail-title").scrollIntoView({ behavior: "smooth", block: "center" });
    }
  };
  selectSector(sectors[0].key);
}

function renderSectorForecast(forecast) {
  if (!forecast || !forecast.sectors?.length) {
    $("#sector-leaders").innerHTML = `<article class="watchlist-empty panel">行业数据暂不可用，大盘模型仍可单独使用。</article>`;
    $("#sector-matrix").innerHTML = "";
    return;
  }

  const sectors = [...forecast.sectors].sort((a, b) => a.rank - b.rank);
  const freshness = forecast.freshness || {};
  if (freshness.status === "stale") {
    renderSectorTomorrow(forecast.tomorrow_selection, sectors, freshness);
    $("#sector-leaders").innerHTML = `<article class="watchlist-empty panel">${freshness.message || "\u884c\u4e1a\u6536\u76d8\u5e8f\u5217\u7b49\u5f85\u540c\u6b65\u3002"}</article>`;
    $("#sector-matrix").innerHTML = "";
    $("#sector-heatmap").innerHTML = "";
    $("#sector-distribution").innerHTML = `<p class="selection-empty">\u5927\u76d8\u65e5\u95f4\u9884\u6d4b\u5df2\u6309\u6700\u65b0\u6536\u76d8\u66f4\u65b0\uff1b\u677f\u5757\u7b49\u5f85\u5bf9\u5e94\u6536\u76d8\u6e90\u540c\u6b65\u3002</p>`;
    $("#sector-select").innerHTML = `<option>\u7b49\u5f85\u884c\u4e1a\u6570\u636e\u540c\u6b65</option>`;
    $("#sector-detail-title").textContent = "\u884c\u4e1a\u6570\u636e\u72b6\u6001";
    $("#sector-detail-chart").innerHTML = "";
    $("#sector-detail-metrics").innerHTML = "";
    $("#sector-method-note").textContent = `${forecast.data_source}\uff0c\u6570\u636e\u622a\u81f3 ${forecast.actual_data_through || forecast.data_through}\u3002${forecast.method}`;
    return;
  }
  renderSectorVisuals(sectors);
  renderSectorTomorrow(forecast.tomorrow_selection, sectors, freshness);
  const leaders = sectors.slice(0, 3);
  $("#sector-leaders").innerHTML = leaders.map((sector) => {
    const validationEdge = sector.validation.accuracy - sector.validation.baseline;
    return `
      <article class="sector-leader panel">
        <div class="sector-leader-top">
          <span class="sector-rank">#${sector.rank}</span>
          <span class="sector-outlook ${sector.weekly_outlook === "相对领先" ? "positive-text" : sector.weekly_outlook === "相对落后" ? "negative-text" : "neutral-text"}">${sector.weekly_outlook}</span>
        </div>
        <span class="stock-code">${sector.code} · ${sector.group}</span>
        <h3>${sector.name}</h3>
        <span class="sector-relative-signal ${relativeSignalClass(sector.days?.[0]?.relative_signal)}">日1相对层：${sector.days?.[0]?.relative_signal || "相对中性"}</span>
        ${sectorSparkline(sector)}
        <div class="sector-leader-metrics">
          <div><span>周路径</span><strong>${formatSigned(sector.weekly_expected_return)}</strong></div>
          <div><span>相对基准</span><strong>${formatSigned(sector.weekly_expected_excess)}</strong></div>
          <div><span>跑赢概率</span><strong>${sector.outperform_probability}%</strong></div>
        </div>
        <p>${sector.drivers.slice(0, 2).join(" · ")}</p>
        <span class="sector-validation ${validationEdge >= 2 ? "" : "weak"}">${sector.validation.is_proxy ? "第1日子行业均值" : "第1日验证"} ${sector.validation.accuracy}% / 基线 ${sector.validation.baseline}%</span>
      </article>`;
  }).join("");

  const headers = forecast.sectors[0].days.map((day) => `<div class="sector-column-head">${day.date.slice(5)}<span>周${day.weekday}</span></div>`).join("");
  $("#sector-matrix").innerHTML = `
    <div class="sector-matrix-row sector-matrix-header">
      <div>行业 / 周判断</div>
      ${headers}
      <div>第1日验证</div>
    </div>
    ${sectors.map((sector) => {
      const validationEdge = sector.validation.accuracy - sector.validation.baseline;
      return `
        <div class="sector-matrix-row ${sector.key === "technology" || sector.key === "utilities" ? "featured" : ""}">
          <div class="sector-name-cell">
            <span class="sector-mini-rank">#${sector.rank}</span>
            <strong>${sector.name}</strong>
            <small>${sector.weekly_outlook} · 周${formatSigned(sector.weekly_expected_return)}</small>
          </div>
          ${sector.days.map((day) => `
              <div class="sector-day-cell ${cardClass(day.direction)}">
                <strong>${day.direction}</strong>
                <span class="signal-strength ${day.signal_band === "强" ? "strong" : ""}">证据 ${day.signal_strength ?? "—"} · ${day.signal_band ?? "—"}</span>
              <small class="sector-relative-mini ${relativeSignalClass(day.relative_signal)}">${day.relative_signal || "相对中性"}</small>
              <span>涨 ${day.up_probability}%</span>
              <span>胜 ${day.outperform_probability}%</span>
              ${day.event_count ? `<span class="sector-event-flag ${riskClass(day.event_risk)}">事件${day.event_risk}</span>` : ""}
            </div>`).join("")}
          <div class="sector-evidence-cell">
            <strong class="${validationEdge >= 2 ? "positive-text" : "negative-text"}">${sector.validation.accuracy}%</strong>
            <span>${sector.validation.is_proxy ? "子行业均值" : `基线 ${sector.validation.baseline}%`}</span>
            <span>${sector.validation.samples} 样本</span>
          </div>
        </div>`;
    }).join("")}`;

  $("#sector-method-note").textContent = `${forecast.data_source}，数据截至 ${forecast.data_through}。${forecast.method}`;
}

function renderWatchlist(rows) {
  if (!rows || !rows.length) {
    $("#watchlist").innerHTML = `<article class="watchlist-empty panel">当前没有股票同时通过流动性、趋势、过热和波动过滤；空观察池也是有效结果。</article>`;
    return;
  }
  $("#watchlist").innerHTML = rows.map((stock) => `
    <article class="stock-card panel">
        <div class="stock-head">
        <div><span class="stock-code">${stock.code} · 数据 ${stock.data_date}</span><h3>${stock.name}</h3></div>
        <div class="stock-score"><strong>${stock.score}</strong><span>筛选优先级</span></div>
      </div>
      <div class="stock-price">
        <strong>¥${stock.price}</strong>
        <span class="${stock.change >= 0 ? "positive-text" : "negative-text"}">${formatSigned(stock.change)}</span>
      </div>
      <div class="stock-metrics">
        <div class="stock-metric"><span>5日动量</span><strong>${formatSigned(stock.momentum_5d)}</strong></div>
        <div class="stock-metric"><span>20日动量</span><strong>${formatSigned(stock.momentum_20d)}</strong></div>
        <div class="stock-metric"><span>RSI 14</span><strong>${stock.rsi_14}</strong></div>
        <div class="stock-metric"><span>相对沪深300</span><strong>${formatSigned(stock.relative_20d)}</strong></div>
        <div class="stock-metric"><span>20日波动</span><strong>${stock.volatility_20d}%</strong></div>
        <div class="stock-metric"><span>成交额</span><strong>${stock.amount_yi}亿</strong></div>
        <div class="stock-metric"><span>同类形态次日概率</span><strong>${stock.setup_probability ?? "—"}%</strong></div>
        <div class="stock-metric"><span>同类形态胜率</span><strong>${stock.setup_win_rate ?? "—"}% · ${stock.setup_samples ?? 0}次</strong></div>
      </div>
      <p class="stock-reason">${stock.reason}</p>
      <div class="stock-rule"><span>观察条件：${stock.trigger}</span><span>失效条件：${stock.invalid}</span></div>
    </article>`).join("");
}

function reviewOutcome(row) {
  if (row.status !== "evaluated") return { label: "待结算", tone: "pending" };
  return row.correct
    ? { label: "命中", tone: "hit" }
    : { label: "未命中", tone: "miss" };
}

function renderReviewRows(rows, type) {
  if (!rows?.length) {
    return `<p class="selection-empty">暂无已到期记录；下一交易日收盘后会自动生成对照。</p>`;
  }
  return rows.map((row) => {
    const outcome = reviewOutcome(row);
    const title = type === "sector"
      ? `${row.sector_name || row.sector_key || "行业"} · 优先级 ${row.priority_score ?? "—"}`
      : `大盘第${row.horizon || 1}日 · ${row.direction || "—"}`;
    const dash = "\u2014";
    const separator = "\u00b7";
    const settledSide = Number(row.up_probability ?? 50) >= 50
      ? "\u4e8c\u5143\u7ed3\u7b97\uff1a\u770b\u6da8"
      : "\u4e8c\u5143\u7ed3\u7b97\uff1a\u770b\u8dcc";
    const prediction = `\u9884\u6d4b ${row.direction || dash} ${separator} ${settledSide} ${separator} \u4e0a\u6da8 ${row.up_probability ?? dash}% ${separator} \u9884\u671f ${formatSigned(row.expected_return ?? 0)}`;
    const actual = row.status === "evaluated"
      ? `实盘 ${formatSigned(row.actual_return ?? 0)} · ${row.actual_up ? "上涨" : "下跌"}`
      : `目标 ${row.target_date || "—"} 收盘后结算`;
    return `<div class="review-row">
      <time>${row.target_date?.slice(5) || "—"}</time>
      <div><strong>${title}</strong><p>${prediction}</p><small>${actual}</small></div>
      <span class="review-outcome ${outcome.tone}">${outcome.label}</span>
    </div>`;
  }).join("");
}

function renderPerformanceReview(review, fallbackMonitor, sectorFreshness = {}) {
  const market = review?.market || {};
  const sectors = review?.sectors || {};
  const marketMonitor = market.monitor || fallbackMonitor || {};
  const sectorMonitor = sectors.monitor || {};
  const sampleWarning = " \u00b7 \u6837\u672c\u4e0d\u8db3";
  const marketSummary = marketMonitor.evaluated_samples
    ? `\u547d\u4e2d ${marketMonitor.accuracy}%${marketMonitor.evaluated_samples < 60 ? sampleWarning : ""}`
    : "\u5b9e\u76d8\u6837\u672c\u79ef\u7d2f\u4e2d";
  const sectorSummary = sectorMonitor.evaluated_samples
    ? `\u547d\u4e2d ${sectorMonitor.accuracy}%${sectorMonitor.evaluated_samples < 60 ? sampleWarning : ""}`
    : "\u884c\u4e1a\u6837\u672c\u79ef\u7d2f\u4e2d";
  const marketEdge = Number(marketMonitor.edge_pp ?? 0);
  const marketEdgeText = `${marketEdge >= 0 ? "+" : ""}${marketEdge.toFixed(1)}pp`;
  const marketNote = marketMonitor.evaluated_samples
    ? `已结算 ${marketMonitor.evaluated_samples} · 基线 ${marketMonitor.baseline}% · 优势 ${marketEdgeText}`
    : marketMonitor.reason || "下一交易日收盘后自动结算第1日预测。";
  const sectorDayCount = sectorMonitor.evaluated_days ?? 0;
  const highPriorityText = Number(sectorMonitor.high_priority_samples ?? 0) > 0
    ? `${sectorMonitor.high_priority_accuracy}%`
    : "\u6682\u65e0\u5230\u671f\u9ad8\u4f18\u5148\u7ea7\u6837\u672c";
  const sectorLastDate = sectorMonitor.last_evaluated_date || "\u2014";
  const sectorFreshnessNote = sectorFreshness.status === "stale"
    ? sectorFreshness.message || "\u884c\u4e1a\u6570\u636e\u7b49\u5f85\u540c\u6b65"
    : sectorFreshness.status === "provisional"
      ? "\u5f53\u65e5\u5b9e\u65f6\u5feb\u7167\u5f85\u65e5\u7ebf\u590d\u6838"
      : "";
  const sectorNote = sectorMonitor.evaluated_samples
    ? `\u5df2\u7ed3\u7b97 ${sectorMonitor.evaluated_samples} \u6761 / ${sectorDayCount} \u4e2a\u4ea4\u6613\u65e5 \u00b7 \u6700\u8fd1\u7ed3\u7b97 ${sectorLastDate} \u00b7 \u57fa\u7ebf ${sectorMonitor.baseline}% \u00b7 \u9ad8\u4f18\u5148\u7ea7 ${highPriorityText}${sectorFreshnessNote ? ` \u00b7 ${sectorFreshnessNote}` : ""}`
    : `\u6bcf\u4e2a\u4e00\u7ea7\u884c\u4e1a\u7684\u6b21\u65e5\u9884\u6d4b\u4f1a\u5728\u76ee\u6807\u6536\u76d8\u540e\u9010\u7b14\u6bd4\u5bf9\u3002${sectorFreshnessNote ? ` ${sectorFreshnessNote}` : ""}`;
  $("#review-market-summary").textContent = marketSummary;
  $("#review-market-note").textContent = marketNote;
  $("#review-sector-summary").textContent = sectorSummary;
  $("#review-sector-note").textContent = sectorNote;
  $("#review-market-rows").innerHTML = renderReviewRows(market.rows, "market");
  $("#review-sector-rows").innerHTML = renderReviewRows(sectors.rows, "sector");
}

function renderEventRadar(radar, playbook) {
  if (!radar?.events?.length) {
    $("#event-risk-days").innerHTML = "";
    $("#events").innerHTML = `<article class="watchlist-empty panel">当前预测窗口没有已确认的重大日程事件；仍需关注突发公告。</article>`;
    return;
  }

  $("#event-risk-days").innerHTML = radar.daily_risk.map((day) => `
    <article class="event-day-summary ${riskClass(day.risk)}">
      <span>${day.date.slice(5)} · 周${day.weekday}</span>
      <strong>${day.risk}风险</strong>
      <small>${day.count ? `${day.count}个已确认事件` : "无已知重大日程"}</small>
    </article>`).join("");

  $("#events").innerHTML = radar.events.map((event) => `
    <article class="event-card panel ${riskClass(event.risk)}">
      <div class="event-card-top">
        <div>
          <span class="event-date">${event.date} · 周${event.weekday}</span>
          <span class="event-time">${event.release_time}</span>
        </div>
        <span class="event-risk">${event.risk}风险 · ${event.risk_score}/5</span>
      </div>
      <span class="event-category">${event.category} · ${event.status} · ${event.source_tier}</span>
      <h3>${event.title}</h3>
      <div class="event-tags">${event.affected_labels.map((label) => `<span>${label}</span>`).join("")}</div>
      <p class="event-mechanism">${event.mechanism}</p>
      <div class="event-scenarios">
        <p><strong>偏强条件</strong>${event.bull_case}</p>
        <p><strong>偏弱条件</strong>${event.bear_case}</p>
      </div>
      <div class="event-confirm"><strong>盘中确认</strong><span>${event.confirmation}</span></div>
      <a href="${event.url}" target="_blank" rel="noreferrer">${event.source_name} ↗</a>
    </article>`).join("");

  $("#shock-watch").innerHTML = radar.unscheduled_watch.map((item) => `
    <div class="shock-watch-item">
      <div><strong>${item.title}</strong><span>${item.risk}风险</span></div>
      <p>${item.monitor}</p>
      <small>${item.rule}</small>
      <a href="${item.url}" target="_blank" rel="noreferrer">监控来源 ↗</a>
    </div>`).join("");
  const collectionLabels = {
    live: "官方日程实时采集",
    partial: "部分实时、部分缓存",
    cached: "官方日程缓存容灾",
    manual: "人工核验日历",
    failed: "自动采集暂不可用",
  };
  const collection = radar.collection || {};
  $("#event-method-note").textContent = `${radar.method} · ${collectionLabels[collection.status] || "事件源状态未知"}`;

  const playbookLabels = { base: "基础", bull: "转强", bear: "失效", neutral: "观望", event: "事件" };
  $("#playbook").innerHTML = Object.entries(playbook).map(([key, value]) => `
    <div class="playbook-item"><span>${playbookLabels[key] || key}</span><p>${value}</p></div>`).join("");
}

function render(data) {
  const { meta, market, days, validation } = data;
  renderedSnapshotId = snapshotId(data);
  const automation = meta.automation;
  const automationLabel = automation?.quality_gate?.passed
    ? "自动更新已校验"
    : automation?.status === "manual"
      ? "手动更新已校验"
      : "发布快照";
  $("#data-status").textContent = `数据截至 ${meta.data_through} · ${automationLabel}`;
  $("#forecast-window").textContent = meta.forecast_window;
  $("#generated-time").textContent = new Date(meta.generated_at).toLocaleString("zh-CN", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  $("#weekly-direction").textContent = market.weekly_direction;
  $("#weekly-probability").textContent = `${market.weekly_up_probability}%`;
  $("#gauge").style.setProperty("--probability", market.weekly_up_probability);
  $("#risk-badge").textContent = `风险 ${market.risk_level}`;
  $("#weekly-return").textContent = formatSigned(market.weekly_expected_return);
  $("#weekly-range").textContent = `${market.weekly_range[0]}% — ${formatSigned(market.weekly_range[1])}`;
  $("#last-close").textContent = `${market.last_close} (${formatSigned(market.last_change)})`;
  $("#hero-summary").textContent = market.weekly_direction === "震荡"
    ? "模型没有发现值得重仓押注的方向优势；弱趋势与事件风险并存，现金也是一种仓位。"
    : `模型给出${market.weekly_direction}倾向，但只把它当作概率优势，不当作确定答案。`;

  renderPulse(data);
  renderDayAhead(data.day_ahead, days, meta);
  renderDays(days);
  renderChart(data.recent_chart, days);
  $("#drivers").innerHTML = data.drivers.map((item) => `
    <div class="driver">
      <div class="driver-top"><span class="driver-name">${item.name}</span><span class="driver-value ${impactClass(item.impact)}">${item.value}</span></div>
      <p>${item.detail}</p>
    </div>`).join("");
  renderBreadth(data.breadth);
  renderIntradayLab(data.intraday);

  $("#weekly-accuracy").textContent = `${validation.weekly_direction_accuracy}%`;
  $("#accuracy-context").textContent = `朴素多数类基线 ${validation.baseline_accuracy}% · AUC ${validation.auc}`;
  $("#strategy-return").textContent = formatSigned(validation.strategy_annual_return);
  $("#strategy-dd").textContent = `${validation.strategy_max_drawdown}%`;
  $("#benchmark-return").textContent = formatSigned(validation.benchmark_annual_return);
  $("#benchmark-dd").textContent = `${validation.benchmark_max_drawdown}%`;
  $("#sample-context").textContent = `${validation.samples} 个样本外观察 · 市场参与率 ${validation.active_days}%`;
  const monitor = data.performance_monitor || {};
  $("#model-health").textContent = monitor.label || "实盘样本积累中";
  $("#model-health-context").textContent = monitor.accuracy == null
    ? monitor.reason || "等待预测到期后自动核对实际涨跌。"
    : `实盘 ${monitor.evaluated_samples} 次 · 近${monitor.recent_window}次命中 ${monitor.accuracy}% · 基线 ${monitor.baseline}% · ${monitor.reason}`;

  $("#model-list").innerHTML = data.models.map((model) => `
    <div class="model-item">
      <div class="model-name-row"><strong>${model.name}</strong><span>权重 ${model.weight}%</span></div>
      <div class="model-bar"><i style="width:${model.probability}%"></i></div>
      <div class="model-numbers"><span>看涨概率</span><strong>${model.probability}%</strong></div>
    </div>`).join("");
  renderHorizonValidation(data.horizon_validation || []);
  renderPerformanceReview(
    data.performance_review,
    monitor,
    data.sector_forecast?.freshness || {},
  );
  renderSectorForecast(data.sector_forecast);
  renderWatchlist(data.watchlist || []);
  renderEventRadar(data.event_radar, data.playbook);

  const guardLabels = { capital_rule: "资金来源", position_cap: "仓位上限", loss_rule: "停止线", human_rule: "生命优先" };
  $("#risk-guard").innerHTML = Object.entries(data.risk_guard).map(([key, value]) => `
    <div class="guard-item"><strong>${guardLabels[key]}</strong><p>${value}</p></div>`).join("");

  $("#sources").innerHTML = data.sources.map((source) => `<a href="${source.url}" target="_blank" rel="noreferrer" title="${source.detail}">${source.name} ↗</a>`).join("");
  $("#disclaimer").textContent = data.disclaimer;
  $("#app-version").textContent = `Structure ${meta.structure_version || meta.release || meta.version} · Data release ${meta.release || meta.version} · Research only`;
  window.dispatchEvent(new CustomEvent("forecast:loaded", { detail: data }));
}

async function loadForecast() {
  const protectedHost = window.location.hostname.endsWith(".chatgpt.site");
  if (protectedHost) {
    try {
      const data = await window.SecureForecast.load();
      render(data);
      return data;
    } catch {
      const next = encodeURIComponent(
        `${window.location.pathname}${window.location.search}`,
      );
      window.location.replace(`/login.html?next=${next}`);
      throw new Error("需要登录，正在返回登录页");
    }
  }
  const response = await fetch(PUBLIC_FORECAST_URL, { cache: "no-store" });
  if (!response.ok) throw new Error("预测文件读取失败");
  const data = await response.json();
  render(data);
  return data;
}

$("#refresh-button").addEventListener("click", async () => {
  const button = $("#refresh-button");
  button.disabled = true;
  button.textContent = "检查中…";
  document.body.classList.add("loading");
  try {
    await loadForecast();
    showToast("已读取自动化系统发布的最新预测");
  } catch (error) {
    showToast(`${error.message}；上一版页面内容已保留。`);
  } finally {
    button.disabled = false;
    button.textContent = "检查最新数据";
    document.body.classList.remove("loading");
  }
});

async function checkForPublishedForecast() {
  const protectedHost = window.location.hostname.endsWith(".chatgpt.site");
  if (protectedHost || document.hidden || forecastPollInFlight) return;
  forecastPollInFlight = true;
  try {
    const response = await fetch(PUBLIC_FORECAST_URL, { cache: "no-store" });
    if (!response.ok) return;
    const data = await response.json();
    const nextSnapshotId = snapshotId(data);
    if (nextSnapshotId && nextSnapshotId !== renderedSnapshotId) {
      render(data);
      showToast("\u68c0\u6d4b\u5230\u81ea\u52a8\u66f4\u65b0\uff0c\u5df2\u5207\u6362\u81f3\u6700\u65b0\u9884\u6d4b");
    }
  } catch {
    // Keep the last accepted snapshot on screen; the next poll retries.
  } finally {
    forecastPollInFlight = false;
  }
}

function enableForecastPolling() {
  if (window.location.hostname.endsWith(".chatgpt.site")) return;
  window.setInterval(checkForPublishedForecast, 5 * 60 * 1000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) checkForPublishedForecast();
  });
}

const logoutButton = $("#logout-button");
if (
  window.location.hostname.endsWith(".chatgpt.site")
  || ["localhost", "127.0.0.1"].includes(window.location.hostname)
) {
  logoutButton.hidden = false;
  logoutButton.addEventListener("click", async () => {
    logoutButton.disabled = true;
    logoutButton.textContent = "正在退出…";
    window.SecureForecast.logout();
    window.location.replace("/login.html");
  });
}

loadForecast()
  .then(() => enableForecastPolling())
  .catch((error) => {
    $("#data-status").textContent = "\u6570\u636e\u8bfb\u53d6\u5931\u8d25";
    showToast(error.message);
  });

const $ = (selector) => document.querySelector(selector);
const formatSigned = (value, suffix = "%") => `${Number(value) > 0 ? "+" : ""}${value}${suffix}`;
const impactClass = (impact) => impact === "positive" ? "positive-text" : impact === "negative" ? "negative-text" : "neutral-text";
const cardClass = (direction) => direction === "偏强" ? "positive" : direction === "偏弱" ? "negative" : "neutral";

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.add("visible");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.remove("visible"), 3500);
}

function renderDays(days) {
  $("#day-grid").innerHTML = days.map((day) => {
    const range = Math.max(day.high_return - day.low_return, 0.01);
    const position = Math.max(4, Math.min(96, (day.expected_return - day.low_return) / range * 100));
    return `
      <article class="day-card panel ${cardClass(day.direction)}">
        <div class="day-date">
          <span>${day.date.slice(5)} · 周${day.weekday}</span>
          <span class="confidence">${day.confidence}置信</span>
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
      <text class="chart-label" x="${pad.left}" y="${height-5}">${history[0].date}</text>
      <text class="chart-label" x="${x(history.length-1)-34}" y="${height-5}">${history.at(-1).date}</text>
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
  ];
  $("#breadth-panel").innerHTML = `<div class="breadth-stats">${items.map(([name, value, cls]) => `<div class="breadth-stat"><span>${name}</span><strong class="${cls}">${value}</strong></div>`).join("")}</div>`;
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

function renderWatchlist(rows) {
  if (!rows || !rows.length) {
    $("#watchlist").innerHTML = `<article class="watchlist-empty panel">当前没有股票同时通过流动性、趋势、过热和波动过滤；空观察池也是有效结果。</article>`;
    return;
  }
  $("#watchlist").innerHTML = rows.map((stock) => `
    <article class="stock-card panel">
      <div class="stock-head">
        <div><span class="stock-code">${stock.code} · 数据 ${stock.data_date}</span><h3>${stock.name}</h3></div>
        <div class="stock-score"><strong>${stock.score}</strong><span>量价分</span></div>
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
      </div>
      <p class="stock-reason">${stock.reason}</p>
      <div class="stock-rule"><span>观察条件：${stock.trigger}</span><span>失效条件：${stock.invalid}</span></div>
    </article>`).join("");
}

function render(data) {
  const { meta, market, days, validation } = data;
  $("#data-status").textContent = `数据截至 ${meta.data_through}`;
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

  renderDays(days);
  renderChart(data.recent_chart, days);
  $("#drivers").innerHTML = data.drivers.map((item) => `
    <div class="driver">
      <div class="driver-top"><span class="driver-name">${item.name}</span><span class="driver-value ${impactClass(item.impact)}">${item.value}</span></div>
      <p>${item.detail}</p>
    </div>`).join("");
  renderBreadth(data.breadth);

  $("#weekly-accuracy").textContent = `${validation.weekly_direction_accuracy}%`;
  $("#accuracy-context").textContent = `朴素多数类基线 ${validation.baseline_accuracy}% · AUC ${validation.auc}`;
  $("#strategy-return").textContent = formatSigned(validation.strategy_annual_return);
  $("#strategy-dd").textContent = `${validation.strategy_max_drawdown}%`;
  $("#benchmark-return").textContent = formatSigned(validation.benchmark_annual_return);
  $("#benchmark-dd").textContent = `${validation.benchmark_max_drawdown}%`;
  $("#sample-context").textContent = `${validation.samples} 个样本外观察 · 市场参与率 ${validation.active_days}%`;

  $("#model-list").innerHTML = data.models.map((model) => `
    <div class="model-item">
      <div class="model-name-row"><strong>${model.name}</strong><span>权重 ${model.weight}%</span></div>
      <div class="model-bar"><i style="width:${model.probability}%"></i></div>
      <div class="model-numbers"><span>看涨概率</span><strong>${model.probability}%</strong></div>
    </div>`).join("");
  renderHorizonValidation(data.horizon_validation || []);
  renderWatchlist(data.watchlist || []);

  $("#events").innerHTML = data.events.map((event) => `
    <a class="event-item" href="${event.url}" target="_blank" rel="noreferrer">
      <span class="event-date">${event.date}</span>
      <div><h3>${event.title}</h3><p>${event.detail}</p></div>
      <span class="event-risk">${event.risk}风险</span>
    </a>`).join("");

  const playbookLabels = { base: "基础", bull: "转强", bear: "失效", neutral: "观望" };
  $("#playbook").innerHTML = Object.entries(data.playbook).map(([key, value]) => `
    <div class="playbook-item"><span>${playbookLabels[key]}</span><p>${value}</p></div>`).join("");

  const guardLabels = { capital_rule: "资金来源", position_cap: "仓位上限", loss_rule: "停止线", human_rule: "生命优先" };
  $("#risk-guard").innerHTML = Object.entries(data.risk_guard).map(([key, value]) => `
    <div class="guard-item"><strong>${guardLabels[key]}</strong><p>${value}</p></div>`).join("");

  $("#sources").innerHTML = data.sources.map((source) => `<a href="${source.url}" target="_blank" rel="noreferrer" title="${source.detail}">${source.name} ↗</a>`).join("");
  $("#disclaimer").textContent = data.disclaimer;
}

async function loadForecast() {
  const response = await fetch("/data/forecast.json", { cache: "no-store" });
  if (!response.ok) throw new Error("预测文件读取失败");
  const data = await response.json();
  render(data);
  return data;
}

$("#refresh-button").addEventListener("click", async () => {
  const button = $("#refresh-button");
  button.disabled = true;
  button.textContent = "计算中…";
  document.body.classList.add("loading");
  try {
    const response = await fetch("/api/refresh", { method: "POST" });
    if (!response.ok) throw new Error("当前页面为静态版本或行情源暂不可用");
    render(await response.json());
    showToast("已用最新收盘数据重新训练并更新");
  } catch (error) {
    showToast(`${error.message}；可在本机运行 py generate.py 更新。`);
  } finally {
    button.disabled = false;
    button.textContent = "重新计算";
    document.body.classList.remove("loading");
  }
});

loadForecast().catch((error) => {
  $("#data-status").textContent = "数据读取失败";
  showToast(error.message);
});

(() => {
  const DEFAULT_SECTORS = [
    "电力", "电子", "计算机", "通信", "银行", "非银金融",
    "食品饮料", "医药生物", "汽车", "有色金属", "基础化工", "其他",
  ];
  const weekdayLabels = { 一: "周一", 二: "周二", 三: "周三", 四: "周四", 五: "周五" };
  const currency = new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency: "CNY",
    maximumFractionDigits: 0,
  });
  let forecast = null;
  let savedState = { holdings: [], cash: 0, lastReport: null };
  let currentReport = null;
  let saveTimer = null;

  const text = (value) => String(value ?? "");
  const number = (value) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : 0;
  };
  const round = (value, digits = 1) => Number(number(value).toFixed(digits));
  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
  const escapeHtml = (value) => text(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

  function toast(message) {
    const node = document.querySelector("#toast");
    if (!node) return;
    node.textContent = message;
    node.classList.add("visible");
    window.clearTimeout(toast.timer);
    toast.timer = window.setTimeout(() => node.classList.remove("visible"), 3600);
  }

  function parseCsv(raw) {
    const rows = [];
    let row = [];
    let value = "";
    let quoted = false;
    const source = text(raw).replace(/^\uFEFF/, "");
    for (let index = 0; index < source.length; index += 1) {
      const character = source[index];
      if (quoted) {
        if (character === '"' && source[index + 1] === '"') {
          value += '"';
          index += 1;
        } else if (character === '"') {
          quoted = false;
        } else {
          value += character;
        }
      } else if (character === '"') {
        quoted = true;
      } else if (character === ",") {
        row.push(value.trim());
        value = "";
      } else if (character === "\n") {
        row.push(value.trim());
        if (row.some(Boolean)) rows.push(row);
        row = [];
        value = "";
      } else if (character !== "\r") {
        value += character;
      }
    }
    row.push(value.trim());
    if (row.some(Boolean)) rows.push(row);
    if (rows.length < 2) return [];

    const aliases = {
      code: ["股票代码", "代码", "code", "symbol"],
      name: ["股票名称", "名称", "name"],
      sector: ["所属行业", "行业", "板块", "sector"],
      cost: ["成本价", "成本", "cost", "cost_price"],
      price: ["现价", "最新价", "price", "current_price"],
      shares: ["股数", "数量", "shares", "quantity"],
    };
    const headers = rows[0].map((item) => item.trim().toLowerCase());
    const indexes = Object.fromEntries(
      Object.entries(aliases).map(([key, names]) => [
        key,
        headers.findIndex((header) => names.includes(header)),
      ]),
    );
    if (indexes.code < 0 || indexes.cost < 0 || indexes.price < 0 || indexes.shares < 0) {
      throw new Error("CSV 至少需要：股票代码、成本价、现价、股数");
    }
    return rows.slice(1).map((cells) => ({
      code: text(cells[indexes.code]).padStart(6, "0"),
      name: indexes.name >= 0 ? text(cells[indexes.name]) : "",
      sector: indexes.sector >= 0 ? text(cells[indexes.sector]) : "其他",
      cost: number(cells[indexes.cost]),
      price: number(cells[indexes.price]),
      shares: number(cells[indexes.shares]),
    })).filter((item) => item.code || item.name);
  }

  function sectorCatalog(data) {
    const modeled = data?.sector_forecast?.sectors || [];
    const names = modeled.map((sector) => sector.name);
    return [...new Set([...names, ...DEFAULT_SECTORS])];
  }

  function findSector(data, holding) {
    const sectors = data?.sector_forecast?.sectors || [];
    const aliasKeys = {
      电力: "utilities",
      公用事业: "utilities",
      半导体: "electronics",
      科技: "technology",
      券商: "nonbank",
      保险: "nonbank",
      军工: "defense",
      新能源: "power_equipment",
    };
    const aliasKey = aliasKeys[holding.sector];
    return sectors.find((sector) => (
      sector.name === holding.sector
      || sector.key === holding.sector
      || sector.group === holding.sector
      || sector.key === aliasKey
      || (
        text(holding.sector).length >= 2
        && sector.name.includes(holding.sector)
      )
    )) || null;
  }

  function matchingEvents(data, holding) {
    const sector = findSector(data, holding);
    const keys = new Set([
      holding.sector,
      sector?.name,
      sector?.key,
      sector?.group,
    ].filter(Boolean));
    if (sector?.key === "utilities") {
      keys.add("电力");
      keys.add("公用事业");
    }
    if (sector?.key === "electronics") keys.add("半导体");
    if (sector?.key === "technology") keys.add("科技");
    return (data?.event_radar?.events || []).filter((event) => {
      const labels = [...(event.affected_labels || []), ...(event.affected_sectors || [])];
      return labels.some((label) => keys.has(label));
    });
  }

  function analyzePortfolio(data, holdings, cash = 0, prior = null) {
    const normalized = holdings.map((holding) => {
      const costValue = number(holding.cost) * number(holding.shares);
      const currentValue = number(holding.price) * number(holding.shares);
      return {
        ...holding,
        code: text(holding.code).padStart(6, "0"),
        costValue,
        currentValue,
        pnl: currentValue - costValue,
        pnlPct: costValue ? (currentValue / costValue - 1) * 100 : 0,
      };
    });
    const invested = normalized.reduce((sum, item) => sum + item.currentValue, 0);
    const totalCost = normalized.reduce((sum, item) => sum + item.costValue, 0);
    const safeCash = Math.max(number(cash), 0);
    const assets = invested + safeCash;
    normalized.forEach((item) => {
      item.weight = assets ? item.currentValue / assets * 100 : 0;
      item.sectorModel = findSector(data, item);
      item.watch = (data?.watchlist || []).find((stock) => stock.code === item.code) || null;
      item.events = matchingEvents(data, item);
    });

    const sectorMap = new Map();
    for (const item of normalized) {
      const key = item.sector || "其他";
      const existing = sectorMap.get(key) || {
        name: key,
        value: 0,
        weight: 0,
        model: item.sectorModel,
        events: [],
      };
      existing.value += item.currentValue;
      existing.events.push(...item.events);
      if (!existing.model && item.sectorModel) existing.model = item.sectorModel;
      sectorMap.set(key, existing);
    }
    const sectors = [...sectorMap.values()]
      .map((item) => ({
        ...item,
        weight: assets ? item.value / assets * 100 : 0,
        events: [...new Map(item.events.map((event) => [event.id || event.title, event])).values()],
      }))
      .sort((a, b) => b.value - a.value);

    const maxStock = Math.max(0, ...normalized.map((item) => item.weight));
    const maxSector = Math.max(0, ...sectors.map((item) => item.weight));
    const positionRatio = assets ? invested / assets * 100 : 0;
    const exposedHighRisk = normalized.reduce((sum, item) => (
      item.events.some((event) => ["高", "极高"].includes(event.risk))
        ? sum + item.currentValue
        : sum
    ), 0);
    const eventExposure = assets ? exposedHighRisk / assets * 100 : 0;
    let riskPoints = 0;
    const risks = [];
    const addRisk = (severity, title, detail, points) => {
      risks.push({ severity, title, detail });
      riskPoints += points;
    };

    if (positionRatio > 90) {
      addRisk("high", "总仓位过高", `股票仓位 ${round(positionRatio)}%，现金缓冲很薄。`, 2);
    } else if (positionRatio > 75) {
      addRisk("medium", "总仓位偏高", `股票仓位 ${round(positionRatio)}%，遇到跳空时调整空间有限。`, 1);
    } else {
      risks.push({ severity: "low", title: "现金缓冲", detail: `股票仓位 ${round(positionRatio)}%，仍有 ${round(100 - positionRatio)}% 现金缓冲。` });
    }
    if (maxStock > 30) {
      addRisk("high", "单票集中", `最大单票占总资产 ${round(maxStock)}%，个股事件会显著影响组合。`, 2);
    } else if (maxStock > 20) {
      addRisk("medium", "单票偏集中", `最大单票占总资产 ${round(maxStock)}%。`, 1);
    }
    if (maxSector > 45) {
      addRisk("high", "行业集中", `最大行业占总资产 ${round(maxSector)}%，板块回撤难以分散。`, 2);
    } else if (maxSector > 30) {
      addRisk("medium", "行业偏集中", `最大行业占总资产 ${round(maxSector)}%。`, 1);
    }
    if (eventExposure > 35) {
      addRisk("high", "事件暴露较大", `${round(eventExposure)}% 资产对应下周高风险事件板块。`, 2);
    } else if (eventExposure > 15) {
      addRisk("medium", "存在事件暴露", `${round(eventExposure)}% 资产对应下周高风险事件板块。`, 1);
    }
    const marketRisk = data?.market?.risk_level || "未知";
    if (["高", "极高"].includes(marketRisk)) {
      addRisk("medium", "大盘风险环境偏高", `当前模型风险等级为“${marketRisk}”。`, 1);
    }
    if (!risks.some((item) => item.severity !== "low")) {
      risks.push({ severity: "low", title: "未触发集中度红线", detail: "规则体检未发现明显集中风险，但这不代表组合不会亏损。" });
    }
    const riskLabel = riskPoints >= 5 ? "高风险" : riskPoints >= 3 ? "需降风险" : riskPoints >= 1 ? "需关注" : "相对可控";

    const dayPlans = (data?.days || []).map((day, index) => {
      let mappedValue = 0;
      let expectedReturn = 0;
      let relativeProbability = 0;
      const eventNames = new Set();
      for (const item of normalized) {
        const sectorDay = item.sectorModel?.days?.[index];
        if (sectorDay) {
          mappedValue += item.currentValue;
          expectedReturn += sectorDay.expected_return * item.currentValue;
          relativeProbability += sectorDay.outperform_probability * item.currentValue;
        }
        for (const event of item.events.filter((event) => event.date === day.date || event.impact_date === day.date)) {
          eventNames.add(event.title);
        }
      }
      const mappedExpected = mappedValue ? expectedReturn / mappedValue : null;
      const mappedRelative = mappedValue ? relativeProbability / mappedValue : null;
      const caution = ["高", "极高"].includes(day.event_risk)
        ? "先观察事件落地与开盘量价，不在剧烈跳空时冲动决策。"
        : day.up_probability >= 54
          ? "若指数不破早盘低点且持仓行业强于沪深300，可维持观察；反之优先控制暴露。"
          : day.up_probability <= 46
            ? "若指数放量跌破前一日低点，优先执行预设风险线，不用预测替代纪律。"
            : "优势不足，重点看持仓行业是否跑赢沪深300与自身失效位。";
      return {
        ...day,
        label: weekdayLabels[day.weekday] || `第${index + 1}日`,
        mappedExpected: mappedExpected == null ? null : round(mappedExpected, 2),
        mappedRelative: mappedRelative == null ? null : round(mappedRelative, 1),
        events: [...eventNames],
        caution,
      };
    });

    const snapshot = {
      generatedAt: new Date().toISOString(),
      dataThrough: data?.meta?.data_through || "未知",
      assets: round(assets, 2),
      invested: round(invested, 2),
      positionRatio: round(positionRatio, 1),
      pnl: round(invested - totalCost, 2),
      pnlPct: totalCost ? round((invested / totalCost - 1) * 100, 2) : 0,
      riskPoints,
      riskLabel,
      holdingCount: normalized.length,
    };
    let change = null;
    if (prior?.assets != null) {
      change = {
        assets: round(snapshot.assets - number(prior.assets), 2),
        positionRatio: round(snapshot.positionRatio - number(prior.positionRatio), 1),
        pnl: round(snapshot.pnl - number(prior.pnl), 2),
        riskChanged: prior.riskLabel !== snapshot.riskLabel,
        priorRisk: prior.riskLabel,
      };
    }
    return {
      snapshot,
      holdings: normalized,
      sectors,
      risks,
      dayPlans,
      change,
      model: {
        marketDirection: data?.market?.weekly_direction || "未知",
        marketProbability: data?.market?.weekly_up_probability,
        forecastWindow: data?.meta?.forecast_window || "",
        validationAccuracy: data?.validation?.weekly_direction_accuracy,
        disclaimer: data?.disclaimer || "",
      },
    };
  }

  window.PortfolioDaily = { analyzePortfolio, parseCsv };
  if (typeof document === "undefined") return;

  function rowTemplate(holding = {}) {
    const sectors = sectorCatalog(forecast);
    const selectedSector = holding.sector || "其他";
    if (!sectors.includes(selectedSector)) sectors.push(selectedSector);
    return `
      <tr class="portfolio-row">
        <td>
          <div class="stock-input-stack">
            <input data-field="code" value="${escapeHtml(holding.code || "")}" maxlength="6" inputmode="numeric" placeholder="600000" aria-label="股票代码">
            <input data-field="name" value="${escapeHtml(holding.name || "")}" maxlength="20" placeholder="股票名称" aria-label="股票名称">
          </div>
        </td>
        <td>
          <select data-field="sector" aria-label="所属行业">
            ${sectors.map((sector) => `<option value="${escapeHtml(sector)}" ${sector === selectedSector ? "selected" : ""}>${escapeHtml(sector)}</option>`).join("")}
          </select>
        </td>
        <td><input data-field="cost" type="number" min="0" step="0.01" inputmode="decimal" value="${holding.cost || ""}" placeholder="0.00" aria-label="成本价"></td>
        <td><input data-field="price" type="number" min="0" step="0.01" inputmode="decimal" value="${holding.price || ""}" placeholder="0.00" aria-label="现价"></td>
        <td><input data-field="shares" type="number" min="0" step="100" inputmode="numeric" value="${holding.shares || ""}" placeholder="1000" aria-label="股数"></td>
        <td class="row-result"><strong>—</strong><span>等待录入</span></td>
        <td><button class="row-remove" type="button" title="删除此持仓" aria-label="删除此持仓">×</button></td>
      </tr>`;
  }

  function readRows() {
    return [...document.querySelectorAll(".portfolio-row")].map((row) => ({
      code: row.querySelector('[data-field="code"]').value.trim(),
      name: row.querySelector('[data-field="name"]').value.trim(),
      sector: row.querySelector('[data-field="sector"]').value,
      cost: number(row.querySelector('[data-field="cost"]').value),
      price: number(row.querySelector('[data-field="price"]').value),
      shares: number(row.querySelector('[data-field="shares"]').value),
    }));
  }

  function validHoldings(showErrors = false) {
    const rows = readRows().filter((item) => (
      item.code || item.name || item.cost || item.price || item.shares
    ));
    const invalid = rows.find((item) => (
      !/^\d{6}$/.test(item.code)
      || !item.name
      || item.cost <= 0
      || item.price <= 0
      || item.shares <= 0
    ));
    if (invalid && showErrors) {
      toast("请检查每行：6位代码、名称、成本价、现价和股数都必须有效。");
    }
    return invalid ? null : rows;
  }

  function updateLiveTotals() {
    const rows = readRows();
    let invested = 0;
    let cost = 0;
    document.querySelectorAll(".portfolio-row").forEach((row, index) => {
      const holding = rows[index];
      const value = holding.price * holding.shares;
      const itemCost = holding.cost * holding.shares;
      const pnlPct = itemCost ? (value / itemCost - 1) * 100 : null;
      invested += value;
      cost += itemCost;
      const result = row.querySelector(".row-result");
      result.innerHTML = value
        ? `<strong>${currency.format(value)}</strong><span class="${pnlPct >= 0 ? "positive-text" : "negative-text"}">${pnlPct >= 0 ? "+" : ""}${round(pnlPct, 2)}%</span>`
        : "<strong>—</strong><span>等待录入</span>";
    });
    const cash = Math.max(number(document.querySelector("#portfolio-cash").value), 0);
    document.querySelector("#portfolio-live-totals").innerHTML = `
      <span>持仓市值 <strong>${currency.format(invested)}</strong></span>
      <span>总资产 <strong>${currency.format(invested + cash)}</strong></span>
      <span>持仓盈亏 <strong class="${invested - cost >= 0 ? "positive-text" : "negative-text"}">${currency.format(invested - cost)}</strong></span>`;
  }

  async function persistState() {
    const next = {
      holdings: readRows(),
      cash: Math.max(number(document.querySelector("#portfolio-cash").value), 0),
      lastReport: savedState.lastReport,
      updatedAt: new Date().toISOString(),
    };
    try {
      await window.SecureForecast.savePersonalData(next);
      savedState = next;
    } catch (error) {
      if (error.message !== "PERSONAL_LOGIN_REQUIRED") {
        console.warn("Personal portfolio save failed", error);
      }
    }
  }

  function scheduleSave() {
    window.clearTimeout(saveTimer);
    saveTimer = window.setTimeout(persistState, 450);
  }

  function renderRows(holdings) {
    const list = holdings?.length ? holdings : [{}];
    document.querySelector("#portfolio-rows").innerHTML = list.map(rowTemplate).join("");
    updateLiveTotals();
  }

  function fillFromWatchlist(row) {
    const codeInput = row.querySelector('[data-field="code"]');
    const code = codeInput.value.trim().padStart(6, "0");
    codeInput.value = code;
    const match = (forecast?.watchlist || []).find((item) => item.code === code);
    if (!match) return;
    const name = row.querySelector('[data-field="name"]');
    const price = row.querySelector('[data-field="price"]');
    if (!name.value) name.value = match.name;
    if (!price.value) price.value = match.price;
    toast(`已引用观察池 ${match.data_date} 收盘价；请核对是否仍为最新价格。`);
    updateLiveTotals();
    scheduleSave();
  }

  function riskIcon(severity) {
    return severity === "high" ? "!" : severity === "medium" ? "△" : "✓";
  }

  function renderReport(report) {
    const snapshot = report.snapshot;
    const reportNode = document.querySelector("#portfolio-report");
    reportNode.hidden = false;
    document.querySelector("#report-meta").textContent = `生成于 ${new Date(snapshot.generatedAt).toLocaleString("zh-CN", { hour12: false })} · 模型数据截至 ${snapshot.dataThrough}`;
    document.querySelector("#report-summary").innerHTML = `
      <div><span>总资产</span><strong>${currency.format(snapshot.assets)}</strong></div>
      <div><span>股票仓位</span><strong>${snapshot.positionRatio}%</strong></div>
      <div><span>持仓盈亏</span><strong class="${snapshot.pnl >= 0 ? "positive-text" : "negative-text"}">${currency.format(snapshot.pnl)} · ${snapshot.pnlPct >= 0 ? "+" : ""}${snapshot.pnlPct}%</strong></div>
      <div><span>规则风险等级</span><strong class="risk-text-${snapshot.riskPoints >= 5 ? "high" : snapshot.riskPoints >= 3 ? "medium" : "low"}">${escapeHtml(snapshot.riskLabel)}</strong></div>
      <div><span>大盘周判断</span><strong>${escapeHtml(report.model.marketDirection)} · 上涨${report.model.marketProbability}%</strong></div>
      <div><span>样本外周命中</span><strong>${report.model.validationAccuracy}%</strong></div>`;

    document.querySelector("#report-risks").innerHTML = report.risks.map((risk) => `
      <div class="report-list-item ${risk.severity}">
        <span class="report-list-icon">${riskIcon(risk.severity)}</span>
        <div><strong>${escapeHtml(risk.title)}</strong><p>${escapeHtml(risk.detail)}</p></div>
      </div>`).join("");

    document.querySelector("#report-exposure").innerHTML = report.sectors.map((sector) => {
      const model = sector.model;
      const outlook = model
        ? `${model.weekly_outlook} · 周路径 ${model.weekly_expected_return >= 0 ? "+" : ""}${model.weekly_expected_return}%`
        : "暂无对应行业模型";
      const events = sector.events.length
        ? `事件：${sector.events.map((event) => event.title).join("、")}`
        : "未匹配到直接行业事件";
      return `
        <div class="exposure-item">
          <div class="exposure-row"><strong>${escapeHtml(sector.name)}</strong><span>${round(sector.weight)}%</span></div>
          <div class="exposure-bar"><i style="width:${clamp(sector.weight, 0, 100)}%"></i></div>
          <p>${escapeHtml(outlook)} · ${escapeHtml(events)}</p>
        </div>`;
    }).join("");

    document.querySelector("#report-holdings").innerHTML = report.holdings.map((holding) => {
      const model = holding.sectorModel;
      const modelText = model
        ? `${model.name}排名 ${model.rank} · 跑赢概率 ${model.outperform_probability}%`
        : "未匹配到行业模型，结论置信度较低";
      const watchText = holding.watch
        ? `观察池评分 ${holding.watch.score}；${holding.watch.invalid}`
        : "未进入当期量价观察池；这不等于看空。";
      const eventText = holding.events.length
        ? `事件暴露：${holding.events.map((event) => `${event.title}（${event.risk}）`).join("、")}`
        : "暂无直接匹配的下周日程事件";
      return `
        <article class="holding-diagnosis">
          <div class="holding-diagnosis-head">
            <div><span>${escapeHtml(holding.code)} · ${escapeHtml(holding.sector)}</span><h5>${escapeHtml(holding.name)}</h5></div>
            <strong class="${holding.pnlPct >= 0 ? "positive-text" : "negative-text"}">${holding.pnlPct >= 0 ? "+" : ""}${round(holding.pnlPct, 2)}%</strong>
          </div>
          <div class="holding-numbers"><span>权重 ${round(holding.weight)}%</span><span>市值 ${currency.format(holding.currentValue)}</span></div>
          <p><b>模型映射</b>${escapeHtml(modelText)}</p>
          <p><b>量价参照</b>${escapeHtml(watchText)}</p>
          <p><b>事件检查</b>${escapeHtml(eventText)}</p>
        </article>`;
    }).join("");

    document.querySelector("#report-plan").innerHTML = report.dayPlans.map((day) => `
      <article class="report-day ${["高", "极高"].includes(day.event_risk) ? "event-heavy" : ""}">
        <div class="report-day-head">
          <span>${escapeHtml(day.label)} · ${escapeHtml(day.date.slice(5))}</span>
          <strong>${escapeHtml(day.direction)} · 上涨${day.up_probability}%</strong>
        </div>
        <div class="report-day-metrics">
          <span>大盘中性路径 ${day.expected_return >= 0 ? "+" : ""}${day.expected_return}%</span>
          <span>持仓行业加权 ${day.mappedExpected == null ? "未覆盖" : `${day.mappedExpected >= 0 ? "+" : ""}${day.mappedExpected}%`}</span>
          <span>相对胜率 ${day.mappedRelative == null ? "未覆盖" : `${day.mappedRelative}%`}</span>
        </div>
        ${day.events.length ? `<p class="report-event-line">事件：${escapeHtml(day.events.join("、"))}</p>` : ""}
        <p>${escapeHtml(day.caution)}</p>
      </article>`).join("");

    const change = report.change;
    document.querySelector("#report-change").innerHTML = change
      ? `<strong>与上次日报相比</strong><span>总资产 ${change.assets >= 0 ? "+" : ""}${currency.format(change.assets)}；仓位 ${change.positionRatio >= 0 ? "+" : ""}${change.positionRatio} 个百分点；持仓盈亏变化 ${change.pnl >= 0 ? "+" : ""}${currency.format(change.pnl)}${change.riskChanged ? `；风险等级由“${escapeHtml(change.priorRisk)}”变为“${escapeHtml(snapshot.riskLabel)}”` : "；风险等级未变"}。</span>`
      : "<strong>首次生成</strong><span>下一次更新现价并生成时，将显示与本次日报的差异。</span>";
    reportNode.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function standaloneReport(report) {
    const summary = document.querySelector("#report-summary").innerHTML;
    const risks = document.querySelector("#report-risks").innerHTML;
    const exposure = document.querySelector("#report-exposure").innerHTML;
    const holdings = document.querySelector("#report-holdings").innerHTML;
    const plan = document.querySelector("#report-plan").innerHTML;
    const change = document.querySelector("#report-change").innerHTML;
    const generated = escapeHtml(new Date(report.snapshot.generatedAt).toLocaleString("zh-CN", { hour12: false }));
    return `<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>A股个人投资日报_${report.snapshot.dataThrough}</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f3f0e8;color:#17211c;font-family:Arial,"Microsoft YaHei",sans-serif}.page{width:min(1120px,calc(100% - 32px));margin:32px auto}.head{padding:34px;border-radius:24px;background:#17211c;color:#eff5f1}.head small{color:#83c5aa;letter-spacing:.14em}.head h1{margin:10px 0 8px;font-size:38px}.head p{margin:0;color:#b8c2bc}.summary{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:18px 0}.summary div,.box{padding:20px;border:1px solid #d9d9d2;border-radius:16px;background:white}.summary span{display:block;color:#6f7771;font-size:12px}.summary strong{display:block;margin-top:7px;font-size:19px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}.box h2{font-size:20px}.report-list-item{display:flex;gap:12px;padding:12px 0;border-top:1px solid #e4e4df}.report-list-item p,.exposure-item p,.holding-diagnosis p,.report-day p{margin:5px 0 0;color:#6f7771;font-size:12px;line-height:1.55}.exposure-item{padding:12px 0;border-top:1px solid #e4e4df}.exposure-row{display:flex;justify-content:space-between}.exposure-bar{height:5px;background:#e6ebe8;border-radius:8px;margin:8px 0}.exposure-bar i{display:block;height:100%;background:#0c6b4f}.holding-diagnosis-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}.holding-diagnosis,.report-day{padding:16px;border:1px solid #e1e1da;border-radius:13px}.holding-diagnosis-head,.report-day-head{display:flex;justify-content:space-between;gap:12px}.holding-diagnosis h5{margin:5px 0;font-size:18px}.holding-numbers,.report-day-metrics{display:flex;gap:12px;flex-wrap:wrap;color:#6f7771;font-size:11px}.holding-diagnosis b{display:block;color:#17211c}.report-day{margin-bottom:9px}.report-day-head strong{color:#0c6b4f}.report-change{padding:18px;border-radius:13px;background:#e7eee9;margin-top:16px}.report-change strong{display:block;margin-bottom:5px}.positive-text{color:#0c6b4f}.negative-text,.risk-text-high{color:#bb3e35}.risk-text-medium{color:#b17623}.risk-text-low{color:#0c6b4f}.disclaimer{margin-top:18px;padding:16px;border-left:3px solid #bb3e35;color:#6f7771;font-size:12px;line-height:1.6}@media(max-width:760px){.summary,.grid,.holding-diagnosis-grid{grid-template-columns:1fr}}@media print{body{background:white}.page{width:100%;margin:0}.head{-webkit-print-color-adjust:exact;print-color-adjust:exact}.box{break-inside:avoid}}
</style></head><body><main class="page">
<header class="head"><small>A-SHARE AUTO COMPASS · PRIVATE REPORT</small><h1>个人投资日报</h1><p>生成于 ${generated} · 模型数据截至 ${escapeHtml(report.snapshot.dataThrough)} · ${escapeHtml(report.model.forecastWindow)}</p></header>
<section class="summary">${summary}</section>
<div class="grid"><section class="box"><h2>01 风险体检</h2>${risks}</section><section class="box"><h2>02 行业与事件暴露</h2>${exposure}</section></div>
<section class="box" style="margin-top:16px"><h2>03 逐项持仓诊断</h2><div class="holding-diagnosis-grid">${holdings}</div></section>
<section class="box" style="margin-top:16px"><h2>04 下周一至周五观察计划</h2>${plan}</section>
<div class="report-change">${change}</div>
<p class="disclaimer">本文件根据用户录入数据和研究模型自动生成，不构成投资建议或收益保证。价格可能滞后，预测可能失效；请勿使用借贷或生活必需资金交易。</p>
</main></body></html>`;
  }

  async function generateReport() {
    if (!forecast) {
      toast("预测数据仍在读取，请稍后再试。");
      return;
    }
    const holdings = validHoldings(true);
    if (!holdings?.length) {
      toast("请至少录入一项完整持仓。");
      return;
    }
    const cash = Math.max(number(document.querySelector("#portfolio-cash").value), 0);
    currentReport = analyzePortfolio(forecast, holdings, cash, savedState.lastReport);
    renderReport(currentReport);
    savedState = {
      holdings,
      cash,
      lastReport: currentReport.snapshot,
      updatedAt: new Date().toISOString(),
    };
    try {
      await window.SecureForecast.savePersonalData(savedState);
      toast("日报已生成，持仓已在本浏览器加密保存。");
    } catch {
      toast("日报已生成；当前会话未能保存个人数据。");
    }
  }

  function exportReport() {
    if (!currentReport) {
      toast("请先生成今日投资日报。");
      return;
    }
    const blob = new Blob([standaloneReport(currentReport)], { type: "text/html;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `A股个人投资日报_${currentReport.snapshot.dataThrough}.html`;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast("单文件日报已导出，可用浏览器打开或打印为 PDF。");
  }

  async function restoreState() {
    try {
      const restored = await window.SecureForecast.loadPersonalData();
      if (restored?.holdings) savedState = restored;
    } catch {
      document.querySelector("#portfolio-storage-note").textContent = "检测到旧账号或旧密码保存的数据，当前会话无法解密；可以清空后重新录入。";
    }
    document.querySelector("#portfolio-cash").value = savedState.cash || 0;
    renderRows(savedState.holdings);
  }

  window.addEventListener("forecast:loaded", (event) => {
    forecast = event.detail;
    const rows = readRows();
    renderRows(rows);
  });

  document.querySelector("#portfolio-add").addEventListener("click", () => {
    document.querySelector("#portfolio-rows").insertAdjacentHTML("beforeend", rowTemplate({}));
    document.querySelector("#portfolio-rows tr:last-child input").focus();
  });
  document.querySelector("#portfolio-rows").addEventListener("input", () => {
    updateLiveTotals();
    scheduleSave();
  });
  document.querySelector("#portfolio-rows").addEventListener("change", scheduleSave);
  document.querySelector("#portfolio-rows").addEventListener("focusout", (event) => {
    if (event.target.matches('[data-field="code"]')) fillFromWatchlist(event.target.closest("tr"));
  });
  document.querySelector("#portfolio-rows").addEventListener("click", (event) => {
    if (!event.target.matches(".row-remove")) return;
    event.target.closest("tr").remove();
    if (!document.querySelector(".portfolio-row")) renderRows([{}]);
    updateLiveTotals();
    scheduleSave();
  });
  document.querySelector("#portfolio-cash").addEventListener("input", () => {
    updateLiveTotals();
    scheduleSave();
  });
  document.querySelector("#portfolio-import").addEventListener("click", () => {
    document.querySelector("#portfolio-csv").click();
  });
  document.querySelector("#portfolio-csv").addEventListener("change", async (event) => {
    const [file] = event.target.files;
    if (!file) return;
    try {
      const imported = parseCsv(await file.text());
      if (!imported.length) throw new Error("CSV 没有可导入的数据行");
      renderRows(imported);
      await persistState();
      toast(`已导入 ${imported.length} 项持仓，请核对行业和现价。`);
    } catch (error) {
      toast(error.message);
    } finally {
      event.target.value = "";
    }
  });
  document.querySelector("#portfolio-clear").addEventListener("click", async () => {
    if (!window.confirm("确定清空当前浏览器保存的全部持仓和上次日报吗？此操作无法撤销。")) return;
    window.SecureForecast.clearPersonalData();
    savedState = { holdings: [], cash: 0, lastReport: null };
    currentReport = null;
    document.querySelector("#portfolio-cash").value = 0;
    document.querySelector("#portfolio-report").hidden = true;
    renderRows([{}]);
    toast("已清空本机持仓。");
  });
  document.querySelector("#portfolio-generate").addEventListener("click", generateReport);
  document.querySelector("#portfolio-export").addEventListener("click", exportReport);

  const mode = window.SecureForecast.personalStorageMode();
  if (mode === "device") {
    document.querySelector("#portfolio-storage-note").textContent = "公开站点使用本机设备密钥加密；数据不会上传，但同一浏览器环境可读取。";
  }
  restoreState();
})();

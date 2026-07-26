import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";

const source = await readFile(
  new URL("../public/portfolio.js", import.meta.url),
  "utf8",
);
const context = {
  window: {},
  console,
  Intl,
  Date,
  Blob,
  URL,
  setTimeout,
  clearTimeout,
};
vm.runInNewContext(source, context, { filename: "portfolio.js" });
const { analyzePortfolio, parseCsv } = context.window.PortfolioDaily;

const imported = parseCsv(
  "股票代码,股票名称,所属行业,成本价,现价,股数\n"
  + "600900,长江电力,公用事业,28.5,30,1000\n",
);
assert.equal(imported.length, 1);
assert.equal(imported[0].code, "600900");
assert.equal(imported[0].shares, 1000);

const forecast = {
  meta: {
    data_through: "2026-07-24",
    forecast_window: "2026-07-27 — 2026-07-31",
  },
  market: {
    weekly_direction: "震荡",
    weekly_up_probability: 49,
    risk_level: "中",
  },
  validation: { weekly_direction_accuracy: 54.2 },
  days: [
    {
      date: "2026-07-27",
      weekday: "一",
      direction: "震荡",
      up_probability: 49,
      expected_return: 0.1,
      event_risk: "低",
    },
  ],
  sector_forecast: {
    sectors: [
      {
        key: "utilities",
        name: "公用事业",
        group: "电力",
        rank: 2,
        weekly_outlook: "相对中性",
        weekly_expected_return: 0.3,
        outperform_probability: 52,
        days: [
          {
            expected_return: 0.2,
            outperform_probability: 53,
          },
        ],
      },
    ],
  },
  event_radar: { events: [] },
  watchlist: [],
};
const report = analyzePortfolio(forecast, imported, 10000);
assert.equal(report.snapshot.assets, 40000);
assert.equal(report.snapshot.positionRatio, 75);
assert.equal(report.snapshot.pnl, 1500);
assert.equal(report.sectors[0].name, "公用事业");
assert.equal(report.sectors[0].model.key, "utilities");
assert.equal(report.dayPlans[0].mappedExpected, 0.2);
assert.equal(report.dayPlans[0].mappedRelative, 53);

console.log("Portfolio analysis verified.");

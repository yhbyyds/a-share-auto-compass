from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from market_forecast.data import (
    MarketDataError,
    fetch_market_breadth,
    fetch_market_data,
)
from market_forecast.model import generate_forecast
from market_forecast.watchlist import generate_watchlist


ROOT = Path(__file__).parent
PUBLIC = ROOT / "public"
FORECAST_FILE = PUBLIC / "data" / "forecast.json"

app = FastAPI(title="A股下周罗盘", version="1.0.0")


@app.get("/api/forecast")
def get_forecast() -> dict:
    if not FORECAST_FILE.exists():
        raise HTTPException(status_code=404, detail="尚未生成预测")
    return json.loads(FORECAST_FILE.read_text(encoding="utf-8"))


@app.post("/api/refresh")
def refresh_forecast() -> dict:
    try:
        data = fetch_market_data()
        forecast = generate_forecast(
            data,
            fetch_market_breadth(),
            generate_watchlist(data),
        )
    except MarketDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    FORECAST_FILE.parent.mkdir(parents=True, exist_ok=True)
    FORECAST_FILE.write_text(
        json.dumps(forecast, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return forecast


@app.get("/")
def home() -> FileResponse:
    return FileResponse(PUBLIC / "index.html")


app.mount("/", StaticFiles(directory=PUBLIC), name="public")

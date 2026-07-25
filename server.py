from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from automation import run_update
from market_forecast.data import MarketDataError


ROOT = Path(__file__).parent
PUBLIC = ROOT / "public"
FORECAST_FILE = PUBLIC / "data" / "forecast.json"

app = FastAPI(title="A股下周罗盘", version="1.0.0")


@app.get("/api/forecast")
def get_forecast() -> dict:
    if not FORECAST_FILE.exists():
        raise HTTPException(status_code=404, detail="尚未生成预测")
    import json

    return json.loads(FORECAST_FILE.read_text(encoding="utf-8"))


@app.post("/api/refresh")
def refresh_forecast() -> dict:
    try:
        forecast, _ = run_update()
    except (MarketDataError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return forecast


@app.get("/")
def home() -> FileResponse:
    return FileResponse(PUBLIC / "index.html")


app.mount("/", StaticFiles(directory=PUBLIC), name="public")

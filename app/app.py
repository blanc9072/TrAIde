"""
app.py — orchestration + API.

  GET /api/analyze?ticker=AAPL&horizon=30
     -> { ticker, generated_at, technical, sentiment, fundamental, errors }

The two legs are independent: if the analysis model errors (e.g. no API key,
yfinance hiccup) you still get the Kronos forecast, and vice-versa. Errors are
reported per-leg instead of failing the whole request.

Run:
  uvicorn app.app:app --reload
Then open http://127.0.0.1:8000/
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

# Load environment variables (e.g. ANTHROPIC_API_KEY) from the project-root .env
# before importing the submodules, which read env vars at import time. Explicit
# path so it works regardless of the process's working directory.
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .data import get_market_data
from .forecast import run_forecast
from .analysis import run_analysis

app = FastAPI(title="Stock Trading Helper")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten for production
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/api/analyze")
def analyze(
    ticker: str = Query(..., min_length=1, max_length=10),
    horizon: int = Query(30, ge=1, le=120),
):
    ticker = ticker.strip().upper()
    errors: dict[str, str] = {}

    # Shared data fetch — if this fails there is nothing to analyze.
    try:
        market = get_market_data(ticker, horizon=horizon)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Could not load data for {ticker}: {e}")

    # Technical leg (Kronos).
    technical = None
    try:
        technical = run_forecast(market, horizon=horizon)
    except Exception as e:
        errors["technical"] = str(e)

    # Analysis leg (sentiment + fundamental).
    sentiment = fundamental = None
    try:
        analysis = run_analysis(market)
        sentiment = analysis.get("sentiment")
        fundamental = analysis.get("fundamental")
    except Exception as e:
        errors["analysis"] = str(e)

    return {
        "ticker": ticker,
        "company": market.info.get("longName", ticker),
        "last_close": market.last_close,
        "currency": market.info.get("currency", "USD"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "technical": technical,
        "sentiment": sentiment,
        "fundamental": fundamental,
        "errors": errors,
    }


# Serve the dashboard (app/static/index.html) at /.
_static = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=_static, html=True), name="static")

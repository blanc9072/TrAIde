"""
data.py — market data layer.

Responsibilities:
  1. Pull historical OHLCV for a ticker (yfinance).
  2. Reshape it into exactly what Kronos expects:
       columns: open, high, low, close, volume, amount
       a separate x_timestamp Series for the history window
       a y_timestamp Series for the future horizon you want predicted
  3. Pull the raw fundamentals + news the analysis model will read.

Kronos gotchas handled here:
  - It wants an `amount` column (turnover). yfinance doesn't give it, so we
    approximate amount = close * volume. (For the no-volume Kronos variant you
    can drop volume/amount entirely — see prediction_wo_vol_example.py upstream.)
  - max_context for Kronos-small / -base is 512, so we cap the lookback.
  - predict() needs the *future* timestamps; for daily data we roll forward
    business days. For intraday you'd extend the grid by the bar interval.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import yfinance as yf

MAX_CONTEXT = 512  # hard limit for Kronos-small / -base


@dataclass
class MarketData:
    ticker: str
    x_df: pd.DataFrame          # open/high/low/close/volume/amount  (the lookback)
    x_timestamp: pd.Series      # timestamps for the lookback rows
    y_timestamp: pd.Series      # timestamps to forecast (length == horizon)
    last_close: float
    history: pd.DataFrame       # full lowercase OHLCV, for charting the past
    info: dict                  # fundamentals snapshot
    news: list                  # recent headlines


def _future_business_days(last: pd.Timestamp, n: int) -> pd.Series:
    """Roll forward `n` business days from the last known timestamp (daily bars)."""
    days = pd.bdate_range(start=last + pd.Timedelta(days=1), periods=n)
    return pd.Series(days, name="timestamps").reset_index(drop=True)


def get_market_data(
    ticker: str,
    horizon: int = 30,
    lookback: int = 400,
    period: str = "3y",
    interval: str = "1d",
) -> MarketData:
    """Fetch and shape everything the rest of the pipeline needs.

    lookback is clamped to MAX_CONTEXT. horizon is how many bars Kronos predicts.
    """
    lookback = min(lookback, MAX_CONTEXT)
    tk = yf.Ticker(ticker)

    raw = tk.history(period=period, interval=interval, auto_adjust=True)
    if raw.empty:
        raise ValueError(f"No price data returned for '{ticker}'.")

    # Normalize to lowercase OHLCV and derive the amount (turnover) proxy.
    df = raw.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].copy()
    df["amount"] = df["close"] * df["volume"]
    df.index = pd.to_datetime(df.index).tz_localize(None)

    window = df.tail(lookback)
    x_df = window[["open", "high", "low", "close", "volume", "amount"]].reset_index(drop=True)
    x_timestamp = pd.Series(window.index, name="timestamps").reset_index(drop=True)
    y_timestamp = _future_business_days(window.index[-1], horizon)

    # Fundamentals + news for the analysis model. yfinance is scrape-based and
    # occasionally flaky, so fail soft — the analysis leg can still run on partial data.
    try:
        info = tk.info or {}
    except Exception:
        info = {}
    try:
        news = tk.news or []
    except Exception:
        news = []

    return MarketData(
        ticker=ticker.upper(),
        x_df=x_df,
        x_timestamp=x_timestamp,
        y_timestamp=y_timestamp,
        last_close=float(window["close"].iloc[-1]),
        history=df,
        info=info,
        news=news,
    )

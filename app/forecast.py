"""
forecast.py — the technical leg (Kronos).

Wraps shiyu-coder/Kronos so the rest of the app only ever sees a clean dict:
the mean projected path, an uncertainty band, and the derived signals
(expected return, upside probability) that the dashboard renders.

How it works:
  Kronos is probabilistic. To get a *distribution* of outcomes (not one line)
  we sample several forecast paths and aggregate them. We run the predictor a
  few times with sampling on; each run is one possible future. From the bundle
  of paths we compute the mean, the p10/p90 band, and the share of paths that
  end above today's close (upside probability) — mirroring the official demo's
  Monte-Carlo dashboard.

Setup (once):
  git clone https://github.com/shiyu-coder/Kronos.git   # next to this project
  Weights auto-download from HuggingFace on first predict().
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

# Point this at your local Kronos clone so `from model import ...` resolves.
KRONOS_PATH = os.environ.get("KRONOS_PATH", os.path.expanduser("~/Kronos"))
sys.path.append(KRONOS_PATH)

_predictor = None  # lazy singleton — loading the model is expensive


def _get_predictor():
    """Load tokenizer + model once and cache. Uses GPU if KRONOS_DEVICE is set."""
    global _predictor
    if _predictor is None:
        from model import Kronos, KronosTokenizer, KronosPredictor  # type: ignore

        device = os.environ.get("KRONOS_DEVICE", "cpu")  # e.g. "cuda:0"
        tokenizer = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
        model = Kronos.from_pretrained("NeoQuasar/Kronos-small")
        _predictor = KronosPredictor(model, tokenizer, device=device, max_context=512)
    return _predictor


def run_forecast(
    market,                 # MarketData from data.py
    horizon: int = 30,
    n_paths: int = 5,       # Monte-Carlo paths; raise for smoother bands (slower on CPU)
    T: float = 1.0,         # sampling temperature
    top_p: float = 0.9,
) -> dict:
    """Return a JSON-serializable forecast summary for the technical leg."""
    predictor = _get_predictor()
    last_close = market.last_close

    # Collect one close-price path per sample. We loop single-sample predictions
    # so we control the aggregation; predict() also accepts sample_count if you
    # prefer to push sampling into the model call.
    paths = []
    for _ in range(n_paths):
        pred_df = predictor.predict(
            df=market.x_df,
            x_timestamp=market.x_timestamp,
            y_timestamp=market.y_timestamp,
            pred_len=horizon,
            T=T,
            top_p=top_p,
            sample_count=1,
            verbose=False,
        )
        paths.append(pred_df["close"].to_numpy())

    paths = np.vstack(paths)                 # shape: (n_paths, horizon)
    mean_path = paths.mean(axis=0)
    p10 = np.percentile(paths, 10, axis=0)
    p90 = np.percentile(paths, 90, axis=0)

    final_prices = paths[:, -1]
    expected_close = float(mean_path[-1])
    expected_return = (expected_close - last_close) / last_close
    upside_prob = float((final_prices > last_close).mean())

    dates = [d.strftime("%Y-%m-%d") for d in market.y_timestamp]

    return {
        "last_close": round(last_close, 2),
        "horizon_days": horizon,
        "expected_close": round(expected_close, 2),
        "expected_return_pct": round(expected_return * 100, 2),
        "upside_probability": round(upside_prob, 3),
        "projection": {
            "dates": dates,
            "mean": [round(float(v), 2) for v in mean_path],
            "lower": [round(float(v), 2) for v in p10],
            "upper": [round(float(v), 2) for v in p90],
        },
        # Tail of recent history so the dashboard can draw past + future together.
        "history_tail": _history_tail(market.history, n=120),
    }


def _history_tail(history: pd.DataFrame, n: int = 120) -> dict:
    tail = history.tail(n)
    return {
        "dates": [d.strftime("%Y-%m-%d") for d in tail.index],
        "close": [round(float(v), 2) for v in tail["close"]],
    }

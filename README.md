# TrAIde — Architecture README

> **What this app actually does:** Given a stock ticker and a time horizon, TrAIde runs three independent analyses and shows them side-by-side. It deliberately does **not** produce a buy or sell recommendation. The human is the decision-maker.

---

## Overview

TrAIde is a FastAPI web application that analyzes a stock on demand and returns three parallel signals:

- A **quantitative forecast** from a probabilistic model (Kronos), run via Monte Carlo simulation
- A **sentiment score** from Claude (claude-sonnet-4-6), derived from recent news headlines
- A **fundamental health score** from Claude, derived from financial ratios pulled via yfinance

These signals are presented separately on a dashboard. They may disagree. The app makes no attempt to reconcile them into a single verdict.

---

## How to Run

Per `Startup.txt`, the entry point is the FastAPI service inside `app/`. The files in the root (`main.py`, `analyzer.py`) are a **legacy batch pipeline** — they are not part of the live app and do not share code or state with it. Do not run both simultaneously expecting them to interact.

---

## Request Flow

A single HTTP request triggers this sequence:

```
GET /api/analyze?ticker=AAPL&horizon=30
        │
        ▼
   app/app.py          orchestrator
        │
        ├──► app/data.py        fetch and reshape market data (yfinance)
        │         │
        │         ▼
        │   MarketData object
        │   (OHLCV window, future timestamps, fundamentals blob, news headlines)
        │         │
        ├──► app/forecast.py    TECHNICAL LEG — Kronos Monte Carlo forecast
        │
        └──► app/analysis.py    ANALYSIS LEG — Claude scores sentiment + fundamentals
        │
        ▼
   JSON response → rendered by app/static/index.html
```

---

## Module-by-Module Breakdown

### `app/data.py` — Data Fetching

`get_market_data()` fetches three years of daily OHLCV data from yfinance and reshapes it for downstream use:

- Caps the lookback window at **512 bars**, which is Kronos's `MAX_CONTEXT` limit
- Computes an `amount` column as `close × volume` (an approximation)
- Generates future business-day timestamps for the forecast horizon
- Also fetches a fundamentals blob and recent headlines for the LLM leg

**Failure behavior:** yfinance's `.info` and `.news` endpoints are known to be unreliable. Both fail softly — they return empty data rather than raising an exception. The request continues with whatever data is available.

---

### `app/app.py` — Orchestrator

The key design decision is in `app.py` lines 59–73: the two analysis legs run in separate `try/except` blocks. Failures are collected into an `errors` dict and returned alongside whatever results succeeded.

Concretely:
- If the Anthropic API key is missing or the Claude call fails, you still get the Kronos forecast
- If Kronos fails to load, you still get the Claude analysis
- A request returns a hard 404 **only** if the shared data fetch (`data.py`) fails — because without market data, neither leg can run

---

### `app/forecast.py` — Technical Leg (Kronos Monte Carlo)

Kronos is a probabilistic price forecasting model. It is loaded once as a lazy singleton because instantiation is expensive.

The forecasting process (lines 63–77):
1. Runs the predictor `n_paths` times (default: 5), with sampling enabled each time, producing `n_paths` distinct possible price paths
2. Extracts three signals from the resulting bundle:
   - `expected_return_pct`: mean of final predicted prices vs. today's close
   - `upside_probability`: fraction of simulated paths that end above today's close — computed as `(final_prices > last_close).mean()`
   - `p10/p90 band`: 10th and 90th percentile of final prices, representing the uncertainty envelope

The output is a distribution, not a single prediction. "Confidence" here means how many simulated futures ended positively — it is not a model confidence score in the usual ML sense.

**Worth noting:** With only 5 paths by default, the upside probability estimate can be noisy (e.g., 3/5 = 60% vs. 2/5 = 40% is a single path flip). Verify what `n_paths` is set to in your environment before reading too much into this number.

---

### `app/analysis.py` — Analysis Leg (Claude)

This leg sends two things to Claude:
- Recent news headlines
- A curated set of approximately 12 fundamental ratios (lines 31–35), pulled from the yfinance fundamentals blob

Claude is instructed to return a strictly structured JSON object with two blocks (lines 37–51):

```json
{
  "sentiment": {
    "score": -1.0 to 1.0,
    "label": "string",
    "summary": "prose",
    "drivers": ["bullet", "bullet"]
  },
  "fundamental": {
    "score": -1.0 to 1.0,
    "label": "string",
    "summary": "prose",
    "highlights": ["bullet", "bullet"]
  }
}
```

There is one self-repair step (lines 92–95): if Claude wraps the JSON in markdown code fences, the parser strips them and retries. No further fallback exists.

The system prompt **explicitly instructs Claude not to make buy or sell recommendations.** Claude's role is scoring, not advising.

---

### `app/static/index.html` — Dashboard

Renders the three signals as meters using `paintMeter()`, which maps a −1…+1 score onto a 0–100% bar. Also draws the historical price chart overlaid with the forecast band (past history + p10/p90 projection).

The UI includes an explicit disclaimer (lines 136–137): technical, sentiment, and fundamental signals can disagree, and nothing shown is financial advice.

---

## Signal Summary

| Signal | Source | Method | Output |
|---|---|---|---|
| Technical | Kronos model | Monte Carlo over N sampled paths | expected return %, upside probability, p10/p90 band |
| Sentiment | Claude + news headlines | LLM scoring to fixed JSON schema | score −1…+1, label, bullet drivers |
| Fundamental | Claude + yfinance ratios | LLM scoring to fixed JSON schema | score −1…+1, label, bullet highlights |

---

## What the App Does Not Do

- It does not fuse the three signals into a combined recommendation
- It does not backtest any signal against historical performance
- It does not account for position sizing, portfolio context, or risk tolerance
- The Kronos model's accuracy over the configured horizon is not characterized in this codebase
- The quality of Claude's fundamental scoring depends entirely on the ratios yfinance returns, which vary by ticker and may be incomplete

---

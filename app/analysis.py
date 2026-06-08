"""
analysis.py — the second model (sentiment + fundamental).

This leg reads recent news headlines and the fundamentals snapshot from
yfinance and asks an LLM to return a *structured* judgement: a sentiment score
+ summary, and a fundamental score + summary, as strict JSON the dashboard can
render directly.

Why an LLM here: it does both jobs (read messy headlines AND interpret a grab
bag of financial ratios) with one dependency and one schema. Alternatives:
  - Sentiment only, no API: a local FinBERT model
    (ProsusAI/finbert via transformers) scoring each headline, then average.
  - Fundamentals: you can also just surface the raw ratios and skip the LLM
    summary if you'd rather show numbers than prose.

Set ANTHROPIC_API_KEY in your environment. The key is read by the SDK; never
hard-code it.
"""

from __future__ import annotations

import json
import os

import anthropic

MODEL = os.environ.get("ANALYSIS_MODEL", "claude-sonnet-4-6")

# Fundamentals we pull out of yfinance's .info blob. All optional — many tickers
# are missing some — so the prompt is told to work with whatever is present.
FUNDAMENTAL_KEYS = [
    "trailingPE", "forwardPE", "priceToBook", "profitMargins",
    "revenueGrowth", "earningsGrowth", "returnOnEquity", "debtToEquity",
    "freeCashflow", "marketCap", "dividendYield", "beta",
]

_SCHEMA = """Return ONLY valid JSON, no prose, no markdown fences, matching exactly:
{
  "sentiment": {
    "score": <float -1.0 to 1.0>,
    "label": "<bearish|neutral|bullish>",
    "summary": "<2-3 sentences on the news mood and why>",
    "drivers": ["<short headline-level driver>", "..."]
  },
  "fundamental": {
    "score": <float -1.0 to 1.0>,
    "label": "<weak|fair|strong>",
    "summary": "<2-3 sentences on financial health: valuation, growth, profitability, leverage>",
    "highlights": ["<metric-level observation>", "..."]
  }
}"""


def _trim_news(news: list, k: int = 10) -> list[str]:
    titles = []
    for item in news[:k]:
        # yfinance news shape varies; handle both flat and nested layouts.
        title = item.get("title") or item.get("content", {}).get("title")
        if title:
            titles.append(title)
    return titles


def run_analysis(market) -> dict:
    """Return {'sentiment': {...}, 'fundamental': {...}} for the analysis leg."""
    headlines = _trim_news(market.news)
    fundamentals = {k: market.info.get(k) for k in FUNDAMENTAL_KEYS if market.info.get(k) is not None}
    company = market.info.get("longName") or market.ticker

    user_block = (
        f"Company: {company} ({market.ticker})\n"
        f"Last close: {market.last_close}\n\n"
        f"Recent headlines:\n" + ("\n".join(f"- {h}" for h in headlines) or "- (none available)") +
        f"\n\nFundamentals (raw, some may be missing):\n{json.dumps(fundamentals, indent=2)}"
    )

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
    resp = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=(
            "You are an equity research assistant. Judge sentiment from the "
            "headlines and fundamental health from the metrics. Be balanced and "
            "concrete; do not give buy/sell recommendations.\n\n" + _SCHEMA
        ),
        messages=[{"role": "user", "content": user_block}],
    )

    text = "".join(block.text for block in resp.content if block.type == "text").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Model wrapped it in fences despite instructions — strip and retry once.
        cleaned = text.replace("```json", "").replace("```", "").strip()
        return json.loads(cleaned)

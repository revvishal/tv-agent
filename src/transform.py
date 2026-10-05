"""Turns scraped data into the exact payload shape the REST API expects."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from dateutil import parser as dateparser

from .config import Company
from .scraper import ForecastData, QuarterRow, compute_consolidated_score


# Strings that scrapers/APIs commonly use for "no data". These must never be
# sent to the REST API; they are replaced with the field's default instead.
_NULL_LIKE_STRINGS = {"", "na", "n/a", "nan", "null", "none", "nil", "-", "--", "—"}


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return True
    if isinstance(value, str) and value.strip().lower() in _NULL_LIKE_STRINGS:
        return True
    return False


def _num(value: Any) -> float | int:
    """Numeric field -> the value, or 0 when missing/unusable."""
    if _is_missing(value) or isinstance(value, bool):
        return 0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    if math.isnan(number) or math.isinf(number):
        return 0
    return value if isinstance(value, (int, float)) else number


def _text(value: Any) -> str:
    """Text field -> the stripped string, or "" when missing."""
    if _is_missing(value):
        return ""
    return str(value).strip()


def _int(value: Any) -> int:
    return int(_num(value))


def _format_quarter_date(raw_label: str, fallback_index: int) -> str:
    """Converts a TradingView column header (e.g. 'Mar 2025', '31 Mar 2025')
    into the 'DD-Mon-YY' style used in the sample payload (e.g. '28-Mar-25').
    Falls back to the raw label if it can't be parsed as a date."""
    try:
        dt = dateparser.parse(raw_label, default=datetime(2000, 1, 1))
        return dt.strftime("%d-%b-%y")
    except (ValueError, OverflowError):
        return raw_label or f"period_{fallback_index}"


def _order_chronologically(quarters: list[QuarterRow]) -> list[QuarterRow]:
    """TradingView usually lists the most recent quarter first. We want
    oldest -> newest (matching the sample payload) so QoQ % change math
    reads left-to-right correctly. Sorts by parsed date when possible,
    otherwise assumes the scraped order was newest-first and reverses it."""
    parsed = []
    for q in quarters:
        try:
            parsed.append((dateparser.parse(q.quarter_label, default=datetime(2000, 1, 1)), q))
        except (ValueError, OverflowError):
            parsed = None
            break

    if parsed:
        parsed.sort(key=lambda pair: pair[0])
        return [q for _, q in parsed]

    return list(reversed(quarters))


def build_quarters_payload(quarters: list[QuarterRow]) -> list[dict]:
    ordered = _order_chronologically(quarters)

    result = []
    prev_revenue = None
    for i, q in enumerate(ordered):
        if q.revenue is not None and prev_revenue:
            revenue_change = round((q.revenue - prev_revenue) / prev_revenue * 100, 2)
        else:
            revenue_change = 0

        result.append(
            {
                "quarter": _format_quarter_date(q.quarter_label, i),
                "revenue": (q.revenue / 1000000000) if q.revenue is not None else 0,  # Converting into Billion
                "revenueChange": revenue_change,
                "epsYoY": _num(q.eps_yoy),
                "ebitda": (q.ebitda / 1000000000) if q.ebitda is not None else 0,  # Converting into Billion
                "opMargin": _num(q.op_margin),
            }
        )
        if q.revenue is not None:
            prev_revenue = q.revenue

    return result


def build_summary(company: Company, quarters_payload: list[dict]) -> str:
    """
    Best-effort, rule-based one-line summary of the trend (TradingView does
    not publish a qualitative summary sentence like the sample payload's
    "Volatile earnings with weak margins" - that reads like editorial
    commentary). Swap this out for an LLM call or your own logic if you
    want richer summaries.
    """
    if not quarters_payload:
        return "Insufficient data to summarize."

    # Zeros are "no data" defaults, so ignore them when judging the trend.
    margins = [q["opMargin"] for q in quarters_payload if q["opMargin"]]
    changes = [q["revenueChange"] for q in quarters_payload if q["revenueChange"]]

    revenue_desc = "growing" if changes and sum(changes) > 0 else "under pressure"
    if changes and (max(changes) - min(changes) > 8):
        revenue_desc = "volatile"

    margin_desc = ""
    if margins:
        avg_margin = sum(margins) / len(margins)
        margin_desc = "weak margins" if avg_margin < 10 else "healthy margins"

    parts = [p for p in [f"Revenue {revenue_desc}", margin_desc] if p]
    return ", ".join(parts).capitalize() + "."


def build_payload(
        company: Company,
        quarters: list[QuarterRow] | None,
        forecast: ForecastData | None,
) -> dict:
    """Builds the PUT body. No field is ever null/NA: missing numbers become 0
    and missing text becomes "" (also when a whole scrape failed)."""
    quarters_payload = build_quarters_payload(quarters or [])
    summary = build_summary(company, quarters_payload)

    rating_counts = forecast.rating_counts if forecast else {}
    consolidated_score = _num(compute_consolidated_score(rating_counts))

    return {
        "company": _text(company.company),
        "summary": summary,
        "financials": {
            "ticker": _text(company.ticker),
            "company": _text(company.company),
            "summary": summary,
            "fetchedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "quarters": quarters_payload,
        },
        "analystRecommendation": {
            "ticker": _text(company.ticker),
            "consolidatedScore": consolidated_score,
            "consolidatedRating": _text(forecast.consolidated_rating_text) if forecast else "",
            "totalAnalysts": _int(forecast.total_analysts) if forecast else 0,
            # NOTE: TradingView's public forecast page does not list individual
            # firm-level ratings (firm name / target price / date) - only the
            # aggregate distribution. Left empty here.
            "ratings": [],
        },
    }

"""Turns scraped data into the exact payload shape the REST API expects."""
from __future__ import annotations

from datetime import datetime, timezone

from dateutil import parser as dateparser

from .config import Company
from .scraper import ForecastData, QuarterRow, compute_consolidated_score


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
                "revenue": (q.revenue/1000000000) if q.revenue is not None else None, # Converting into Billion
                "revenueChange": revenue_change,
                "epsYoY": q.eps_yoy,
                "ebitda": (q.ebitda/1000000000) if q.ebitda is not None else None, # Converting into Billion
                "opMargin": q.op_margin,
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

    margins = [q["opMargin"] for q in quarters_payload if q["opMargin"] is not None]
    changes = [q["revenueChange"] for q in quarters_payload if q["revenueChange"] is not None]

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
    quarters: list[QuarterRow],
    forecast: ForecastData,
) -> dict:
    quarters_payload = build_quarters_payload(quarters)
    summary = build_summary(company, quarters_payload)
    consolidated_score = compute_consolidated_score(forecast.rating_counts)

    return {
        "company": company.company,
        "summary": summary,
        "financials": {
            "ticker": company.ticker,
            "company": company.company,
            "summary": summary,
            "fetchedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "quarters": quarters_payload,
        },
        "analystRecommendation": {
            "ticker": company.ticker,
            "consolidatedScore": consolidated_score,
            "consolidatedRating": forecast.consolidated_rating_text,
            "totalAnalysts": forecast.total_analysts,
            # NOTE: TradingView's public forecast page does not list individual
            # firm-level ratings (firm name / target price / date) like the
            # sample payload's "Motilal Oswal" / "ICICI Securities" entries -
            # only the aggregate distribution. Left empty here; populate from
            # another data source if you need per-firm rows.
            "ratings": [],
        },
    }

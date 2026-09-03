"""
Scrapes two TradingView pages per ticker using Playwright:

  1. Quarterly income statement:
     https://www.tradingview.com/symbols/{tv_symbol}/financials-income-statement/?statements-period=FQ

  2. Analyst forecast / price target:
     https://www.tradingview.com/symbols/{tv_symbol}/forecast-price-target/

IMPORTANT - READ THIS:
TradingView renders its tables with CSS-module class names that are hashed
and change on every deploy (e.g. class="value-abc123"). That means fixed
CSS selectors break often. Instead, this scraper locates rows by their stable
``data-name`` attribute (e.g. data-name="Total revenue") and reads each
period's number from the ``div[class*="value-"]`` cell inside that column
(YoY % changes come from ``div[class*="change-"]``). Grabbing a row's flat
innerText interleaves values with change-percentages and breaks parsing.
See README.md -> "When the scraper breaks".

Run with SCRAPER_DEBUG=1 to save a screenshot + full HTML of every page
visited into ./debug/, which makes it much faster to fix a broken selector.
"""
from __future__ import annotations

import asyncio
import re
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from .config import Company, DEBUG_DIR

FINANCIALS_URL = "https://www.tradingview.com/symbols/{symbol}/financials-income-statement/?statements-period=FQ"
FORECAST_URL = "https://www.tradingview.com/symbols/{symbol}/forecast-price-target/"

logger = logging.getLogger("tv-agent")

# Row ``data-name`` values, in priority order. On the income-statement tab,
# EPS YoY is the ``change-*`` cell on the diluted-EPS row (subtitle "YoY
# growth"), not a separate metric row. Operating margin usually lives on the
# Statistics tab; when absent we derive it from Operating income / revenue.
ROW_LABELS = {
    "revenue": ["Total revenue", "Revenue"],
    "ebitda": ["EBITDA"],
    "op_margin": ["Operating margin, %", "Operating margin"],
    "operating_income": ["Operating income"],
}

EPS_YOY_SOURCES: list[tuple[list[str], str]] = [
    (["Diluted earnings per share (diluted EPS)"], "change"),
    (
        [
            "EPS diluted YoY change, %",
            "EPS diluted YoY growth, %",
            "Basic EPS YoY change, %",
            "EPS diluted YoY",
        ],
        "value",
    ),
]

RATING_BUCKETS = ["Strong buy", "Buy", "Neutral", "Sell", "Strong sell"]
# Standard 1-5 analyst-score weighting used by most aggregators.
RATING_WEIGHTS = {"Strong sell": 1, "Sell": 2, "Neutral": 3, "Buy": 4, "Strong buy": 5}


@dataclass
class QuarterRow:
    quarter_label: str  # raw text from the column header, e.g. "Mar 2025" or "28 Mar 2025"
    revenue: float | None
    eps_yoy: float | None
    ebitda: float | None
    op_margin: float | None


@dataclass
class ForecastData:
    consolidated_rating_text: str | None  # e.g. "Buy", "Strong Buy", "Hold"
    total_analysts: int | None
    rating_counts: dict[str, int]  # bucket -> count
    price_target_current: float | None
    price_target_max: float | None
    price_target_min: float | None
    price_target_avg: float | None


async def _dump_debug(page: Page, name: str) -> None:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    await page.screenshot(path=str(DEBUG_DIR / f"{name}.png"), full_page=True)
    html = await page.content()
    (DEBUG_DIR / f"{name}.html").write_text(html)


def _parse_number(text: str) -> float | None:
    """Parses TradingView-formatted numbers like '59.92B', '−2.32%', '8.19%', '—'."""
    if not text:
        return None
    # TradingView wraps values in bidi marks and narrow no-break spaces.
    text = re.sub(r"[\u202a\u202b\u202c\u202d\u202e\ufeff\u200e\u200f]", "", text)
    text = text.replace("\u202f", " ").replace("\u00a0", " ")
    text = text.strip().replace(",", "")
    if text in ("—", "-", "", "N/A", "NM"):
        return None
    text = text.replace("−", "-")  # TradingView uses a unicode minus sign
    text = text.rstrip("%")

    multiplier = 1.0
    if text and text[-1] in ("K", "M", "B", "T"):
        multiplier = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}[text[-1]]
        text = text[:-1]

    match = re.search(r"-?\d+(\.\d+)?", text)
    if not match:
        return None
    return float(match.group()) * multiplier


async def _find_financial_row(page: Page, label_candidates: list[str]):
    """Locates a statement row by its ``data-name`` attribute."""
    for label in label_candidates:
        row = page.locator(f'div[data-name="{label}"]').first
        try:
            if await row.count() > 0:
                return row
        except PlaywrightTimeoutError:
            continue
    return None


async def _extract_row_columns(
    page: Page,
    row,
    *,
    kind: str = "value",
) -> list[str | None]:
    """
    Reads one cell per column from a statement row, aligned with the header
    columns. ``kind="value"`` selects ``div[class*="value-"]``; ``kind="change"``
    selects the YoY ``div[class*="change-"]`` cell. Locked (paywalled) columns
    return None.
    """
    values_wrap = row.locator('div[class*="values-"]').first
    try:
        if await values_wrap.count() == 0:
            return []
    except PlaywrightTimeoutError:
        return []

    containers = values_wrap.locator('div[class*="container-"]')
    try:
        n = await containers.count()
    except PlaywrightTimeoutError:
        return []

    out: list[str | None] = []
    for i in range(n):
        col = containers.nth(i)
        try:
            if await col.locator('[class*="lockButton"]').count() > 0:
                out.append(None)
                continue

            if kind == "change":
                cell = col.locator('div[class*="change-"]').first
            else:
                cell = col.locator('div[class*="value-"]').first

            if await cell.count() == 0:
                out.append(None)
                continue

            txt = (await cell.inner_text()).strip()
            out.append(txt or None)
        except PlaywrightTimeoutError:
            out.append(None)
    return out


async def _find_row_columns(
    page: Page,
    label_candidates: list[str],
    *,
    kind: str = "value",
) -> list[str | None] | None:
    """Finds a row by ``data-name`` and returns column-aligned cell text."""
    row = await _find_financial_row(page, label_candidates)
    logger.info(f" _find_row_columns... row {row}" )
    if row is None:
        return None
    columns = await _extract_row_columns(page, row, kind=kind)
    logger.info(f" _find_row_columns... columns {columns}" )
    return columns if any(v is not None for v in columns) else None


async def _find_first_row_columns(
    page: Page,
    sources: list[tuple[list[str], str]],
) -> list[str | None] | None:
    """Try several (label, kind) pairs until one row yields data."""
    for labels, kind in sources:
        columns = await _find_row_columns(page, labels, kind=kind)
        if columns:
            return columns
    return None


async def _find_period_headers(page: Page) -> list[str | None]:
    """
    Reads quarter-end dates from the sticky header above the statement table.
    Each header column exposes its date in ``div[class*="subvalue-"]`` (e.g.
    "Sep 2024"). Locked columns and the trailing TTM column may have no date.
    """
    fin_block = page.locator(".js-financials-block-init-ssr").first
    header_values = fin_block.locator('div[class*="values-"]').first
    try:
        if await header_values.count() == 0:
            return []
    except PlaywrightTimeoutError:
        return []

    containers = header_values.locator('div[class*="container-"]')
    try:
        n = await containers.count()
    except PlaywrightTimeoutError:
        return []

    headers: list[str | None] = []
    for i in range(n):
        col = containers.nth(i)
        try:
            sub = col.locator('div[class*="subvalue-"]').first
            if await sub.count() > 0:
                headers.append((await sub.inner_text()).strip())
                continue

            val = col.locator('div[class*="value-"]').first
            if await val.count() > 0 and (await val.inner_text()).strip() == "TTM":
                headers.append("TTM")
            else:
                headers.append(None)
        except PlaywrightTimeoutError:
            headers.append(None)
        logger.info(f" _find_period_headers... {headers}" )
    return headers


def _compute_op_margin(
    operating_income: list[str | None] | None,
    revenue: list[str | None] | None,
    index: int,
) -> float | None:
    """Derives operating margin % when the Statistics-tab row is unavailable."""
    if not operating_income or not revenue:
        return None
    if index >= len(operating_income) or index >= len(revenue):
        return None
    oi = _parse_number(operating_income[index] or "")
    rev = _parse_number(revenue[index] or "")
    if oi is None or rev is None or rev == 0:
        return None
    return round(oi / rev * 100, 2)


async def scrape_financials(page: Page, company: Company, debug: bool) -> list[QuarterRow]:
    url = FINANCIALS_URL.format(symbol=company.tv_symbol)
    await page.goto(url, wait_until="networkidle", timeout=45000)
    await page.wait_for_timeout(2000)  # let client-side rendering settle

    if debug:
        await _dump_debug(page, f"{company.ticker}_financials")

    headers = await _find_period_headers(page)
    revenue_cols = await _find_row_columns(page, ROW_LABELS["revenue"])
    eps_yoy_cols = await _find_first_row_columns(page, EPS_YOY_SOURCES)
    ebitda_cols = await _find_row_columns(page, ROW_LABELS["ebitda"])
    op_margin_cols = await _find_row_columns(page, ROW_LABELS["op_margin"])
    operating_income_cols = await _find_row_columns(page, ROW_LABELS["operating_income"])

    if not revenue_cols:
        raise RuntimeError(
            f"[{company.ticker}] Could not find a 'Total revenue' row on {url}. "
            f"TradingView's markup may have changed - see README.md."
        )

    n_cols = max(len(headers), len(revenue_cols))
    quarters = []
    for i in range(n_cols):
        label = headers[i] if i < len(headers) else None
        if not label or label == "TTM":
            continue

        rev_raw = revenue_cols[i] if i < len(revenue_cols) else None
        if rev_raw is None:
            continue

        margin_raw = op_margin_cols[i] if op_margin_cols and i < len(op_margin_cols) else None
        op_margin_val = _parse_number(margin_raw) if margin_raw else None
        if op_margin_val is None:
            op_margin_val = _compute_op_margin(operating_income_cols, revenue_cols, i)

        quarters.append(
            QuarterRow(
                quarter_label=label,
                revenue=_parse_number(rev_raw),
                eps_yoy=_parse_number(eps_yoy_cols[i])
                if eps_yoy_cols and i < len(eps_yoy_cols) and eps_yoy_cols[i]
                else None,
                ebitda=_parse_number(ebitda_cols[i])
                if ebitda_cols and i < len(ebitda_cols) and ebitda_cols[i]
                else None,
                op_margin=op_margin_val,
            )
        )
    return quarters


async def scrape_forecast(page: Page, company: Company, debug: bool) -> ForecastData:
    url = FORECAST_URL.format(symbol=company.tv_symbol)
    await page.goto(url, wait_until="networkidle", timeout=45000)
    await page.wait_for_timeout(2000)

    if debug:
        await _dump_debug(page, f"{company.ticker}_forecast")

    body_text = await page.inner_text("body")

    # Overall rating word, e.g. "Buy" / "Strong Buy" / "Hold" / "Sell".
    rating_match = re.search(
        r"\b(Strong Buy|Buy|Neutral|Hold|Sell|Strong Sell)\b",
        body_text,
    )
    consolidated_rating_text = rating_match.group(1) if rating_match else None

    # "Based on X analysts giving stock ratings in the past 3 months."
    total_match = re.search(r"Based on\s+(\d+)\s+analysts", body_text)
    total_analysts = int(total_match.group(1)) if total_match else None

    # Per-bucket counts, e.g. "Strong buy ... 6", "Buy ... 1"
    rating_counts: dict[str, int] = {}
    for bucket in RATING_BUCKETS:
        m = re.search(rf"{bucket}\s*[\r\n]+\s*(\d+)", body_text, re.IGNORECASE)
        if m:
            rating_counts[bucket] = int(m.group(1))

    # "max estimate of 20,150 and a min estimate of 14,230"
    max_match = re.search(r"max estimate of\s*([\d,]+\.?\d*)", body_text)
    min_match = re.search(r"min estimate of\s*([\d,]+\.?\d*)", body_text)
    price_target_max = _parse_number(max_match.group(1)) if max_match else None
    price_target_min = _parse_number(min_match.group(1)) if min_match else None

    # "Current" / "Avg" values shown near the fan chart.
    current_match = re.search(r"Current\s*[\r\n]+\s*([\d,]+\.?\d*)", body_text)
    avg_match = re.search(r"Avg\s*[\r\n]+[^\d\-]*([\d,]+\.?\d*)", body_text)
    price_target_current = _parse_number(current_match.group(1)) if current_match else None
    price_target_avg = _parse_number(avg_match.group(1)) if avg_match else None

    return ForecastData(
        consolidated_rating_text=consolidated_rating_text,
        total_analysts=total_analysts,
        rating_counts=rating_counts,
        price_target_current=price_target_current,
        price_target_max=price_target_max,
        price_target_min=price_target_min,
        price_target_avg=price_target_avg,
    )


def compute_consolidated_score(rating_counts: dict[str, int]) -> float | None:
    """
    TradingView's forecast page shows a rating *word* and a gauge needle but
    no raw numeric score. This computes a conventional 1 (Strong Sell) to 5
    (Strong Buy) weighted-average score from the analyst count distribution,
    matching how most aggregators derive a "3.8"-style score. Remove/replace
    this if you'd rather source the score elsewhere.
    """
    total = sum(rating_counts.values())
    if not total:
        return None
    weighted = sum(RATING_WEIGHTS[b] * c for b, c in rating_counts.items() if b in RATING_WEIGHTS)
    return round(weighted / total, 2)

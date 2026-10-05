"""
Entry point. For every company in config/companies.yaml:
  1. Scrape quarterly financials from TradingView
  2. Scrape analyst rating / price target forecast from TradingView
  3. Transform both into the target API payload shape
  4. PUT the payload to your REST API

Usage:
    python -m src.main
"""
import asyncio
import logging
import random
import sys

from playwright.async_api import async_playwright

from .api_client import push_financials
from .config import Company, Settings, load_companies, load_settings
from .scraper import scrape_financials, scrape_forecast
from .transform import build_payload

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("tv-agent")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


async def process_company(page, settings: Settings, company: Company) -> bool:
    # If a scrape fails we still push the payload, with 0 / "" defaults
    # instead of null/NA, so the API never receives missing values.
    quarters = None
    forecast = None

    logger.info("[%s] Scraping financials...", company.ticker)
    try:
        quarters = await scrape_financials(page, company, settings.debug)
    except Exception:
        logger.warning(
            "[%s] Financials scrape failed - using default values (0 / empty)",
            company.ticker,
            exc_info=True,
        )

    logger.info("[%s] Scraping forecast / analyst rating...", company.ticker)
    try:
        forecast = await scrape_forecast(page, company, settings.debug)
    except Exception:
        logger.warning(
            "[%s] Forecast scrape failed - using default values (0 / empty)",
            company.ticker,
            exc_info=True,
        )

    payload = build_payload(company, quarters, forecast)

    logger.info("[%s] Pushing payload to API...", company.ticker)
    push_financials(settings, company.ticker, payload)
    return True


async def run() -> int:
    settings = load_settings()
    companies = load_companies()

    failures = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not settings.headed)
        context = await browser.new_context(user_agent=USER_AGENT)
        page = await context.new_page()

        for i, company in enumerate(companies):
            try:
                await process_company(page, settings, company)
            except Exception:
                logger.warning(
                    "[%s] FAILED - continuing with next company",
                    company.ticker,
                    exc_info=True,
                )
                failures.append(company.ticker)

            # Politeness delay between companies. Skip after the last one.
            if i < len(companies) - 1:
                delay = settings.request_delay_seconds + random.uniform(0, 3)
                await asyncio.sleep(delay)

        await browser.close()

    total = len(companies)
    failed = len(failures)
    if not failures:
        logger.info("All %d companies processed successfully.", total)
        return 0

    failure_rate = failed / total if total else 0
    summary = (
        f"Completed with {failed}/{total} ticker failure(s): {', '.join(failures)}"
    )
    if failure_rate > 0.5:
        logger.error(summary)
        return 1

    logger.warning(summary)
    logger.info(
        "Treating run as success (%d/%d tickers succeeded; failure threshold is >50%%).",
        total - failed,
        total,
        )
    return 0


def main() -> None:
    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    main()

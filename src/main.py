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
    logger.info("[%s] Scraping financials...", company.ticker)
    quarters = await scrape_financials(page, company, settings.debug)

    logger.info("[%s] Scraping forecast / analyst rating...", company.ticker)
    forecast = await scrape_forecast(page, company, settings.debug)

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
                logger.exception("[%s] FAILED - continuing with next company", company.ticker)
                failures.append(company.ticker)

            # Politeness delay between companies. Skip after the last one.
            if i < len(companies) - 1:
                delay = settings.request_delay_seconds + random.uniform(0, 3)
                await asyncio.sleep(delay)

        await browser.close()

    if failures:
        logger.error("Completed with failures: %s", ", ".join(failures))
        return 1

    logger.info("All %d companies processed successfully.", len(companies))
    return 0


def main() -> None:
    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    main()

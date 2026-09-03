# TradingView -> REST API Agent

Scrapes quarterly financials and analyst forecasts from TradingView for a
configurable list of companies, and PUTs them to your REST API in the shape
of your sample payload.

## What it does

For each company in `config/companies.yaml`:
1. Opens `https://www.tradingview.com/symbols/{tv_symbol}/financials-income-statement/?statements-period=FQ`
   and scrapes Revenue, EPS YoY change, EBITDA, and Operating margin per quarter.
2. Opens `https://www.tradingview.com/symbols/{tv_symbol}/forecast-price-target/`
   and scrapes the consolidated rating (Buy/Hold/Sell), analyst count, rating
   distribution, and price-target min/max/avg/current.
3. Builds the payload and sends `PUT {API_BASE_URL}/api/financials/{ticker}`
   with your JWT bearer token, matching the sample curl.

## Before you run this: please read

**Scraping TradingView is against their Terms of Service**, and their pages
use hashed CSS class names that change on redeploys, plus bot-detection that
can block headless browsers entirely. This project is built to work
technically, but:
- It may get rate-limited or blocked, especially run frequently or on a
  shared cloud IP.
- Selectors are matched by *visible label text* (e.g. "EBITDA") rather than
  CSS classes, which is more durable but not bulletproof.
- For anything business-critical, an official/paid financial data API is a
  more reliable long-term source than scraping.

## Known data gaps vs. your sample payload

- `analystRecommendation.ratings` (individual firms like "Motilal Oswal"
  with their own target price) **cannot be sourced from TradingView's public
  forecast page** — it only shows the aggregate distribution shown in your
  screenshot, not a per-firm breakdown. This is left as an empty list;
  plug in another data source if you need it.
- `analystRecommendation.consolidatedScore` (e.g. `3.8`) isn't shown as a
  raw number on the page either — it's computed here as a weighted average
  of the Strong Sell(1)..Strong Buy(5) distribution counts. Adjust
  `compute_consolidated_score()` in `src/scraper.py` if you want a
  different formula.
- `summary` (e.g. "Volatile earnings with weak margins") is editorial
  commentary that TradingView doesn't publish either. A simple rule-based
  version is generated in `src/transform.py::build_summary()` — swap it for
  an LLM call if you want a better summary.
- Quarter dates: TradingView's column headers are sometimes month+year only
  (e.g. "Mar 2025") without a specific day, so the day defaults to `01`
  (e.g. `01-Mar-25`) rather than the real quarter-end day (`28-Mar-25`).
  If TradingView's headers do include a full date on the live page, it will
  be used as-is — check a debug dump (below) to confirm.

## Setup

```bash
cp .env.example .env
# edit .env: set API_BASE_URL and JWT_TOKEN
# edit config/companies.yaml: list your tickers
```

### Run locally (no Docker)

```bash
pip install -r requirements.txt
playwright install --with-deps chromium
python -m src.main
```

### Run with Docker (recommended for the cloud VM)

```bash
docker build -t tv-agent .
docker run -d --name tv-agent \
  --env-file .env \
  tv-agent
```

The container runs the pipeline once immediately on startup, then again
daily at 06:00 UTC via cron. Check logs with:

```bash
docker logs -f tv-agent
```

To change the schedule, edit `crontab` (standard 5-field cron syntax) and
rebuild the image.

## When the scraper breaks

TradingView changes its DOM periodically. To debug:

1. Set `SCRAPER_DEBUG=1` in `.env`.
2. Run `python -m src.main` (or `docker run` again).
3. Look in `./debug/` for `{ticker}_financials.png` / `.html` and
   `{ticker}_forecast.png` / `.html` — a screenshot and full HTML of exactly
   what the scraper saw.
4. Compare the row labels TradingView is currently using against
   `ROW_LABELS` in `src/scraper.py`, and add any new wording as another
   candidate string.
5. For the forecast page, the scraper parses `page.inner_text("body")` with
   regexes (`consolidated_rating_text`, `total_analysts`, etc. in
   `src/scraper.py::scrape_forecast`) — check the saved HTML if a regex
   stops matching.

## Project layout

```
config/companies.yaml   # the list of tickers to process
src/config.py           # env vars + companies.yaml loading
src/scraper.py          # Playwright scraping of both TradingView pages
src/transform.py        # builds the exact API payload shape
src/api_client.py       # PUTs the payload to your REST API
src/main.py             # orchestrates the pipeline for all companies
Dockerfile, crontab,
entrypoint.sh           # containerize + schedule with cron
```

## Extending

- **More metrics**: add a label to `ROW_LABELS` in `src/scraper.py`, wire it
  through `QuarterRow`, and add it to the payload in `src/transform.py`.
- **More companies**: just add entries to `config/companies.yaml`.
- **Different schedule**: edit `crontab`.
- **Alerting on failure**: `src/main.py` already logs and exits with status
  `1` if any company fails — wire that exit code into your cloud
  scheduler's alerting (e.g. a failed cron job notification).

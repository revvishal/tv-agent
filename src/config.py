"""Loads environment variables and the companies.yaml config file."""
import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent.parent
COMPANIES_FILE = ROOT_DIR / "config" / "companies.yaml"
DEBUG_DIR = ROOT_DIR / "debug"


@dataclass(frozen=True)
class Settings:
    api_base_url: str
    jwt_token: str
    request_delay_seconds: float
    headed: bool
    debug: bool


def load_settings() -> Settings:
    api_base_url = os.environ.get("API_BASE_URL", "").rstrip("/")
    jwt_token = os.environ.get("JWT_TOKEN", "")

    if not api_base_url:
        raise RuntimeError("API_BASE_URL is not set. Copy .env.example to .env and fill it in.")
    if not jwt_token or jwt_token == "your_jwt_token_here":
        raise RuntimeError("JWT_TOKEN is not set. Copy .env.example to .env and fill it in.")

    return Settings(
        api_base_url=api_base_url,
        jwt_token=jwt_token,
        request_delay_seconds=float(os.environ.get("REQUEST_DELAY_SECONDS", "8")),
        headed=os.environ.get("PLAYWRIGHT_HEADED", "0") == "1",
        debug=os.environ.get("SCRAPER_DEBUG", "0") == "1",
    )


@dataclass(frozen=True)
class Company:
    ticker: str
    tv_symbol: str
    company: str


def load_companies() -> list[Company]:
    with open(COMPANIES_FILE, "r") as f:
        raw = yaml.safe_load(f)

    companies = []
    for entry in raw.get("companies", []):
        companies.append(
            Company(
                ticker=entry["ticker"],
                tv_symbol=entry["tv_symbol"],
                company=entry["company"],
            )
        )
    if not companies:
        raise RuntimeError(f"No companies found in {COMPANIES_FILE}")
    return companies

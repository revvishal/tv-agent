"""Sends the built payload to your REST API, matching the sample curl call."""
import logging

import requests

from .config import Settings

logger = logging.getLogger(__name__)


def push_financials(settings: Settings, ticker: str, payload: dict) -> None:
    url = f"{settings.api_base_url}/api/financials/{ticker}"
    headers = {
        "Authorization": f"Bearer {settings.jwt_token}",
        "Content-Type": "application/json",
    }

    resp = requests.put(url, json=payload, headers=headers, timeout=30)

    if resp.status_code >= 400:
        logger.error("PUT %s failed [%s]: %s", url, resp.status_code, resp.text[:500])
        resp.raise_for_status()

    logger.info("PUT %s succeeded [%s]", url, resp.status_code)

"""Sends the built payload to your REST API, matching the sample curl call."""
import logging
import math
from typing import Any

import requests

from .config import Settings

logger = logging.getLogger(__name__)


def strip_nulls(value: Any) -> Any:
    """Last line of defence before the PUT: no None/NaN/'NA'-style value is
    ever sent. Numbers default to 0; everything else to ""."""
    if isinstance(value, dict):
        cleaned = {}
        for k, v in value.items():
            if isinstance(v, (dict, list)):
                cleaned[k] = strip_nulls(v)
            elif v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
                cleaned[k] = 0
            elif isinstance(v, str) and v.strip().lower() in {"na", "n/a", "nan", "null", "none", "-", "--", "—"}:
                cleaned[k] = ""
            else:
                cleaned[k] = v
        return cleaned
    if isinstance(value, list):
        return [strip_nulls(v) for v in value if v is not None]
    return value


def push_financials(settings: Settings, ticker: str, payload: dict) -> None:
    url = f"{settings.api_base_url}/api/financials/{ticker}"
    headers = {
        "Authorization": f"Bearer {settings.jwt_token}",
        "Content-Type": "application/json",
    }

    resp = requests.put(url, json=strip_nulls(payload), headers=headers, timeout=30)

    if resp.status_code >= 400:
        logger.error("PUT %s failed [%s]: %s", url, resp.status_code, resp.text[:500])
        resp.raise_for_status()

    logger.info("PUT %s succeeded [%s]", url, resp.status_code)

"""Finance data: USD-BRL quote and configurable headline scraping."""

from __future__ import annotations

import logging
from typing import Any

import requests
from bs4 import BeautifulSoup

from core.utils import ScraperResult, request_timeout

logger = logging.getLogger(__name__)

AWESOMEAPI_URL = "https://economia.awesomeapi.com.br/json/last/{pair}"


def _fetch_usd_brl(settings) -> dict[str, Any]:
    finance_cfg = settings.get("finance") or {}
    pair = finance_cfg.get("awesomeapi_pair", "USD-BRL")
    url = AWESOMEAPI_URL.format(pair=pair)

    headers = {}
    if settings.awesomeapi_token:
        headers["x-api-key"] = settings.awesomeapi_token

    response = requests.get(url, headers=headers, timeout=request_timeout(settings))
    response.raise_for_status()
    body = response.json()

    key = pair.replace("-", "")
    quote = body.get(key) or next(iter(body.values()), {})
    return {
        "pair": pair,
        "bid": quote.get("bid"),
        "ask": quote.get("ask"),
        "timestamp": quote.get("create_date") or quote.get("timestamp"),
    }


def _scrape_target(target: dict[str, Any], settings) -> list[dict[str, str]]:
    url = target.get("url")
    if not url:
        return []

    if target.get("use_playwright"):
        logger.warning("Playwright requested for %s but is not installed in MVP; skipping", target.get("name"))
        return []

    response = requests.get(
        url,
        timeout=request_timeout(settings),
        headers={"User-Agent": "Mozilla/5.0 (compatible; DailyJournalBot/1.0)"},
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "lxml")
    selector = (target.get("selectors") or {}).get("headlines", "h2 a, h3 a")
    max_items = int((target.get("selectors") or {}).get("max_items", 5))

    headlines: list[dict[str, str]] = []
    for element in soup.select(selector):
        title = element.get_text(strip=True)
        href = element.get("href", "")
        if not title:
            continue
        headlines.append({"title": title, "url": href})
        if len(headlines) >= max_items:
            break
    return headlines


def fetch(settings) -> ScraperResult:
    section = "finance"
    finance_cfg = settings.get("finance") or {}
    data: dict[str, Any] = {"headlines": []}
    errors: list[str] = []

    try:
        data["usd_brl"] = _fetch_usd_brl(settings)
    except Exception as exc:
        logger.warning("USD-BRL fetch failed: %s", exc)
        errors.append(f"USD-BRL: {exc}")

    for target in finance_cfg.get("scrape_targets", []):
        try:
            headlines = _scrape_target(target, settings)
            data["headlines"].extend(
                {"source": target.get("name", "unknown"), **item} for item in headlines
            )
        except Exception as exc:
            logger.warning("Finance scrape failed for %s: %s", target.get("name"), exc)
            errors.append(f"{target.get('name', 'target')}: {exc}")

    if not data.get("usd_brl") and not data["headlines"]:
        return ScraperResult(section=section, status="error", error="; ".join(errors) or "No finance data")

    status = "ok"
    error_msg = None
    if errors:
        status = "partial" if data.get("usd_brl") or data["headlines"] else "error"
        error_msg = "; ".join(errors)

    return ScraperResult(section=section, status=status, data=data, error=error_msg)

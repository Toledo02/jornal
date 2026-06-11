"""Gaming deals, RSS headlines, and GitHub trending."""

from __future__ import annotations

import logging
from typing import Any

import requests
from bs4 import BeautifulSoup

from core.utils import ScraperResult, request_timeout
from scrapers import news_rss

logger = logging.getLogger(__name__)

CHEAPSHARK_URL = "https://www.cheapshark.com/api/1.0/deals"


def _fetch_cheapshark_deals(settings) -> list[dict[str, Any]]:
    gaming_cfg = settings.get("gaming") or {}
    cheapshark_cfg = gaming_cfg.get("cheapshark") or {}
    if not cheapshark_cfg.get("enabled", True):
        return []

    params = {
        "upperPrice": cheapshark_cfg.get("max_price", 0),
        "pageSize": cheapshark_cfg.get("max_deals", 5),
        "sortBy": "Recent",
    }
    response = requests.get(CHEAPSHARK_URL, params=params, timeout=request_timeout(settings))
    response.raise_for_status()
    deals = response.json()

    results: list[dict[str, Any]] = []
    for deal in deals[: params["pageSize"]]:
        results.append(
            {
                "title": deal.get("title"),
                "sale_price": deal.get("salePrice"),
                "normal_price": deal.get("normalPrice"),
                "store": deal.get("storeID"),
                "url": f"https://www.cheapshark.com/redirect?dealID={deal.get('dealID')}",
            }
        )
    return results


def _fetch_github_trending(settings) -> list[dict[str, str]]:
    github_cfg = settings.get("github") or {}
    base_url = github_cfg.get("trending_url", "https://github.com/trending")
    languages = github_cfg.get("trending_languages") or [""]
    max_repos = int(github_cfg.get("max_repos", 5))

    repos: list[dict[str, str]] = []
    for language in languages:
        url = f"{base_url}/{language}" if language else base_url
        response = requests.get(
            url,
            timeout=request_timeout(settings),
            headers={"User-Agent": "Mozilla/5.0 (compatible; DailyJournalBot/1.0)"},
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "lxml")

        for article in soup.select("article.Box-row"):
            title_el = article.select_one("h2 a")
            if not title_el:
                continue
            name = title_el.get_text(strip=True).replace("\n", " ").strip()
            href = title_el.get("href", "")
            desc_el = article.select_one("p")
            repos.append(
                {
                    "name": name,
                    "url": f"https://github.com{href}",
                    "description": desc_el.get_text(strip=True) if desc_el else "",
                    "language_filter": language or "all",
                }
            )
            if len(repos) >= max_repos:
                return repos
    return repos


def fetch(settings) -> ScraperResult:
    section = "gaming"
    gaming_cfg = settings.get("gaming") or {}
    data: dict[str, Any] = {"deals": [], "news": [], "github_trending": []}
    errors: list[str] = []

    try:
        data["deals"] = _fetch_cheapshark_deals(settings)
    except Exception as exc:
        logger.warning("CheapShark fetch failed: %s", exc)
        errors.append(f"CheapShark: {exc}")

    if gaming_cfg.get("include_rss", True):
        rss_result = news_rss.fetch(settings, category="gaming")
        if rss_result.status == "error":
            errors.append(rss_result.error or "gaming RSS failed")
        else:
            data["news"] = rss_result.data.get("items", [])
            if rss_result.error:
                errors.append(rss_result.error)

    try:
        data["github_trending"] = _fetch_github_trending(settings)
    except Exception as exc:
        logger.warning("GitHub trending fetch failed: %s", exc)
        errors.append(f"GitHub trending: {exc}")

    has_data = any(data[key] for key in data)
    if not has_data:
        return ScraperResult(section=section, status="error", error="; ".join(errors) or "No gaming data")

    status = "partial" if errors else "ok"
    return ScraperResult(
        section=section,
        status=status,
        data=data,
        error="; ".join(errors) if errors else None,
    )

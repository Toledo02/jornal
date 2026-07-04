"""Gaming deals, RSS headlines, and global GitHub trending."""

from __future__ import annotations

import logging
from typing import Any

from bs4 import BeautifulSoup

from core.utils import ScraperResult, http_get_json, http_get_text

logger = logging.getLogger(__name__)

CHEAPSHARK_URL = "https://www.cheapshark.com/api/1.0/deals"
STORES_URL = "https://www.cheapshark.com/api/1.0/stores"


async def _fetch_store_map(settings) -> dict[str, str]:
    try:
        stores = await http_get_json(STORES_URL, settings)
        return {
            store.get("storeID"): store.get("storeName")
            for store in stores
            if store.get("storeID") and store.get("storeName")
        }
    except Exception as exc:
        logger.warning("Failed to fetch CheapShark stores dynamically: %s", exc)
        return {
            "1": "Steam",
            "2": "GamersGate",
            "3": "GreenManGaming",
            "7": "GOG",
            "11": "Humble Store",
            "25": "Epic Games Store",
            "32": "Microsoft Store",
            "34": "Fanatical",
        }


async def _fetch_cheapshark_deals(settings) -> list[dict[str, Any]]:
    gaming_cfg = settings.get("gaming") or {}
    cheapshark_cfg = gaming_cfg.get("cheapshark") or {}
    if not cheapshark_cfg.get("enabled", True):
        return []

    store_map = await _fetch_store_map(settings)

    params = {
        "sortBy": "Savings",
        "pageSize": 60,
    }
    if "max_price" in cheapshark_cfg:
        params["upperPrice"] = cheapshark_cfg["max_price"]

    deals = await http_get_json(CHEAPSHARK_URL, settings, params=params)

    min_savings = float(cheapshark_cfg.get("min_savings_percent", 90))
    max_deals = int(cheapshark_cfg.get("max_deals", 5))

    results: list[dict[str, Any]] = []
    for deal in deals:
        try:
            sale_price = float(deal.get("salePrice") or 0.0)
            savings = float(deal.get("savings") or 0.0)
        except ValueError:
            continue

        is_free = (sale_price == 0.0)
        is_high_savings = (savings >= min_savings)

        if is_free or is_high_savings:
            store_id = deal.get("storeID")
            store_name = store_map.get(store_id, f"Loja {store_id}")
            results.append(
                {
                    "title": deal.get("title"),
                    "sale_price": deal.get("salePrice"),
                    "normal_price": deal.get("normalPrice"),
                    "savings": f"{savings:.1f}",
                    "store": store_name,
                    "url": f"https://www.cheapshark.com/redirect?dealID={deal.get('dealID')}",
                }
            )
            if len(results) >= max_deals:
                break
    return results


async def _fetch_github_trending(settings) -> list[dict[str, str]]:
    github_cfg = settings.get("github") or {}
    url = github_cfg.get("trending_url", "https://github.com/trending")
    max_repos = int(github_cfg.get("max_repos", 5))

    html = await http_get_text(url, settings)
    soup = BeautifulSoup(html, "lxml")

    repos: list[dict[str, str]] = []
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
            }
        )
        if len(repos) >= max_repos:
            break
    return repos


async def fetch(settings) -> ScraperResult:
    section = "gaming"
    data: dict[str, Any] = {"deals": [], "github_trending": []}
    errors: list[str] = []

    try:
        data["deals"] = await _fetch_cheapshark_deals(settings)
    except Exception as exc:
        logger.warning("CheapShark fetch failed: %s", exc)
        errors.append(f"CheapShark: {exc}")

    try:
        data["github_trending"] = await _fetch_github_trending(settings)
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

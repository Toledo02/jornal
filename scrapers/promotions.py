"""Product price monitoring with local history."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

from core.utils import ScraperResult, request_timeout

logger = logging.getLogger(__name__)


def _parse_price(text: str) -> float | None:
    cleaned = re.sub(r"[^\d,.]", "", text)
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _load_history(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_history(path: Path, history: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")


def _scrape_product(product: dict[str, Any], settings) -> dict[str, Any]:
    url = product["url"]
    selector = product.get("price_selector", ".price")
    response = requests.get(
        url,
        timeout=request_timeout(settings),
        headers={"User-Agent": "Mozilla/5.0 (compatible; DailyJournalBot/1.0)"},
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "lxml")
    element = soup.select_one(selector)
    if not element:
        raise ValueError(f"Price selector '{selector}' not found")

    price = _parse_price(element.get_text(strip=True))
    if price is None:
        raise ValueError("Could not parse price from page")

    return {"name": product.get("name", url), "url": url, "price": price}


def fetch(settings) -> ScraperResult:
    section = "promotions"
    promo_cfg = settings.get("promotions") or {}
    products = promo_cfg.get("products") or []

    if not products:
        return ScraperResult(
            section=section,
            status="ok",
            data={"products": [], "note": "No products configured for price monitoring"},
        )

    history_file = settings.project_root / promo_cfg.get("history_file", "promotions_history.json")
    history = _load_history(history_file)
    threshold = float(promo_cfg.get("drop_threshold_percent", 5))

    results: list[dict[str, Any]] = []
    errors: list[str] = []

    for product in products:
        try:
            current = _scrape_product(product, settings)
            key = current["url"]
            previous_price = history.get(key, {}).get("price")
            change_percent = None
            alert = False

            if previous_price and previous_price > 0:
                change_percent = round(((current["price"] - previous_price) / previous_price) * 100, 2)
                if change_percent <= -threshold:
                    alert = True

            history[key] = {"price": current["price"], "name": current["name"]}
            results.append({**current, "previous_price": previous_price, "change_percent": change_percent, "alert": alert})
        except Exception as exc:
            logger.warning("Promotion scrape failed for %s: %s", product.get("name"), exc)
            errors.append(f"{product.get('name', 'product')}: {exc}")

    _save_history(history_file, history)

    if not results:
        return ScraperResult(section=section, status="error", error="; ".join(errors))

    status = "partial" if errors else "ok"
    return ScraperResult(
        section=section,
        status=status,
        data={"products": results, "drop_threshold_percent": threshold},
        error="; ".join(errors) if errors else None,
    )

"""Product price monitoring via multi-source search."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from core.utils import ScraperResult, http_get_text

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


def _slugify(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    return re.sub(r"[\s_]+", "-", slug)


async def _search_mercado_livre(product_name: str, settings) -> dict[str, Any] | None:
    url = f"https://lista.mercadolivre.com.br/{quote_plus(product_name)}"
    html = await http_get_text(url, settings)
    soup = BeautifulSoup(html, "lxml")

    item = soup.select_one("li.ui-search-layout__item, div.ui-search-result")
    if not item:
        return None

    link_el = item.select_one("a.ui-search-link, a.poly-component__title")
    price_el = item.select_one(
        "span.andes-money-amount__fraction, "
        "span.price-tag-fraction, "
        ".poly-price__current .andes-money-amount__fraction"
    )
    if not price_el:
        return None

    price = _parse_price(price_el.get_text(strip=True))
    if price is None:
        return None

    href = link_el.get("href", url) if link_el else url
    return {"price": price, "url": href, "source": "mercado_livre"}


async def _search_buscape(product_name: str, settings) -> dict[str, Any] | None:
    url = f"https://www.buscape.com.br/search?q={quote_plus(product_name)}"
    html = await http_get_text(url, settings)
    soup = BeautifulSoup(html, "lxml")

    card = soup.select_one("[data-testid='product-card'], div[class*='ProductCard'], article")
    if not card:
        return None

    link_el = card.select_one("a[href]")
    price_el = card.select_one(
        "[data-testid='price'], span[class*='Price'], "
        "span[class*='price'], div[class*='Price']"
    )
    if not price_el:
        return None

    price = _parse_price(price_el.get_text(strip=True))
    if price is None:
        return None

    href = link_el.get("href", url) if link_el else url
    if href.startswith("/"):
        href = f"https://www.buscape.com.br{href}"

    return {"price": price, "url": href, "source": "buscape"}


async def _find_best_price(product_name: str, settings) -> dict[str, Any]:
    offers: list[dict[str, Any]] = []
    for search_fn in (_search_mercado_livre, _search_buscape):
        try:
            offer = await search_fn(product_name, settings)
            if offer:
                offers.append(offer)
        except Exception as exc:
            logger.warning("Price search failed (%s) for %s: %s", search_fn.__name__, product_name, exc)

    if not offers:
        raise ValueError(f"No offers found for '{product_name}'")

    best = min(offers, key=lambda o: o["price"])
    return {
        "name": product_name,
        "price": best["price"],
        "url": best["url"],
        "source": best["source"],
        "all_offers": offers,
    }


def _resolve_product_names(promo_cfg: dict[str, Any]) -> list[str]:
    names = promo_cfg.get("product_names") or []
    if names:
        return [str(name).strip() for name in names if str(name).strip()]

    legacy = promo_cfg.get("products") or []
    resolved: list[str] = []
    for product in legacy:
        if isinstance(product, str):
            resolved.append(product.strip())
        elif isinstance(product, dict):
            name = product.get("name") or product.get("url")
            if name:
                resolved.append(str(name).strip())
    return resolved


async def fetch(settings) -> ScraperResult:
    section = "promotions"
    promo_cfg = settings.get("promotions") or {}
    product_names = _resolve_product_names(promo_cfg)

    if not product_names:
        return ScraperResult(
            section=section,
            status="ok",
            data={"products": [], "note": "No product_names configured for price monitoring"},
        )

    history_file = settings.project_root / promo_cfg.get("history_file", "promotions_history.json")
    history = _load_history(history_file)
    threshold = float(promo_cfg.get("drop_threshold_percent", 5))

    results: list[dict[str, Any]] = []
    errors: list[str] = []

    for product_name in product_names:
        try:
            current = await _find_best_price(product_name, settings)
            key = _slugify(product_name)
            previous_price = history.get(key, {}).get("price")
            change_percent = None
            alert = False

            if previous_price and previous_price > 0:
                change_percent = round(((current["price"] - previous_price) / previous_price) * 100, 2)
                if change_percent <= -threshold:
                    alert = True

            history[key] = {"price": current["price"], "name": product_name}
            results.append(
                {
                    **current,
                    "previous_price": previous_price,
                    "change_percent": change_percent,
                    "alert": alert,
                }
            )
        except Exception as exc:
            logger.warning("Promotion search failed for %s: %s", product_name, exc)
            errors.append(f"{product_name}: {exc}")

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

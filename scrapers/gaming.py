"""Game deals from CheapShark."""

from __future__ import annotations

import logging
from typing import Any

from core.utils import ScraperResult, http_get_json

logger = logging.getLogger(__name__)

CHEAPSHARK_URL = "https://www.cheapshark.com/api/1.0/deals"
STORES_URL = "https://www.cheapshark.com/api/1.0/stores"

FALLBACK_STORES = {
    "1": "Steam",
    "2": "GamersGate",
    "3": "GreenManGaming",
    "7": "GOG",
    "11": "Humble Store",
    "25": "Epic Games Store",
    "32": "Microsoft Store",
    "34": "Fanatical",
}


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
        return FALLBACK_STORES


async def _fetch_deals(settings) -> list[dict[str, Any]]:
    gaming_cfg = settings.get("gaming") or {}
    cheapshark_cfg = gaming_cfg.get("cheapshark") or {}
    if not cheapshark_cfg.get("enabled", True):
        return []

    store_map = await _fetch_store_map(settings)

    params: dict[str, Any] = {"sortBy": "Savings", "pageSize": 60}
    # Ordenar por desconto máximo seleciona shovelware: os 96% off costumam ser jogos que
    # ninguém compra. A nota da Steam é o filtro que separa oferta de entulho.
    min_rating = cheapshark_cfg.get("min_steam_rating")
    if min_rating is not None:
        params["steamRating"] = min_rating
    if "max_price" in cheapshark_cfg:
        params["upperPrice"] = cheapshark_cfg["max_price"]

    deals = await http_get_json(CHEAPSHARK_URL, settings, params=params)

    min_savings = float(cheapshark_cfg.get("min_savings_percent", 90))
    max_deals = int(cheapshark_cfg.get("max_deals", 5))

    results: list[dict[str, Any]] = []
    seen_titles: set[str] = set()

    for deal in deals:
        # O mesmo jogo aparece uma vez por loja; como a lista vem ordenada por desconto,
        # a primeira ocorrência já é a melhor oferta.
        title_key = (deal.get("title") or "").strip().lower()
        if not title_key or title_key in seen_titles:
            continue

        try:
            sale_price = float(deal.get("salePrice") or 0.0)
            normal_price = float(deal.get("normalPrice") or 0.0)
            savings = float(deal.get("savings") or 0.0)
            rating = int(deal.get("steamRatingPercent") or 0)
        except (TypeError, ValueError):
            continue

        if sale_price > 0.0 and savings < min_savings:
            continue

        seen_titles.add(title_key)
        results.append(
            {
                "title": deal.get("title"),
                "is_free": sale_price == 0.0,
                "sale_price_usd": f"{sale_price:.2f}",
                "normal_price_usd": f"{normal_price:.2f}",
                "savings_percent": f"{savings:.0f}",
                "steam_rating_percent": rating or None,
                "store": store_map.get(deal.get("storeID"), f"Loja {deal.get('storeID')}"),
                "url": f"https://www.cheapshark.com/redirect?dealID={deal.get('dealID')}",
            }
        )
        if len(results) >= max_deals:
            break

    return results


async def fetch(settings) -> ScraperResult:
    section = "gaming"
    try:
        deals = await _fetch_deals(settings)
    except Exception as exc:
        logger.warning("CheapShark fetch failed: %s", exc)
        return ScraperResult(section=section, status="error", error=f"CheapShark: {exc}")

    # Zero ofertas qualificadas é um resultado legítimo, não uma falha: alertar sobre isso
    # todo dia treinaria você a ignorar os alertas.
    return ScraperResult(
        section=section,
        status="ok",
        data={"deals": deals, "count": len(deals)},
    )

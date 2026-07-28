"""Free game giveaways (GamerPower) and paid deals (CheapShark)."""

from __future__ import annotations

import logging
import re
from typing import Any

from core.utils import ScraperResult, http_get_json

logger = logging.getLogger(__name__)

CHEAPSHARK_URL = "https://www.cheapshark.com/api/1.0/deals"
STORES_URL = "https://www.cheapshark.com/api/1.0/stores"
GAMERPOWER_URL = "https://www.gamerpower.com/api/giveaways"

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


def _worth_usd(text: str | None) -> float:
    """'$19.99' -> 19.99. Serve para ordenar os giveaways pelo que vale mais a pena."""
    match = re.search(r"[\d.]+", (text or "").replace(",", ""))
    return float(match.group()) if match else 0.0


async def _fetch_free_games(settings) -> list[dict[str, Any]]:
    """Giveaways ativos de jogos.

    O CheapShark ordenado por desconto quase nunca traz algo de graça — a seção prometia
    "Games Grátis" e entregava sete títulos obscuros a US$ 0,51, os mesmos por semanas.
    """
    gaming_cfg = settings.get("gaming") or {}
    gp_cfg = gaming_cfg.get("gamerpower") or {}
    if not gp_cfg.get("enabled", True):
        return []

    giveaways = await http_get_json(
        GAMERPOWER_URL, settings, params={"type": gp_cfg.get("type", "game")}
    )

    platform_filter = [p.lower() for p in (gp_cfg.get("platforms") or [])]
    max_items = int(gp_cfg.get("max_items", 5))

    selected: list[dict[str, Any]] = []
    for giveaway in giveaways:
        if str(giveaway.get("status", "")).lower() != "active":
            continue

        platforms = str(giveaway.get("platforms", ""))
        if platform_filter and not any(p in platforms.lower() for p in platform_filter):
            continue

        selected.append(
            {
                "title": giveaway.get("title", "").replace(" Giveaway", "").strip(),
                "worth": giveaway.get("worth"),
                "worth_value": _worth_usd(giveaway.get("worth")),
                "platforms": platforms,
                "ends_at": giveaway.get("end_date") if giveaway.get("end_date") != "N/A" else None,
                "url": giveaway.get("open_giveaway_url") or giveaway.get("gamerpower_url"),
            }
        )

    # Os mais valiosos primeiro: um jogo de US$ 20 de graça interessa mais que um de US$ 2.
    selected.sort(key=lambda item: item["worth_value"], reverse=True)
    return selected[:max_items]


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
    data: dict[str, Any] = {"free_games": [], "deals": []}
    errors: list[str] = []

    try:
        data["free_games"] = await _fetch_free_games(settings)
    except Exception as exc:
        logger.warning("GamerPower fetch failed: %s", exc)
        errors.append(f"GamerPower: {exc}")

    try:
        data["deals"] = await _fetch_deals(settings)
    except Exception as exc:
        logger.warning("CheapShark fetch failed: %s", exc)
        errors.append(f"CheapShark: {exc}")

    logger.info(
        "gaming: %s jogos grátis, %s ofertas", len(data["free_games"]), len(data["deals"])
    )

    if errors and not data["free_games"] and not data["deals"]:
        return ScraperResult(section=section, status="error", error="; ".join(errors))

    # Zero ofertas qualificadas é um resultado legítimo, não uma falha: alertar sobre isso
    # todo dia treinaria você a ignorar os alertas.
    return ScraperResult(
        section=section,
        status="partial" if errors else "ok",
        data=data,
        error="; ".join(errors) if errors else None,
    )

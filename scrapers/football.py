"""Football intelligence via GE Globo Esporte scraping."""

from __future__ import annotations

import logging
from typing import Any

from bs4 import BeautifulSoup

from core.utils import ScraperResult, http_get_text

logger = logging.getLogger(__name__)


def _extract_text_blocks(soup: BeautifulSoup, selectors: list[str]) -> list[str]:
    blocks: list[str] = []
    for selector in selectors:
        for element in soup.select(selector):
            text = element.get_text(" ", strip=True)
            if text and len(text) > 5:
                blocks.append(text)
    return blocks


def _parse_team_page(html: str, team_name: str, selectors: dict[str, str]) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    next_selectors = [s.strip() for s in selectors.get("next_match", "").split(",") if s.strip()]
    last_selectors = [s.strip() for s in selectors.get("last_match", "").split(",") if s.strip()]
    item_selectors = [s.strip() for s in selectors.get("match_items", "li").split(",") if s.strip()]

    next_blocks = _extract_text_blocks(soup, next_selectors)
    last_blocks = _extract_text_blocks(soup, last_selectors)

    if not next_blocks and not last_blocks:
        fallback = _extract_text_blocks(soup, item_selectors)
        next_blocks = fallback[:2]
        last_blocks = fallback[2:4]

    return {
        "team": team_name,
        "next_match": next_blocks[0] if next_blocks else None,
        "last_match": last_blocks[0] if last_blocks else None,
        "raw_snippets": (next_blocks + last_blocks)[:4],
    }


async def _scrape_team(team_name: str, settings, ge_cfg: dict[str, Any]) -> dict[str, Any]:
    base_url = ge_cfg.get("base_url", "https://ge.globo.com").rstrip("/")
    slugs = ge_cfg.get("team_slugs") or {}
    selectors = ge_cfg.get("selectors") or {}
    slug = slugs.get(team_name)

    if not slug:
        raise ValueError(f"No GE slug configured for team '{team_name}'")

    url = f"{base_url}/{slug.strip('/')}/"
    html = await http_get_text(url, settings)
    return _parse_team_page(html, team_name, selectors)


async def fetch(settings) -> ScraperResult:
    section = "football"
    football_cfg = settings.get("football") or {}
    teams = football_cfg.get("teams") or []
    ge_cfg = (football_cfg.get("sources") or {}).get("ge") or {}

    if not teams:
        return ScraperResult(section=section, status="error", error="No football teams configured")

    team_data: list[dict[str, Any]] = []
    errors: list[str] = []

    for team in teams:
        try:
            team_data.append(await _scrape_team(team, settings, ge_cfg))
        except Exception as exc:
            logger.warning("Football scrape failed for %s: %s", team, exc)
            errors.append(f"{team}: {exc}")

    if not team_data:
        return ScraperResult(section=section, status="error", error="; ".join(errors))

    status = "partial" if errors else "ok"
    return ScraperResult(
        section=section,
        status=status,
        data={"teams": team_data},
        error="; ".join(errors) if errors else None,
    )

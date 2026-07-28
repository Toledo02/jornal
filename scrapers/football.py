"""Football intelligence via GE Globo Esporte scraping.

Limitação conhecida: o GE não expõe agenda estruturada nessas páginas, então o que sai daqui
são manchetes recentes do time, não jogos com data. O prompt é instruído a não inferir datas
relativas a partir desse texto. A solução real é trocar por uma API — ver PLANO.md, item 6.1.
"""

from __future__ import annotations

import logging
from typing import Any

from bs4 import BeautifulSoup

from core.utils import BROWSER_HEADERS, ScraperResult, http_get_text

logger = logging.getLogger(__name__)


def _select_texts(
    soup: BeautifulSoup, selector: str, limit: int, noise: list[str] | None = None
) -> list[str]:
    if not selector:
        return []

    noise = noise or []
    texts: list[str] = []
    seen: set[str] = set()

    for element in soup.select(selector):
        text = " ".join(element.get_text(" ", strip=True).split())
        # Blocos gigantes são sinal de seletor casando com um container, não com um item.
        if not text or len(text) < 15 or len(text) > 300 or text in seen:
            continue
        if any(pattern in text.lower() for pattern in noise):
            continue
        seen.add(text)
        texts.append(text)
        if len(texts) >= limit:
            break
    return texts


def _parse_team_page(
    html: str, team_name: str, selectors: dict[str, Any], noise: list[str]
) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    max_headlines = int(selectors.get("max_headlines", 6))

    return {
        "team": team_name,
        "next_match": next(iter(_select_texts(soup, selectors.get("next_match", ""), 1)), None),
        "last_match": next(iter(_select_texts(soup, selectors.get("last_match", ""), 1)), None),
        "headlines": _select_texts(soup, selectors.get("headlines", ""), max_headlines, noise),
    }


async def _scrape_team(team_name: str, settings, ge_cfg: dict[str, Any]) -> dict[str, Any]:
    base_url = ge_cfg.get("base_url", "https://ge.globo.com").rstrip("/")
    slug = (ge_cfg.get("team_slugs") or {}).get(team_name)

    if not slug:
        raise ValueError(f"No GE slug configured for team '{team_name}'")

    url = f"{base_url}/{slug.strip('/')}/"
    html = await http_get_text(url, settings, headers=BROWSER_HEADERS)
    noise = [str(p).lower() for p in (ge_cfg.get("noise_patterns") or [])]
    return _parse_team_page(html, team_name, ge_cfg.get("selectors") or {}, noise)


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
            data = await _scrape_team(team, settings, ge_cfg)
            if not data["headlines"] and not data["next_match"] and not data["last_match"]:
                errors.append(f"{team}: nenhum conteúdo extraído (seletores desatualizados?)")
                continue
            team_data.append(data)
        except Exception as exc:
            logger.warning("Football scrape failed for %s: %s", team, exc)
            errors.append(f"{team}: {exc}")

    if not team_data:
        return ScraperResult(section=section, status="error", error="; ".join(errors))

    return ScraperResult(
        section=section,
        status="partial" if errors else "ok",
        data={
            "teams": team_data,
            "_nota": (
                "Manchetes recentes sem data explícita. Não inferir 'ontem', 'hoje' ou "
                "'amanhã' a partir delas."
            ),
        },
        error="; ".join(errors) if errors else None,
    )

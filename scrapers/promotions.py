"""Promoções: canais públicos do Telegram + monitoramento opcional de produtos.

Os canais são a fonte principal. A vitrine de um comparador mostra o preço corrente de um
produto que você escolheu; os canais mostram o que está barato *hoje*, com curadoria humana —
que é onde as promoções de verdade aparecem.

A leitura usa `https://t.me/s/<canal>`, a prévia web que o Telegram publica para canais
públicos: HTML simples, sem bot, sem token e sem entrar no canal.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from itertools import zip_longest
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlparse

from bs4 import BeautifulSoup

from core.utils import BROWSER_HEADERS, ScraperResult, http_get_text

logger = logging.getLogger(__name__)

TELEGRAM_PREVIEW_URL = "https://t.me/s/{channel}"


# --------------------------------------------------------------------------- canais


def _message_datetime(node: Any) -> datetime | None:
    time_el = node.select_one("time[datetime]")
    if not time_el:
        return None
    try:
        return datetime.fromisoformat(time_el["datetime"].replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return None


def _external_link(text_el: Any) -> str | None:
    """Primeiro link que sai do Telegram — normalmente o da oferta."""
    for anchor in text_el.select("a[href]"):
        href = anchor.get("href", "")
        host = urlparse(href).netloc.lower()
        if href.startswith("http") and not host.endswith("t.me") and "telegram" not in host:
            return href
    return None


def _parse_channel(html: str, channel: str, cutoff: datetime, noise: list[str]) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    messages: list[dict[str, Any]] = []

    for node in soup.select("div.tgme_widget_message"):
        text_el = node.select_one(".tgme_widget_message_text")
        if not text_el:
            continue

        published = _message_datetime(node)
        if published and published < cutoff:
            continue

        text = " ".join(text_el.get_text(" ", strip=True).split())
        if len(text) < 25 or any(pattern in text.lower() for pattern in noise):
            continue

        post = node.get("data-post")
        messages.append(
            {
                "channel": channel,
                "text": text[:400],
                "url": _external_link(text_el),
                "permalink": f"https://t.me/{post}" if post else None,
                "published": published.isoformat() if published else None,
            }
        )

    # Mais recentes primeiro: a prévia vem em ordem cronológica.
    messages.reverse()
    return messages


async def _fetch_channels(settings, promo_cfg: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    channels = [str(c).strip().lstrip("@") for c in (promo_cfg.get("telegram_channels") or [])]
    if not channels:
        return [], []

    max_age_hours = int(promo_cfg.get("max_age_hours", 24))
    per_channel = int(promo_cfg.get("per_channel", 5))
    total = int(promo_cfg.get("max_items", 10))
    noise = [str(p).lower() for p in (promo_cfg.get("noise_patterns") or [])]
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)

    per_channel_items: list[list[dict[str, Any]]] = []
    errors: list[str] = []

    for channel in channels:
        try:
            html = await http_get_text(
                TELEGRAM_PREVIEW_URL.format(channel=channel), settings, headers=BROWSER_HEADERS
            )
            found = _parse_channel(html, channel, cutoff, noise)[:per_channel]
            if found:
                per_channel_items.append(found)
            else:
                logger.info("Canal %s sem ofertas nas últimas %sh", channel, max_age_hours)
        except Exception as exc:
            logger.warning("Falha ao ler o canal %s: %s", channel, exc)
            errors.append(f"@{channel}: {exc}")

    # Round-robin entre canais, mesma razão dos feeds RSS: concatenar e truncar faria o
    # primeiro canal ocupar todos os slots.
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in zip_longest(*per_channel_items):
        for item in row:
            if item is None:
                continue
            key = re.sub(r"[^\w]", "", item["text"].lower())[:60]
            if key in seen:
                continue
            seen.add(key)
            items.append(item)
            if len(items) >= total:
                return items, errors
    return items, errors


# --------------------------------------------------------------------------- produtos


def _parse_price(text: str) -> float | None:
    cleaned = re.sub(r"[^\d,.]", "", text)
    if not cleaned:
        return None

    if "," in cleaned and "." in cleaned:
        # O separador que aparece por último é o decimal — vale para "1.234,56" e "1,234.56".
        decimal_sep = "," if cleaned.rfind(",") > cleaned.rfind(".") else "."
        thousands_sep = "." if decimal_sep == "," else ","
        cleaned = cleaned.replace(thousands_sep, "").replace(decimal_sep, ".")
    else:
        for separator in (",", "."):
            if separator not in cleaned:
                continue
            head, _, tail = cleaned.rpartition(separator)
            # "1.299" é mil duzentos e noventa e nove, não 1,299. Preço de varejo tem duas casas
            # decimais, então três dígitos depois do separador significam milhar. Sem isso um
            # produto de R$ 1.299 virava R$ 1,30 e disparava alerta de queda de 99%.
            cleaned = cleaned.replace(separator, "" if head and len(tail) == 3 else ".")
            break

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


async def _search_buscape(product_name: str, settings) -> dict[str, Any] | None:
    """Buscapé é a única vitrine que ainda responde.

    O Mercado Livre redireciona para um muro de verificação de conta e a API pública dele
    exige app OAuth registrado; o Zoom é do mesmo grupo do Buscapé e devolve o mesmo catálogo.
    """
    url = f"https://www.buscape.com.br/search?q={quote_plus(product_name)}"
    html = await http_get_text(url, settings, headers=BROWSER_HEADERS)
    soup = BeautifulSoup(html, "lxml")

    card = soup.select_one("[data-testid='product-card'], div[class*='ProductCard'], article")
    if not card:
        return None

    price_el = card.select_one(
        "[data-testid='price'], span[class*='Price'], span[class*='price'], div[class*='Price']"
    )
    if not price_el:
        return None

    price = _parse_price(price_el.get_text(strip=True))
    if price is None:
        return None

    link_el = card.select_one("a[href]")
    href = link_el.get("href", url) if link_el else url
    if href.startswith("/"):
        href = f"https://www.buscape.com.br{href}"

    return {"price": price, "url": href, "source": "buscape"}


async def _track_products(settings, promo_cfg: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    names = [str(n).strip() for n in (promo_cfg.get("product_names") or []) if str(n).strip()]
    if not names:
        return [], []

    history_file = settings.project_root / promo_cfg.get("history_file", "promotions_history.json")
    history = _load_history(history_file)
    threshold = float(promo_cfg.get("drop_threshold_percent", 5))

    results: list[dict[str, Any]] = []
    errors: list[str] = []

    for name in names:
        try:
            current = await _search_buscape(name, settings)
            if not current:
                errors.append(f"{name}: não encontrado")
                continue

            key = _slugify(name)
            previous_price = history.get(key, {}).get("price")
            change_percent = None
            alert = False

            if previous_price and previous_price > 0:
                change_percent = round((current["price"] - previous_price) / previous_price * 100, 2)
                alert = change_percent <= -threshold

            history[key] = {"price": current["price"], "name": name}
            results.append(
                {
                    "name": name,
                    **current,
                    "previous_price": previous_price,
                    "change_percent": change_percent,
                    "alert": alert,
                }
            )
        except Exception as exc:
            logger.warning("Busca de preço falhou para %s: %s", name, exc)
            errors.append(f"{name}: {exc}")

    _save_history(history_file, history)
    return results, errors


# --------------------------------------------------------------------------- entrada


async def fetch(settings) -> ScraperResult:
    section = "promotions"
    promo_cfg = settings.get("promotions") or {}

    offers, channel_errors = await _fetch_channels(settings, promo_cfg)
    products, product_errors = await _track_products(settings, promo_cfg)

    logger.info("promotions: %s ofertas de canais, %s produtos monitorados", len(offers), len(products))

    errors = channel_errors + product_errors
    data = {"offers": offers, "products": products}

    # Sem nada configurado não é falha: devolve vazio para o prompt omitir a seção.
    if not offers and not products and errors:
        return ScraperResult(section=section, status="error", error="; ".join(errors))

    return ScraperResult(
        section=section,
        status="partial" if errors else "ok",
        data=data,
        error="; ".join(errors) if errors else None,
    )

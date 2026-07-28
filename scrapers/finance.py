"""Finance data: multi-asset quotes filled asset-by-asset across several sources."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from bs4 import BeautifulSoup

from core.utils import (
    BROWSER_HEADERS,
    ScraperResult,
    format_number_pt_br,
    http_get_json,
    http_get_text,
)

logger = logging.getLogger(__name__)

AWESOMEAPI_URL = "https://economia.awesomeapi.com.br/json/last/{pairs}"
HG_BRASIL_URL = "https://api.hgbrasil.com/finance"

ASSET_PAIRS = [
    ("USD-BRL", "usd_brl", "USD"),
    ("EUR-BRL", "eur_brl", "EUR"),
    ("ARS-BRL", "ars_brl", "ARS"),
    ("BTC-BRL", "btc_brl", "BTC"),
]

ASSET_KEYS = ["usd_brl", "eur_brl", "ars_brl", "btc_brl", "ibovespa"]

YFINANCE_TICKERS = {
    "usd_brl": "USDBRL=X",
    "eur_brl": "EURBRL=X",
    "ars_brl": "ARSBRL=X",
    "ibovespa": "^BVSP",
    # BTC-BRL foi descontinuado no Yahoo ("possibly delisted"); é derivado em _derive_btc_brl.
}


def _has_value(quote: dict[str, Any] | None) -> bool:
    if not quote:
        return False
    return quote.get("bid") is not None or quote.get("points") is not None


# --------------------------------------------------------------------------- fontes


def _quote_from_awesome(body: dict[str, Any], pair: str) -> dict[str, Any]:
    key = pair.replace("-", "")
    quote = body.get(key) or {}
    return {
        "pair": pair,
        "bid": quote.get("bid"),
        "ask": quote.get("ask"),
        "timestamp": quote.get("create_date") or quote.get("timestamp"),
        "source": "awesomeapi",
    }


async def _fetch_awesomeapi(settings, missing: list[str]) -> dict[str, Any]:
    wanted = [(pair, key) for pair, key, _ in ASSET_PAIRS if key in missing]
    if not wanted:
        return {}

    url = AWESOMEAPI_URL.format(pairs=",".join(pair for pair, _ in wanted))
    headers = {}
    if settings.awesomeapi_token:
        headers["x-api-key"] = settings.awesomeapi_token

    body = await http_get_json(url, settings, headers=headers)
    return {key: _quote_from_awesome(body, pair) for pair, key in wanted}


async def _fetch_hg_brasil(settings, missing: list[str]) -> dict[str, Any]:
    body = await http_get_json(HG_BRASIL_URL, settings, params={"key": "free"})
    results = body.get("results") or {}
    currencies = results.get("currencies") or {}
    bitcoin = results.get("bitcoin") or {}
    ibov = (results.get("stocks") or {}).get("IBOVESPA") or {}

    quotes: dict[str, Any] = {}
    for _, key, currency_code in ASSET_PAIRS:
        if currency_code == "BTC":
            # A chave "free" costuma devolver o bloco bitcoin vazio; nesse caso o ativo fica
            # faltando e a próxima fonte da cadeia preenche.
            source = next(iter(bitcoin.values()), {}) if isinstance(bitcoin, dict) else {}
            quotes[key] = {
                "pair": "BTC-BRL",
                "bid": source.get("buy") if isinstance(source, dict) else None,
                "ask": source.get("sell") if isinstance(source, dict) else None,
                "source": "hgbrasil",
            }
        else:
            currency = currencies.get(currency_code) or {}
            quotes[key] = {
                "pair": f"{currency_code}-BRL",
                "bid": currency.get("buy"),
                "ask": currency.get("sell"),
                "source": "hgbrasil",
            }

    quotes["ibovespa"] = {
        "symbol": "IBOVESPA",
        "points": ibov.get("points"),
        "variation_percent": ibov.get("variation"),
        "source": "hgbrasil",
    }
    return quotes


def _fetch_yfinance_sync(keys: list[str]) -> dict[str, Any]:
    import yfinance as yf

    pair_labels = {key: pair for pair, key, _ in ASSET_PAIRS}
    quotes: dict[str, Any] = {}

    for key in keys:
        ticker = YFINANCE_TICKERS.get(key)
        if not ticker:
            continue
        history = yf.Ticker(ticker).history(period="5d")
        if history.empty:
            continue

        close = float(history.iloc[-1]["Close"])
        previous = float(history.iloc[-2]["Close"]) if len(history) >= 2 else None

        if key == "ibovespa":
            quotes[key] = {
                "symbol": "IBOVESPA",
                "points": round(close, 2),
                "previous_close": round(previous, 2) if previous else None,
                "source": "yfinance",
            }
        else:
            quotes[key] = {
                "pair": pair_labels.get(key, ticker),
                "bid": round(close, 4),
                "ask": round(close, 4),
                "previous_close": round(previous, 4) if previous else None,
                "source": "yfinance",
            }
    return quotes


async def _fetch_yfinance(settings, missing: list[str]) -> dict[str, Any]:
    # Só busca o que falta: cada ticker é uma chamada de rede síncrona.
    return await asyncio.to_thread(_fetch_yfinance_sync, missing)


def _yfinance_close_sync(ticker: str) -> float | None:
    import yfinance as yf

    history = yf.Ticker(ticker).history(period="5d")
    return None if history.empty else float(history.iloc[-1]["Close"])


async def _derive_btc_brl(quotes: dict[str, Any]) -> None:
    """Deriva BTC-BRL de BTC-USD x USD-BRL.

    O par direto saiu do Yahoo e a AwesomeAPI — única outra fonte de BTC — responde 429 na VPS,
    então sem esta derivação o Bitcoin fica permanentemente ausente em produção.
    """
    usd = quotes.get("usd_brl") or {}
    if not usd.get("bid"):
        return

    try:
        btc_usd = await asyncio.to_thread(_yfinance_close_sync, "BTC-USD")
    except Exception as exc:
        logger.warning("BTC-USD fetch failed: %s", exc)
        return

    if btc_usd is None:
        return

    quotes["btc_brl"] = {
        "pair": "BTC-BRL",
        "bid": round(btc_usd * float(usd["bid"]), 2),
        "source": "derivado de BTC-USD x USD-BRL",
    }
    logger.info("BTC-BRL derivado de BTC-USD x USD-BRL")


# Ordem por confiabilidade observada em produção, não por preferência:
# a AwesomeAPI responde 429 na VPS desde 11/06/2026 e ficou por último.
SOURCES: list[tuple[str, Callable[..., Awaitable[dict[str, Any]]]]] = [
    ("HG Brasil", _fetch_hg_brasil),
    ("yfinance", _fetch_yfinance),
    ("AwesomeAPI", _fetch_awesomeapi),
]


async def _fetch_quotes(settings) -> tuple[dict[str, Any], list[str]]:
    """Preenche ativo a ativo, e não tudo-ou-nada.

    Antes, bastava o USD vir preenchido para a função retornar — se a fonte não trouxesse o BTC,
    ninguém consultava as demais e o jornal saía com "Bitcoin: cotação indisponível".
    """
    quotes: dict[str, Any] = {}
    errors: list[str] = []

    for label, fetcher in SOURCES:
        missing = [key for key in ASSET_KEYS if not _has_value(quotes.get(key))]
        if not missing:
            break

        try:
            fresh = await fetcher(settings, missing)
        except Exception as exc:
            logger.warning("%s fetch failed: %s", label, exc)
            errors.append(f"{label}: {exc}")
            continue

        filled = [key for key in missing if _has_value(fresh.get(key))]
        for key in filled:
            quotes[key] = fresh[key]
        if filled:
            logger.info("%s forneceu: %s", label, ", ".join(filled))

    if not _has_value(quotes.get("btc_brl")):
        await _derive_btc_brl(quotes)

    still_missing = [key for key in ASSET_KEYS if not _has_value(quotes.get(key))]
    if still_missing:
        errors.append(f"sem cotação para: {', '.join(still_missing)}")

    return quotes, errors


# --------------------------------------------------------------------------- formatação


def _decorate(quotes: dict[str, Any]) -> None:
    """Acrescenta o texto pronto para exibição a cada ativo.

    Formatação numérica e cálculo de variação são feitos aqui, em Python: entregar float cru ao
    modelo produzia "R$ 0.0034278" e percentuais inventados por aritmética dele.
    """
    for key in ("usd_brl", "eur_brl"):
        quote = quotes.get(key) or {}
        if quote.get("bid") is not None:
            quote["display"] = f"R$ {format_number_pt_br(float(quote['bid']), 2)}"

    ars = quotes.get("ars_brl") or {}
    if ars.get("bid"):
        try:
            # 0,0034 BRL por peso não diz nada a ninguém; o inverso é legível.
            ars["display"] = f"1 BRL = {format_number_pt_br(1 / float(ars['bid']), 2)} ARS"
        except (ValueError, ZeroDivisionError):
            pass

    btc = quotes.get("btc_brl") or {}
    if btc.get("bid") is not None:
        btc["display"] = f"R$ {format_number_pt_br(float(btc['bid']), 0)}"

    ibov = quotes.get("ibovespa") or {}
    points = ibov.get("points")
    if points is not None:
        variation = ibov.get("variation_percent")
        if variation is None and ibov.get("previous_close"):
            try:
                previous = float(ibov["previous_close"])
                variation = round((float(points) - previous) / previous * 100, 2)
                ibov["variation_percent"] = variation
            except (ValueError, ZeroDivisionError):
                variation = None

        display = f"{format_number_pt_br(float(points), 0)} pts"
        if variation is not None:
            display += f" ({format_number_pt_br(float(variation), 2)}%)"
        ibov["display"] = display


# --------------------------------------------------------------------------- manchetes


async def _scrape_target(target: dict[str, Any], settings) -> list[dict[str, str]]:
    url = target.get("url")
    if not url:
        return []

    if target.get("use_playwright"):
        logger.warning("Playwright requested for %s but is not installed; skipping", target.get("name"))
        return []

    html = await http_get_text(url, settings, headers=BROWSER_HEADERS)
    soup = BeautifulSoup(html, "lxml")
    selector = (target.get("selectors") or {}).get("headlines", "h2 a, h3 a")
    max_items = int((target.get("selectors") or {}).get("max_items", 5))
    min_length = int((target.get("selectors") or {}).get("min_title_length", 25))

    headlines: list[dict[str, str]] = []
    seen: set[str] = set()
    for element in soup.select(selector):
        title = element.get_text(strip=True)
        # Seletores genéricos como "h2 a" pegam menu e rodapé junto com as manchetes.
        if not title or len(title) < min_length or title in seen:
            continue
        seen.add(title)
        headlines.append({"title": title, "url": element.get("href", "")})
        if len(headlines) >= max_items:
            break
    return headlines


async def fetch(settings) -> ScraperResult:
    section = "finance"
    finance_cfg = settings.get("finance") or {}
    data: dict[str, Any] = {"headlines": []}
    errors: list[str] = []

    quotes, quote_errors = await _fetch_quotes(settings)
    _decorate(quotes)
    errors.extend(quote_errors)
    data.update(quotes)

    for target in finance_cfg.get("scrape_targets", []):
        try:
            headlines = await _scrape_target(target, settings)
            data["headlines"].extend(
                {"source": target.get("name", "unknown"), **item} for item in headlines
            )
        except Exception as exc:
            logger.warning("Finance scrape failed for %s: %s", target.get("name"), exc)
            errors.append(f"{target.get('name', 'target')}: {exc}")

    has_quotes = any(_has_value(data.get(key)) for key in ASSET_KEYS)
    if not has_quotes and not data["headlines"]:
        return ScraperResult(section=section, status="error", error="; ".join(errors) or "No finance data")

    return ScraperResult(
        section=section,
        status="partial" if errors else "ok",
        data=data,
        error="; ".join(errors) if errors else None,
    )

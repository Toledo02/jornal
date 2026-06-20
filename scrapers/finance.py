"""Finance data: multi-asset quotes with AwesomeAPI fallback to HG Brasil / yfinance."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from bs4 import BeautifulSoup

from core.utils import ScraperResult, http_get_json, http_get_text

logger = logging.getLogger(__name__)

AWESOMEAPI_URL = "https://economia.awesomeapi.com.br/json/last/{pairs}"
HG_BRASIL_URL = "https://api.hgbrasil.com/finance"

ASSET_PAIRS = [
    ("USD-BRL", "usd_brl", "USD"),
    ("EUR-BRL", "eur_brl", "EUR"),
    ("ARS-BRL", "ars_brl", "ARS"),
    ("BTC-BRL", "btc_brl", "BTC"),
]

YFINANCE_TICKERS = {
    "usd_brl": "USDBRL=X",
    "eur_brl": "EURBRL=X",
    "ars_brl": "ARSBRL=X",
    "btc_brl": "BTC-BRL",
    "ibovespa": "^BVSP",
}


def _quote_from_awesome(body: dict[str, Any], pair: str) -> dict[str, Any]:
    key = pair.replace("-", "")
    quote = body.get(key) or next(iter(body.values()), {})
    return {
        "pair": pair,
        "bid": quote.get("bid"),
        "ask": quote.get("ask"),
        "timestamp": quote.get("create_date") or quote.get("timestamp"),
        "source": "awesomeapi",
    }


async def _fetch_awesomeapi(settings) -> dict[str, Any]:
    pairs = ",".join(pair for pair, _, _ in ASSET_PAIRS)
    url = AWESOMEAPI_URL.format(pairs=pairs)
    headers = {}
    if settings.awesomeapi_token:
        headers["x-api-key"] = settings.awesomeapi_token

    body = await http_get_json(url, settings, headers=headers)
    quotes: dict[str, Any] = {}
    for pair, key, _ in ASSET_PAIRS:
        quotes[key] = _quote_from_awesome(body, pair)
    return quotes


async def _fetch_hg_brasil(settings) -> dict[str, Any]:
    body = await http_get_json(HG_BRASIL_URL, settings, params={"key": "free"})
    results = body.get("results") or {}
    currencies = results.get("currencies") or {}
    bitcoin = results.get("bitcoin") or {}
    stocks = results.get("stocks") or {}
    ibov = stocks.get("IBOVESPA") or {}

    quotes: dict[str, Any] = {}
    for _, key, currency_code in ASSET_PAIRS:
        if currency_code == "BTC":
            quotes[key] = {
                "pair": "BTC-BRL",
                "bid": bitcoin.get("buy"),
                "ask": bitcoin.get("sell"),
                "timestamp": body.get("valid_key"),
                "source": "hgbrasil",
            }
        else:
            currency = currencies.get(currency_code) or {}
            quotes[key] = {
                "pair": f"{currency_code}-BRL",
                "bid": currency.get("buy"),
                "ask": currency.get("sell"),
                "timestamp": body.get("valid_key"),
                "source": "hgbrasil",
            }

    quotes["ibovespa"] = {
        "symbol": "IBOVESPA",
        "previous_close": ibov.get("points"),
        "variation_percent": ibov.get("variation"),
        "source": "hgbrasil",
    }
    return quotes


def _fetch_yfinance_sync(keys: list[str] | None = None) -> dict[str, Any]:
    import yfinance as yf

    pair_labels = {key: pair for pair, key, _ in ASSET_PAIRS}
    quotes: dict[str, Any] = {}

    target_tickers = {k: v for k, v in YFINANCE_TICKERS.items() if keys is None or k in keys}

    for key, ticker in target_tickers.items():
        history = yf.Ticker(ticker).history(period="5d")
        if history.empty:
            continue

        last_row = history.iloc[-1]
        close = float(last_row["Close"])

        if key == "ibovespa":
            previous_close = float(history.iloc[-2]["Close"]) if len(history) >= 2 else close
            quotes[key] = {
                "symbol": "IBOVESPA",
                "previous_close": round(previous_close, 2),
                "last_close": round(close, 2),
                "timestamp": str(history.index[-1]),
                "source": "yfinance",
            }
        else:
            quotes[key] = {
                "pair": pair_labels.get(key, ticker),
                "bid": round(close, 4),
                "ask": round(close, 4),
                "timestamp": str(history.index[-1]),
                "source": "yfinance",
            }
    return quotes


async def _fetch_yfinance(settings) -> dict[str, Any]:
    return await asyncio.to_thread(_fetch_yfinance_sync)


async def _fetch_quotes(settings) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    quotes: dict[str, Any] = {}

    try:
        quotes = await _fetch_awesomeapi(settings)
        if quotes.get("usd_brl", {}).get("bid"):
            ibov = await _fetch_ibovespa_yfinance()
            if ibov:
                quotes["ibovespa"] = ibov
            return quotes, errors
    except Exception as exc:
        logger.warning("AwesomeAPI fetch failed: %s", exc)
        errors.append(f"AwesomeAPI: {exc}")

    try:
        quotes = await _fetch_hg_brasil(settings)
        if quotes.get("usd_brl", {}).get("bid"):
            return quotes, errors
    except Exception as exc:
        logger.warning("HG Brasil fetch failed: %s", exc)
        errors.append(f"HG Brasil: {exc}")

    try:
        quotes = await _fetch_yfinance(settings)
        if quotes:
            return quotes, errors
    except Exception as exc:
        logger.warning("yfinance fetch failed: %s", exc)
        errors.append(f"yfinance: {exc}")

    return quotes, errors


async def _fetch_ibovespa_yfinance() -> dict[str, Any] | None:
    quotes = await asyncio.to_thread(_fetch_yfinance_sync, ["ibovespa"])
    return quotes.get("ibovespa")


async def _scrape_target(target: dict[str, Any], settings) -> list[dict[str, str]]:
    url = target.get("url")
    if not url:
        return []

    if target.get("use_playwright"):
        logger.warning("Playwright requested for %s but is not installed; skipping", target.get("name"))
        return []

    html = await http_get_text(
        url,
        settings,
        headers={"User-Agent": "Mozilla/5.0 (compatible; DailyJournalBot/2.0)"},
    )
    soup = BeautifulSoup(html, "lxml")
    selector = (target.get("selectors") or {}).get("headlines", "h2 a, h3 a")
    max_items = int((target.get("selectors") or {}).get("max_items", 5))

    headlines: list[dict[str, str]] = []
    for element in soup.select(selector):
        title = element.get_text(strip=True)
        href = element.get("href", "")
        if not title:
            continue
        headlines.append({"title": title, "url": href})
        if len(headlines) >= max_items:
            break
    return headlines


async def fetch(settings) -> ScraperResult:
    section = "finance"
    finance_cfg = settings.get("finance") or {}
    data: dict[str, Any] = {"headlines": []}
    errors: list[str] = []

    quotes, quote_errors = await _fetch_quotes(settings)
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

    has_quotes = any(data.get(key) for key in ("usd_brl", "eur_brl", "ars_brl", "btc_brl", "ibovespa"))
    if not has_quotes and not data["headlines"]:
        return ScraperResult(section=section, status="error", error="; ".join(errors) or "No finance data")

    status = "ok"
    error_msg = None
    if errors:
        status = "partial" if has_quotes or data["headlines"] else "error"
        error_msg = "; ".join(errors)

    return ScraperResult(section=section, status=status, data=data, error=error_msg)

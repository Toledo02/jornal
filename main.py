"""Daily journal orchestrator."""

from __future__ import annotations

import logging
import sys
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Callable

from config.settings import load_settings
from core.ai_engine import generate_journal
from core.telegram_sender import send_journal
from core.utils import ScraperResult, setup_logging
from scrapers import finance, football, gaming, news_rss, promotions, weather

ScraperFn = Callable[..., ScraperResult]

SCRAPERS: list[tuple[str, ScraperFn]] = [
    ("weather", weather.fetch),
    ("finance", finance.fetch),
    ("tech_news", lambda s: news_rss.fetch(s, category="tech")),
    ("world_news", lambda s: news_rss.fetch(s, category="world")),
    ("gaming", gaming.fetch),
    ("football", football.fetch),
    ("promotions", promotions.fetch),
]


def _run_scraper(name: str, fn: ScraperFn, settings) -> ScraperResult:
    logger = logging.getLogger("journal")
    try:
        result = fn(settings)
        logger.info("Scraper %s finished with status=%s", name, result.status)
        return result
    except Exception as exc:
        logger.warning("Scraper %s raised exception: %s", name, exc)
        return ScraperResult(section=name, status="error", error=str(exc))


def _collect_data(settings) -> dict[str, ScraperResult]:
    logger = logging.getLogger("journal")
    timeout = int(settings.orchestrator.get("scraper_timeout_seconds", 30))
    results: dict[str, ScraperResult] = {}

    with ThreadPoolExecutor(max_workers=len(SCRAPERS)) as executor:
        futures = {
            name: executor.submit(_run_scraper, name, fn, settings) for name, fn in SCRAPERS
        }
        for name, future in futures.items():
            try:
                results[name] = future.result(timeout=timeout)
            except FuturesTimeoutError:
                logger.warning("Scraper %s timed out after %ss", name, timeout)
                results[name] = ScraperResult(section=name, status="error", error=f"Timeout after {timeout}s")
            except Exception as exc:
                logger.warning("Scraper %s future failed: %s", name, exc)
                results[name] = ScraperResult(section=name, status="error", error=str(exc))

    return results


def _build_payload(results: dict[str, ScraperResult]) -> dict:
    return {name: result.to_payload() for name, result in results.items()}


def _successful_sections(results: dict[str, ScraperResult]) -> int:
    return sum(1 for result in results.values() if result.status in {"ok", "partial"})


def main() -> int:
    settings = load_settings()
    logger = setup_logging(settings)
    logger.info("Starting daily journal pipeline")

    results = _collect_data(settings)
    ok_count = _successful_sections(results)
    min_sections = int(settings.orchestrator.get("min_sections_for_send", 1))

    for name, result in results.items():
        if result.status == "error":
            logger.warning("Section %s failed: %s", name, result.error)
        elif result.status == "partial":
            logger.info("Section %s partial: %s", name, result.error)

    if ok_count < min_sections:
        logger.error("Not enough successful sections (%s/%s). Aborting.", ok_count, min_sections)
        return 1

    payload = _build_payload(results)
    journal_text = generate_journal(payload, settings)
    logger.info("Journal generated (%s chars)", len(journal_text))

    try:
        send_journal(journal_text, settings)
        logger.info("Journal sent to Telegram successfully")
    except Exception as exc:
        logger.error("Failed to send journal: %s", exc)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

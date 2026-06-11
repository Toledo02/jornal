"""Unified RSS collector driven by config categories."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

import feedparser
import requests

from core.utils import ScraperResult, request_timeout

logger = logging.getLogger(__name__)


def _normalize_link(link: str) -> str:
    parsed = urlparse(link)
    return f"{parsed.netloc}{parsed.path}".rstrip("/")


def _fetch_feed(url: str, max_entries: int, settings) -> list[dict[str, Any]]:
    response = requests.get(
        url,
        timeout=request_timeout(settings),
        headers={"User-Agent": "Mozilla/5.0 (compatible; DailyJournalBot/1.0)"},
    )
    response.raise_for_status()
    parsed = feedparser.parse(response.content)

    items: list[dict[str, Any]] = []
    for entry in parsed.entries[:max_entries]:
        items.append(
            {
                "title": entry.get("title", "").strip(),
                "link": entry.get("link", ""),
                "published": entry.get("published", entry.get("updated", "")),
                "summary": entry.get("summary", "")[:500],
                "source_feed": url,
            }
        )
    return items


def fetch(settings, category: str = "tech") -> ScraperResult:
    section = f"{category}_news" if category in {"tech", "world"} else category
    rss_cfg = settings.get("rss") or {}
    feeds = (settings.get("rss_feeds") or {}).get(category, [])

    if not feeds:
        return ScraperResult(
            section=section,
            status="error",
            error=f"No RSS feeds configured for category '{category}'",
        )

    entries_per_feed = int(rss_cfg.get("entries_per_feed", 5))
    if category == "world":
        entries_per_feed = max(entries_per_feed, int(rss_cfg.get("world_min_entries", 10)))

    all_items: list[dict[str, Any]] = []
    errors: list[str] = []
    seen: set[str] = set()

    for feed_url in feeds:
        try:
            items = _fetch_feed(feed_url, entries_per_feed, settings)
            for item in items:
                key = _normalize_link(item.get("link") or item.get("title", ""))
                if key in seen:
                    continue
                seen.add(key)
                all_items.append(item)
        except Exception as exc:
            logger.warning("RSS fetch failed for %s: %s", feed_url, exc)
            errors.append(f"{feed_url}: {exc}")

    if not all_items:
        return ScraperResult(section=section, status="error", error="; ".join(errors) or "No RSS entries")

    status = "partial" if errors else "ok"
    return ScraperResult(
        section=section,
        status=status,
        data={"category": category, "items": all_items, "count": len(all_items)},
        error="; ".join(errors) if errors else None,
    )

"""Unified async RSS collector driven by config categories."""

from __future__ import annotations

import logging
import re
from typing import Any

import feedparser

from core.utils import ScraperResult, http_get

logger = logging.getLogger(__name__)


def _normalize_title(title: str) -> str:
    cleaned = re.sub(r"\s+", " ", title.lower().strip())
    return re.sub(r"[^\w\s]", "", cleaned)


async def _fetch_feed(url: str, max_entries: int, settings) -> list[dict[str, Any]]:
    response = await http_get(url, settings)
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


async def fetch(settings, category: str = "tech") -> ScraperResult:
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
    max_items = int(rss_cfg.get("max_items_per_category", 15))

    all_items: list[dict[str, Any]] = []
    errors: list[str] = []
    seen_titles: set[str] = set()

    for feed_url in feeds:
        try:
            items = await _fetch_feed(feed_url, entries_per_feed, settings)
            for item in items:
                title_key = _normalize_title(item.get("title", ""))
                if not title_key or title_key in seen_titles:
                    continue
                seen_titles.add(title_key)
                all_items.append(item)
        except Exception as exc:
            logger.warning("RSS fetch failed for %s: %s", feed_url, exc)
            errors.append(f"{feed_url}: {exc}")

    all_items = all_items[:max_items]

    if not all_items:
        return ScraperResult(section=section, status="error", error="; ".join(errors) or "No RSS entries")

    status = "partial" if errors else "ok"
    return ScraperResult(
        section=section,
        status=status,
        data={"category": category, "items": all_items, "count": len(all_items)},
        error="; ".join(errors) if errors else None,
    )

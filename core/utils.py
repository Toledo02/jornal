"""Shared utilities for scrapers and orchestrator."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

USER_AGENT = "Mozilla/5.0 (compatible; DailyJournalBot/2.0)"
DEFAULT_HEADERS = {"User-Agent": USER_AGENT}


@dataclass
class ScraperResult:
    section: str
    status: str  # ok | partial | error
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_payload(self) -> dict[str, Any]:
        payload = dict(self.data)
        if self.status == "error":
            payload["_error"] = self.error or "Unknown error"
        elif self.status == "partial" and self.error:
            payload["_warning"] = self.error
        return payload


def setup_logging(settings) -> logging.Logger:
    log_cfg = settings.logging_config
    level_name = log_cfg.get("level", "INFO")
    level = getattr(logging, level_name.upper(), logging.INFO)
    log_dir = settings.project_root / log_cfg.get("directory", "logs")
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / f"journal_{datetime.now():%Y%m%d}.log"
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(),
        ],
        force=True,
    )
    return logging.getLogger("journal")


def request_timeout(settings) -> int:
    return int(settings.get("orchestrator", "request_timeout_seconds", default=15))


def _client_timeout(settings) -> httpx.Timeout:
    return httpx.Timeout(request_timeout(settings))


async def http_get(
    url: str,
    settings,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
) -> httpx.Response:
    merged_headers = {**DEFAULT_HEADERS, **(headers or {})}
    async with httpx.AsyncClient(timeout=_client_timeout(settings), follow_redirects=True) as client:
        response = await client.get(url, headers=merged_headers, params=params)
        response.raise_for_status()
        return response


async def http_get_text(url: str, settings, **kwargs: Any) -> str:
    response = await http_get(url, settings, **kwargs)
    return response.text


async def http_get_json(url: str, settings, **kwargs: Any) -> Any:
    response = await http_get(url, settings, **kwargs)
    return response.json()


def format_date_pt_br(dt: datetime) -> str:
    weekdays = (
        "Segunda-feira",
        "Terça-feira",
        "Quarta-feira",
        "Quinta-feira",
        "Sexta-feira",
        "Sábado",
        "Domingo",
    )
    months = (
        "Janeiro",
        "Fevereiro",
        "Março",
        "Abril",
        "Maio",
        "Junho",
        "Julho",
        "Agosto",
        "Setembro",
        "Outubro",
        "Novembro",
        "Dezembro",
    )
    return f"{weekdays[dt.weekday()]}, {dt.day} de {months[dt.month - 1]} de {dt.year}"

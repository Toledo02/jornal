"""Shared utilities for scrapers and orchestrator."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


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

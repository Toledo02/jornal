"""Configuration loader for the daily journal agent."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = CONFIG_DIR / "config.yaml"
DEFAULT_ENV_PATH = CONFIG_DIR / ".env"


@dataclass
class Settings:
    """Runtime settings loaded from config.yaml and .env."""

    config: dict[str, Any]
    project_root: Path = field(default_factory=lambda: PROJECT_ROOT)

    # Secrets / env
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_temperature: float = 0.4
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    awesomeapi_token: str = ""

    def get(self, *keys: str, default: Any = None) -> Any:
        node: Any = self.config
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    @property
    def orchestrator(self) -> dict[str, Any]:
        return self.config.get("orchestrator", {})

    @property
    def logging_config(self) -> dict[str, Any]:
        return self.config.get("logging", {})


def load_settings(
    config_path: Path | None = None,
    env_path: Path | None = None,
) -> Settings:
    config_path = config_path or DEFAULT_CONFIG_PATH
    env_path = env_path or DEFAULT_ENV_PATH

    if env_path.exists():
        load_dotenv(env_path)
    else:
        load_dotenv()

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with config_path.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}

    settings = Settings(
        config=config,
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        openai_temperature=float(os.getenv("OPENAI_TEMPERATURE", "0.4")),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        awesomeapi_token=os.getenv("AWESOMEAPI_TOKEN", ""),
    )

    return settings

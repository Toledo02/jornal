"""Configuration loader for the daily journal agent."""

from __future__ import annotations

import logging
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

# Medido em 28/07/2026 com o payload real: o 3.6-flash sintetiza as manchetes em texto próprio
# (o 2.5-flash copiava os títulos crus), sai ~25% mais curto e mais rápido. Os modelos "pro"
# respondem 429 RESOURCE_EXHAUSTED na cota gratuita — não adianta configurá-los aqui.
DEFAULT_MODEL = "gemini-3.6-flash"
DEFAULT_FALLBACK_MODELS = ["gemini-2.5-flash", "gemini-2.0-flash"]


@dataclass
class Settings:
    """Runtime settings loaded from config.yaml and .env."""

    config: dict[str, Any]
    project_root: Path = field(default_factory=lambda: PROJECT_ROOT)

    # Secrets / env
    llm_api_key: str = ""
    llm_model: str = DEFAULT_MODEL
    llm_temperature: float = 0.4
    llm_fallback_models: list[str] = field(default_factory=lambda: list(DEFAULT_FALLBACK_MODELS))
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    awesomeapi_token: str = ""
    football_data_token: str = ""

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


def _resolve_model(name: str) -> str:
    """Protege contra o `gpt-4o-mini` que o .env.example distribuiu por engano.

    O projeto migrou para o Gemini mas manteve os nomes de variável antigos, então um .env
    copiado do exemplo aponta para um modelo da OpenAI e todo o pipeline cai no fallback.
    """
    if name and not name.lower().startswith("gemini"):
        logging.getLogger(__name__).warning(
            "Modelo '%s' não é do Gemini; usando '%s'. Ajuste LLM_MODEL no config/.env.",
            name,
            DEFAULT_MODEL,
        )
        return DEFAULT_MODEL
    return name or DEFAULT_MODEL


def _env(*names: str, default: str = "") -> str:
    """Primeiro nome que existir no ambiente.

    Os nomes OPENAI_* são herança de quando o projeto usava OpenAI; hoje carregam credenciais do
    Gemini. Continuam aceitos para não quebrar o .env que já está na VPS.
    """
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


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

    fallback_raw = _env("LLM_FALLBACK_MODELS", "GEMINI_FALLBACK_MODELS")
    fallback_models = (
        [name.strip() for name in fallback_raw.split(",") if name.strip()]
        if fallback_raw
        else list(DEFAULT_FALLBACK_MODELS)
    )

    return Settings(
        config=config,
        llm_api_key=_env("LLM_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY"),
        llm_model=_resolve_model(_env("LLM_MODEL", "GEMINI_MODEL", "OPENAI_MODEL")),
        llm_temperature=float(_env("LLM_TEMPERATURE", "OPENAI_TEMPERATURE", default="0.4")),
        llm_fallback_models=fallback_models,
        telegram_bot_token=_env("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_env("TELEGRAM_CHAT_ID"),
        awesomeapi_token=_env("AWESOMEAPI_TOKEN"),
        football_data_token=_env("FOOTBALL_DATA_TOKEN"),
    )

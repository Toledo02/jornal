"""Telegram Bot API sender with message splitting and bulletproof parse_mode."""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
MAX_MESSAGE_LENGTH = 4096


def _split_message(text: str, limit: int = MAX_MESSAGE_LENGTH) -> list[str]:
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        candidate = f"{current}\n{line}".strip() if current else line
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]
        current = line
    if current:
        chunks.append(current)
    return chunks


def send_message(text: str, settings, parse_mode: str | None = "HTML") -> list[dict[str, Any]]:
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id

    if not token or not chat_id:
        raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in config/.env")

    url = TELEGRAM_API.format(token=token)
    responses: list[dict[str, Any]] = []

    for chunk in _split_message(text):
        payload = {
            "chat_id": chat_id,
            "text": chunk,
            "disable_web_page_preview": True,
        }
        
        # Só adiciona o parse_mode se ele for válido (corrige o erro do fallback)
        if parse_mode:
            payload["parse_mode"] = parse_mode

        response = httpx.post(url, json=payload, timeout=30)
        body = response.json()
        
        if not response.is_success or not body.get("ok"):
            logger.error("Telegram API error: %s", body)
            raise RuntimeError(f"Telegram send failed: {body}")
            
        responses.append(body)

    return responses


def send_journal(text: str, settings) -> list[dict[str, Any]]:
    try:
        # Tenta enviar formatado bonitinho primeiro
        return send_message(text, settings, parse_mode="HTML")
    except RuntimeError:
        logger.warning("Markdown send failed; retrying without parse_mode (plain text)")
        # Se falhar por causa de um asterisco solto, envia como texto puro!
        return send_message(text, settings, parse_mode=None)
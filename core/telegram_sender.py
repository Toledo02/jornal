"""Telegram Bot API sender.

Desde a Rodada 6 o jornal é uma página web e o Telegram recebe só avisos curtos em texto puro: o
link da edição do dia e os alertas operacionais. Por isso não há mais sanitização de HTML, divisão
com reabertura de tags nem fallback para texto — nada aqui manda marcação.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
MAX_MESSAGE_LENGTH = 4096


def _split_message(text: str, limit: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """Divide por linhas para caber no limite do Telegram.

    Aviso acima do limite é improvável (as mensagens de erro já são cortadas em `_clean_error`),
    mas a API rejeita a mensagem inteira em vez de truncar, então o corte fica aqui.
    """
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        candidate = f"{current}\n{line}" if current else line
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


def send_message(text: str, settings) -> list[dict[str, Any]]:
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id

    if not token or not chat_id:
        raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in config/.env")

    url = TELEGRAM_API.format(token=token)
    responses: list[dict[str, Any]] = []

    for chunk in _split_message(text):
        response = httpx.post(
            url,
            json={"chat_id": chat_id, "text": chunk, "disable_web_page_preview": True},
            timeout=30,
        )
        body = response.json()

        if not response.is_success or not body.get("ok"):
            logger.error("Telegram API error: %s", body)
            raise RuntimeError(f"Telegram send failed: {body}")

        responses.append(body)

    return responses


def send_alert(text: str, settings) -> list[dict[str, Any]]:
    """Envia um aviso: o link do jornal do dia ou uma falha operacional.

    Sem parse_mode de propósito: o aviso carrega mensagens de erro cruas, cheias de <, > e &, que
    quebrariam o parser HTML justamente quando algo já deu errado.
    """
    return send_message(text, settings)

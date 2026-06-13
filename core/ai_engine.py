"""LLM engine: consolidates raw scraper data into a Telegram-ready journal using official Gemini SDK."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo
import time

# Importa o SDK oficial do Google GenAI
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a personal morning briefing editor.

Your task is to transform raw JSON data from multiple sources into a concise daily journal in Brazilian Portuguese (pt-BR).

Rules:
1. Output ONLY the final journal text in Markdown compatible with Telegram. 
   - CRITICAL: Every opening asterisk for bold (*text*) MUST have a matching closing asterisk. Never leave a dangling asterisk.
   - Do not use nested formatting.
2. Write in pt-BR with a clear, informative tone.
3. Structure the journal with clear section headers. ALWAYS add TWO blank lines (\n\n) before starting a new section emoji (e.g., 🌦️, 💵, 💻, 🌍, 🎮, ⚽) to prevent text crowding.
   - 🌦️ Clima
   - 💵 Economia & Investimentos
   - 💻 Tecnologia & Dev (top 5 tech news + GitHub trending repos)
   - 🌍 Mundo (EXACTLY 3 most relevant global facts: wars, macroeconomics, historic events; ignore clickbait)
   - 🎮 Gaming (free/cheap deals + relevant gaming news)
   - ⚽ Futebol (next matches and last results for configured teams)
   - 🛒 Achados & Promoções (price changes and alerts)
4. For world news, strictly select only the 3 most relevant global stories from the provided headlines.
5. Omit sections that failed completely (_error). For partial sections (_warning), include available data briefly.
6. Keep the full message between 1500-2500 characters when possible.
7. Do not invent facts not present in the input data.
8. Do not wrap the output in code fences.
"""


def _build_user_prompt(payload: dict[str, Any], settings) -> str:
    city = settings.get("weather", "city", default="")
    now = datetime.now(ZoneInfo("America/Sao_Paulo"))
    meta = {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M"),
        "timezone": "America/Sao_Paulo",
        "city": city,
        "failed_sections": [
            section for section, data in payload.items() if isinstance(data, dict) and data.get("_error")
        ],
    }
    return (
        "Create today's personal morning journal from this JSON payload.\n\n"
        f"Metadata:\n{json.dumps(meta, ensure_ascii=False, indent=2)}\n\n"
        f"Data:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _fallback_journal(payload: dict[str, Any], settings) -> str:
    """Simple template when the LLM call fails."""
    lines = ["*📰 Jornal Matinal (modo fallback)*", ""]
    city = settings.get("weather", "city", default="")

    weather = payload.get("weather", {})
    if weather and not weather.get("_error"):
        lines.append(
            f"🌦️ *Clima ({city})*: "
            f"mín {weather.get('temp_min_c')}°C, máx {weather.get('temp_max_c')}°C, "
            f"chuva {weather.get('rain_probability_percent')}%"
        )

    finance = payload.get("finance", {})
    if finance and not finance.get("_error"):
        quote = finance.get("usd_brl") or {}
        lines.append(f"💵 *Dólar*: R$ {quote.get('bid', 'N/A')}")

    for section_key, emoji, title in [
        ("tech_news", "💻", "Tech"),
        ("world_news", "🌍", "Mundo"),
        ("gaming", "🎮", "Gaming"),
        ("football", "⚽", "Futebol"),
        ("promotions", "🛒", "Promoções"),
    ]:
        data = payload.get(section_key, {})
        if not data or data.get("_error"):
            continue
        lines.append(f"{emoji} *{title}*: dados coletados ({json.dumps(data, ensure_ascii=False)[:300]}...)")

    return "\n".join(lines)


def generate_journal(payload: dict[str, Any], settings) -> str:
    if not settings.openai_api_key:
        logger.warning("Chave de API não configurada; usando fallback journal")
        return _fallback_journal(payload, settings)

    user_prompt = _build_user_prompt(payload, settings)
    
    max_retries = 3
    retry_delay = 10  # segundos de espera entre tentativas

    for attempt in range(1, max_retries + 1):
        try:
            client = genai.Client(api_key=settings.openai_api_key)
            
            response = client.models.generate_content(
                model=settings.openai_model,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    temperature=settings.openai_temperature,
                ),
            )
            
            content = (response.text or "").strip()
            if not content:
                raise ValueError("Resposta vazia do Gemini")
            return content
            
        except Exception as exc:
            logger.warning(f"Tentativa {attempt}/{max_retries} falhou via Gemini: {exc}")
            if attempt < max_retries:
                logger.info(f"Aguardando {retry_delay} segundos antes de tentar novamente...")
                time.sleep(retry_delay)
            else:
                logger.error("Todas as tentativas de geração via Gemini falharam.")
                return _fallback_journal(payload, settings)
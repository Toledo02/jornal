"""LLM engine: consolidates raw scraper data into a Telegram-ready journal using the Gemini SDK."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from google import genai
from google.genai import types

from core.utils import format_date_pt_br, now_local

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a personal morning briefing editor.

Your task is to transform raw JSON data from multiple sources into a concise daily journal in Brazilian Portuguese (pt-BR).

Rules:
1. Output ONLY the final journal text formatted in HTML compatible with Telegram.
   - Use <b>text</b> for bold and <i>text</i> for italics. No other formatting tags exist.
   - Never use Markdown asterisks (*) or underscores (_) — they render literally in Telegram.
   - Never use <br>, <p>, <ul> or <li>. Separate blocks with literal blank lines (\\n\\n).
   - For bullet points use the character "•" at the start of the line. This is the ONLY
     acceptable bullet marker; "*" and "-" are forbidden.
   - Leave one blank line between a section header and its content, and two blank lines before
     the next section emoji.
2. Write in pt-BR with a clear, informative tone.
3. Start the journal with the EXACT date header provided in metadata.date_header_pt_br. Copy it
   verbatim as the first line — do not invent or alter the date.
4. SECTION STRUCTURE — use EXACTLY these sections, with these exact titles, in this exact order.
   Never add, rename, remove, split or reorder a section. If a section has no data, omit it
   entirely (see rule 7) rather than inventing a replacement.
   - 🌦️ Clima
   - 💵 Economia & Investimentos
   - 💻 Tecnologia & Dev
   - 🌍 Mundo
   - 🎬 Cultura Pop & Entretenimento
   - 🎮 Ofertas & Games Grátis
   - ⚽ Futebol
   - 🛒 Achados & Promoções
   - 📚 Neste Dia na História
5. SECTION CONTENT:
   - Economia: present USD, EUR, ARS, BTC and IBOVESPA as bullet points, never as running text.
     Each asset carries a preformatted "display" field — copy that string EXACTLY. Never reformat
     a number, never convert currency, and never compute a percentage yourself. If an asset has no
     "display" field, omit that bullet.
   - Tecnologia & Dev: top tech news, followed by the GitHub trending repositories.
   - Mundo: EXACTLY 3 most relevant global facts (wars, macroeconomics, historic events). Ignore
     clickbait.
   - Cultura Pop: prioritise major film/streaming releases, anime, book adaptations and updates on
     competitive scenes or MOBA/tactical game patches. Ignore rumours, minor delays and celebrity
     gossip.
   - Ofertas & Games Grátis: list the CheapShark deals as bullets, highlighting the free ones
     (is_free = true) and the deepest discounts.
   - Futebol: the data contains recent headlines WITHOUT explicit dates. Never write "ontem",
     "hoje", "amanhã" or any relative time reference for football — you cannot know when those
     events happened. Summarise them without dating them.
   - Neste Dia na História: one concise historical fact for today's calendar day and month, from
     your own knowledge.
6. LINKS: HTML links (<a href="URL">Text</a>) are allowed EXCLUSIVELY in:
   - Ofertas & Games Grátis and Achados & Promoções, as <a href="URL">[Resgatar]</a> or
     <a href="URL">[Ver Oferta]</a> at the end of each item.
   - GitHub Trending, as "...descrição do repo. <a href="URL">[Ver Repo]</a>".
   No links anywhere else — Mundo, Tecnologia (news), Economia, Cultura Pop and Futebol must be
   pure text. No loose URLs anywhere.
7. OMITTING SECTIONS: omit entirely any section whose data failed (_error) or is empty (no items,
   no deals, no products). Do not write "nenhuma oferta hoje" or similar filler — just leave the
   section out. For partial sections (_warning), include the available data without commenting on
   the failure.
8. Target 2000-3500 characters total.
9. Do not invent facts not present in the input data. The only exception is "Neste Dia na
   História", which uses your own historical knowledge.
10. Do not wrap the output in code fences.
11. ANTI-REPETITION POLICY:
    - The tag <PREVIOUS_DAY_JOURNAL> contains yesterday's journal, when available.
    - This policy applies ONLY to these news sections: Tecnologia & Dev, Mundo, Cultura Pop &
      Entretenimento, and Futebol. Do not repeat a story already covered yesterday unless there is
      a significant development, an impactful update, or the continuation of an ongoing event.
    - This policy NEVER applies to Clima, Economia & Investimentos, Ofertas & Games Grátis,
      Achados & Promoções or Neste Dia na História. Those are expected to look similar every day
      and must ALWAYS be present regardless of yesterday's content. Never omit the weather or the
      exchange rates because they resemble yesterday's.
"""

# Erros que não melhoram com nova tentativa: modelo inexistente, chave inválida, prompt malformado.
PERMANENT_ERROR_MARKERS = ("400", "401", "403", "404", "INVALID_ARGUMENT", "PERMISSION_DENIED")


def _build_user_prompt(payload: dict[str, Any], settings, previous_journal_text: str | None = None) -> str:
    now = now_local()
    date_header = format_date_pt_br(now)
    meta = {
        "date": now.strftime("%Y-%m-%d"),
        "date_header_pt_br": date_header,
        "time": now.strftime("%H:%M"),
        "timezone": "America/Sao_Paulo",
        "city": settings.get("weather", "city", default=""),
        "instruction": (
            f"Use EXACTLY this string as the journal header (first line): {date_header}"
        ),
        "failed_sections": [
            section for section, data in payload.items() if isinstance(data, dict) and data.get("_error")
        ],
    }

    previous_context = ""
    if previous_journal_text and previous_journal_text.strip():
        previous_context = (
            f"\n\n<PREVIOUS_DAY_JOURNAL>\n{previous_journal_text.strip()}\n</PREVIOUS_DAY_JOURNAL>"
        )

    return (
        "Create today's personal morning journal from this JSON payload.\n\n"
        f"Metadata:\n{json.dumps(meta, ensure_ascii=False, indent=2)}\n\n"
        f"Data:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
        f"{previous_context}"
    )


def _fallback_journal(payload: dict[str, Any], settings) -> str:
    """Jornal mínimo em texto puro, para quando o LLM não responde.

    Sem marcação: a versão anterior usava asteriscos de Markdown e era enviada como HTML, então
    os asteriscos apareciam literalmente na mensagem. Texto puro atravessa o sanitizador intacto.
    """
    lines = [format_date_pt_br(now_local()), "", "(modo fallback — o gerador de texto falhou)", ""]

    weather = payload.get("weather") or {}
    if weather and not weather.get("_error"):
        lines.append(
            f"🌦️ Clima em {weather.get('city', '')}: "
            f"mín {weather.get('temp_min_c')}°C, máx {weather.get('temp_max_c')}°C, "
            f"chuva {weather.get('rain_probability_percent')}%"
        )

    finance = payload.get("finance") or {}
    if finance and not finance.get("_error"):
        quotes = [
            f"{label}: {(finance.get(key) or {}).get('display')}"
            for key, label in (
                ("usd_brl", "Dólar"),
                ("eur_brl", "Euro"),
                ("btc_brl", "Bitcoin"),
                ("ibovespa", "IBOVESPA"),
            )
            if (finance.get(key) or {}).get("display")
        ]
        if quotes:
            lines.append("💵 " + " | ".join(quotes))

    for key, emoji, title, field in (
        ("tech_news", "💻", "Tecnologia", "items"),
        ("world_news", "🌍", "Mundo", "items"),
        ("pop_culture", "🎬", "Cultura Pop", "items"),
    ):
        section = payload.get(key) or {}
        items = section.get(field) or []
        if not items:
            continue
        lines.append("")
        lines.append(f"{emoji} {title}")
        lines.extend(f"• {item.get('title', '')}" for item in items[:4])

    return "\n".join(lines)


def _is_permanent(error: Exception) -> bool:
    message = str(error)
    return any(marker in message for marker in PERMANENT_ERROR_MARKERS)


def _generate_once(model: str, user_prompt: str, settings) -> str:
    client = genai.Client(api_key=settings.llm_api_key)
    response = client.models.generate_content(
        model=model,
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=settings.llm_temperature,
        ),
    )
    content = (response.text or "").strip()
    if not content:
        raise ValueError("Resposta vazia do Gemini")
    return content


def generate_journal(payload: dict[str, Any], settings, previous_journal_text: str | None = None) -> str:
    if not settings.llm_api_key:
        logger.warning("Chave de API não configurada; usando fallback journal")
        return _fallback_journal(payload, settings)

    user_prompt = _build_user_prompt(payload, settings, previous_journal_text)

    # Um 503 do Gemini é sobrecarga do modelo e costuma durar minutos, não segundos: o intervalo
    # fixo de 10s gastava as três tentativas em menos de um minuto e caía no fallback.
    delays = [10, 30, 90]
    models = [settings.llm_model, *settings.llm_fallback_models]

    for model in models:
        for attempt, delay in enumerate(delays, start=1):
            try:
                content = _generate_once(model, user_prompt, settings)
                if model != settings.llm_model:
                    logger.warning("Jornal gerado pelo modelo de reserva %s", model)
                return content
            except Exception as exc:
                if _is_permanent(exc):
                    logger.error("Erro permanente em %s, sem retry: %s", model, exc)
                    break
                logger.warning("Tentativa %s/%s falhou em %s: %s", attempt, len(delays), model, exc)
                if attempt < len(delays):
                    logger.info("Aguardando %ss antes de tentar novamente", delay)
                    time.sleep(delay)

    logger.error("Todas as tentativas de geração falharam; usando fallback")
    return _fallback_journal(payload, settings)

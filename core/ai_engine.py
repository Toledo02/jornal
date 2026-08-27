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
   - Never use <br>, <p>, <ul>, <li> or heading tags. Separate blocks with literal blank lines.
   - For bullet points use the character "•" at the start of the line. This is the ONLY
     acceptable bullet marker; "*" and "-" are forbidden.
2. Write in pt-BR with a clear, informative tone.
3. Start the journal with the EXACT date header provided in metadata.date_header_pt_br. Copy it
   verbatim as the first line, wrapped in <b>...</b> — do not invent or alter the date.
4. SECTION HEADERS — every section starts with a header block written EXACTLY like this, and
   nothing else may appear on those two lines:

   ━━━━━━━━━━━━━━━
   <b>🌦️ CLIMA</b>

   That is: a line with the rule "━━━━━━━━━━━━━━━", then the title line in <b>, the title always
   in UPPERCASE, then one blank line before the content. Telegram has no font-size control, so
   uppercase + bold + the rule above is what makes a header read as a heading. Never write a
   header as plain sentence case, never put content on the header line.
5. SECTION STRUCTURE — use EXACTLY these sections, with these exact titles, in this exact order.
   Never add, rename, remove, split or reorder a section. If a section has no data, omit it
   entirely (see rule 8) rather than inventing a replacement.
   - 🌦️ CLIMA
   - 💵 ECONOMIA
   - 📈 IDEIAS DE INVESTIMENTO
   - 💻 TECNOLOGIA & DEV
   - 🌍 MUNDO
   - 🎬 CULTURA POP & ENTRETENIMENTO
   - 🎮 OFERTAS & GAMES GRÁTIS
   - ⚽ FUTEBOL
   - 🛒 ACHADOS & PROMOÇÕES
   - 📚 NESTE DIA NA HISTÓRIA
6. SECTION CONTENT — sections marked "by topic" are read at a glance, so each fact gets its own
   bullet with a bold label. Never merge those into a paragraph.
   - CLIMA (by topic): one bullet per topic, in this order, using ONLY the preformatted strings
     from the payload — copy them verbatim and never rewrite a number:
       • <b>Agora:</b> "condition_now", "temp_now" and then "(sensação de X)" with "feels_like"
       • <b>Máxima e mínima:</b> "temp_max" e "temp_min" (in that order)
       • <b>Chuva:</b> "rain_summary". Omit the bullet when it is null; never speculate about rain.
       • <b>Sol:</b> "sun_summary"
       • <b>Vento:</b> "wind" — include only when the value is present
       • <b>Índice UV:</b> "uv_summary" — include ONLY when uv_label is "alto", "muito alto" or
         "extremo"
     If "vs_ontem" is present, append it to the "Máxima e mínima" bullet.
   - ECONOMIA: present USD, EUR, ARS, BTC and IBOVESPA as bullet points, never as running text.
     Each asset carries a preformatted "display" field — copy that string EXACTLY, including the
     percentage in parentheses, but the field is only the value: prefix EVERY bullet with a bold
     label naming the asset, e.g. "• <b>Dólar:</b> R$ 5,12 (+0,10%)". Use these labels in this
     order: <b>Dólar:</b> for usd_brl, <b>Euro:</b> for eur_brl, <b>Peso argentino:</b> for
     ars_brl, <b>Bitcoin:</b> for btc_brl, <b>Ibovespa:</b> for ibovespa. Never reformat a number,
     never convert currency, and never compute a percentage yourself. If an asset has no "display"
     field, omit that bullet. Close with at most one short line about the market headlines, when
     there are any.
   - IDEIAS DE INVESTIMENTO: built from the "investments" payload, which is data about interest
     rates plus, when present, a rotating set of real market tickers — not a portfolio and not
     personalized advice.
       • Open with one bullet listing the reference rates, copying the "display" of each indicator
         in "indicators" (e.g. "• <b>Referências:</b> Selic 14,00% a.a. · CDI 13,90% a.a. ·
         IPCA 4,64% em 12 meses").
       • If "stocks.candidates" is present and non-empty, add ONE bullet per entry, labelled in
         bold with its ticker and name (e.g. "• <b>PETR4 (Petrobras):</b> R$ 38,50 (+1,20%) —
         faixa de R$ 30,10 a R$ 42,80 em 12 meses."). Copy each entry's "display" and
         "range_display" EXACTLY as given. You may name ONLY the tickers present in
         "stocks.candidates" — never any other stock, fund, cryptocurrency or issuer. This is
         factual reporting of price and range, not a recommendation: never tell the reader to buy
         or sell that ticker, and never predict where its price is going.
       • Then ONE bullet per profile in "profiles", labelled in bold ("• <b>Conservador:</b> ...").
         Each is at most two sentences describing what class of asset makes sense at these rates
         and why, in plain language.
       • Every numeric claim MUST come from "talking_points", "derived", "indicators" or
         "stocks.candidates" — copy the numbers as formatted there. NEVER compute a yield, a
         projection or a percentage yourself, and never state how much money something would
         return.
       • Outside of the tickers listed in "stocks.candidates", speak about CLASSES of asset only
         (Tesouro Selic, CDB de liquidez diária, Tesouro IPCA+, fundos de índice amplos, reserva em
         dólar). NEVER invent a specific stock, ticker, cryptocurrency, broker, fund or issuer that
         is not in the payload, and never tell the reader to buy or sell. Use "faz sentido
         considerar", not "compre".
       • The "arrojado" bullet may reference the IBOVESPA or Bitcoin variation from the "finance"
         payload, or the movement of a listed ticker, as context, but must not predict where any
         price is going.
       • Close the section with the "disclaimer" string in <i>italics</i>, on its own line.
   - TECNOLOGIA & DEV: the 3 most relevant tech news, followed by at most 3 GitHub trending
     repositories.
   - MUNDO: EXACTLY 3 most relevant global facts (wars, macroeconomics, historic events). Ignore
     clickbait.
   - CULTURA POP: at most 3 items. Prioritise major film/streaming releases, anime, book
     adaptations and updates on competitive scenes or MOBA/tactical game patches. Ignore rumours,
     minor delays and celebrity gossip.
   - OFERTAS & GAMES GRÁTIS: the "free_games" list comes FIRST and is the point of the section —
     these are real giveaways. At most 4 of them, one bullet each with the title in bold, the
     platform, what it is worth, and the "ends_in" string when present (copy it verbatim; never
     compute a deadline yourself and never write "ontem"/"hoje"/"amanhã" from a raw date). An item
     with "is_new": false has already appeared in a previous journal: keep it only when it is
     ending, and then say so plainly. Then add at most 3 "deals" (paid discounts) as a secondary
     block; those are already filtered for quality, so mention the Steam rating when available.
     Deal prices are in US dollars from international stores: write them as "US$ 0,71" and never
     convert to reais. If "free_games" is empty, still show the deals; if both are empty, omit the
     whole section.
   - FUTEBOL: lead with "next_matches" (opponent, competition, day and kick-off time) — that is
     the point of the section. Then "last_matches" with the score. Those strings are already
     formatted: copy them, do not reword the dates. Finally summarise "news" briefly. The news
     items carry NO date, so never write "ontem", "hoje" or "amanhã" about them — only the match
     strings have reliable dates.
   - ACHADOS & PROMOÇÕES: "offers" are deals curated by Telegram channels, already in reais —
     copy the prices as they appear and never convert or recalculate. Pick the 4 most interesting
     ones and write one bullet each, with a short description and the price. When "coupon" is
     present, state the code in bold at the end of the description ("cupom <b>BLACK20</b>").
     "products" is the personal price watch: mention an item only when "alert" is true or
     "change_percent" is negative, stating the previous and current price.
   - NESTE DIA NA HISTÓRIA: ONE historical fact for today's calendar day and month, from your own
     knowledge, in a single sentence.
7. LINKS: HTML links (<a href="URL">Text</a>) are allowed EXCLUSIVELY in:
   - OFERTAS & GAMES GRÁTIS, at the end of each item, using the "url" field:
     <a href="URL">[Resgatar]</a> for a giveaway from "free_games", and
     <a href="URL">[Ver Oferta]</a> for a paid discount from "deals".
   - ACHADOS & PROMOÇÕES, as <a href="URL">[Ver no canal]</a> at the end of each item, using the
     "link" field and NOTHING ELSE. That link points to the post in the Telegram channel, which is
     where the coupon and the instructions are — linking straight to the store makes the reader pay
     full price. Never use "store_url", and never emit two links for the same offer.
   - GITHUB TRENDING, as "...descrição do repo. <a href="URL">[Ver Repo]</a>".
   No links anywhere else — MUNDO, TECNOLOGIA (news), ECONOMIA, IDEIAS DE INVESTIMENTO, CULTURA POP
   and FUTEBOL must be pure text. No loose URLs anywhere.
8. OMITTING SECTIONS: omit entirely any section whose data failed (_error) or is empty (no items,
   no deals, no products). Do not write "nenhuma oferta hoje" or similar filler — just leave the
   section out. For partial sections (_warning), include the available data without commenting on
   the failure.
9. LENGTH: 4000-5000 characters, with 5000 as a hard ceiling. What keeps it inside the budget:
   - Every news bullet is ONE sentence of at most 25 words, stating the fact directly. Do not open
     a bullet with a bold topic label ("<b>Crise no Irã:</b> ..."); start with the fact itself.
   - Each profile bullet in IDEIAS DE INVESTIMENTO is ONE sentence.
   - No introductory sentence for a section, no closing remark, no commentary about the journal
     itself, no "vale acompanhar" filler.
   - If everything does not fit, cut items from the end of the news sections — never drop a whole
     section and never truncate mid-sentence.
   Never leave a space before punctuation, including right after a closing tag.
10. Do not invent facts not present in the input data. The only exception is "NESTE DIA NA
    HISTÓRIA", which uses your own historical knowledge.
11. Do not wrap the output in code fences.
12. ANTI-REPETITION POLICY:
    - The tag <RECENT_JOURNALS> contains the journals of the previous days, each with its date.
      A story may only reappear if it is genuinely new or has evolved since ALL of them.
    - This policy applies ONLY to these news sections: TECNOLOGIA & DEV, MUNDO, CULTURA POP &
      ENTRETENIMENTO, and FUTEBOL. Do not repeat a story already covered yesterday unless there is
      a significant development, an impactful update, or the continuation of an ongoing event.
    - This policy NEVER applies to CLIMA, ECONOMIA, IDEIAS DE INVESTIMENTO, OFERTAS & GAMES
      GRÁTIS, ACHADOS & PROMOÇÕES or NESTE DIA NA HISTÓRIA. Those are expected to look similar
      every day and must ALWAYS be present regardless of yesterday's content. Never omit the
      weather or the exchange rates because they resemble yesterday's.
"""

# Erros que não melhoram com nova tentativa: modelo inexistente, chave inválida, prompt malformado.
PERMANENT_ERROR_MARKERS = ("400", "401", "403", "404", "INVALID_ARGUMENT", "PERMISSION_DENIED")


def _build_user_prompt(
    payload: dict[str, Any], settings, previous_journals: list[tuple[str, str]] | None = None
) -> str:
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
    if previous_journals:
        blocks = "\n\n".join(
            f"<JOURNAL date=\"{day}\">\n{text.strip()}\n</JOURNAL>"
            for day, text in previous_journals
            if text and text.strip()
        )
        if blocks:
            previous_context = f"\n\n<RECENT_JOURNALS>\n{blocks}\n</RECENT_JOURNALS>"

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
    lines = [format_date_pt_br(now_local()), "", "(modo fallback — o gerador de texto falhou)"]

    def header(title: str) -> None:
        lines.extend(["", "━━━━━━━━━━━━━━━", title, ""])

    weather = payload.get("weather") or {}
    if weather and not weather.get("_error"):
        header(f"🌦️ CLIMA — {weather.get('city', '')}")
        for label, value in (
            ("Agora", weather.get("temp_now")),
            ("Máxima e mínima", f"{weather.get('temp_max')} / {weather.get('temp_min')}"),
            ("Chuva", weather.get("rain_summary")),
            ("Sol", weather.get("sun_summary")),
        ):
            if value and "None" not in str(value):
                lines.append(f"• {label}: {value}")

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
            header("💵 ECONOMIA")
            lines.extend(f"• {quote}" for quote in quotes)

    investments = payload.get("investments") or {}
    indicators = (investments.get("indicators") or {}) if not investments.get("_error") else {}
    if indicators:
        header("📈 IDEIAS DE INVESTIMENTO")
        lines.extend(
            f"• {entry.get('label')}: {entry.get('display')}"
            for entry in indicators.values()
            if entry.get("display")
        )
        if investments.get("disclaimer"):
            lines.extend(["", investments["disclaimer"]])

    for key, title, field in (
        ("tech_news", "💻 TECNOLOGIA", "items"),
        ("world_news", "🌍 MUNDO", "items"),
        ("pop_culture", "🎬 CULTURA POP", "items"),
    ):
        section = payload.get(key) or {}
        items = section.get(field) or []
        if not items:
            continue
        header(title)
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


def generate_journal(
    payload: dict[str, Any], settings, previous_journals: list[tuple[str, str]] | None = None
) -> str:
    if not settings.llm_api_key:
        logger.warning("Chave de API não configurada; usando fallback journal")
        return _fallback_journal(payload, settings)

    user_prompt = _build_user_prompt(payload, settings, previous_journals)

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

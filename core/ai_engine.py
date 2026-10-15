"""LLM engine: consolidates raw scraper data into the journal structure using the Gemini SDK."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from google import genai
from google.genai import types

from core.renderer import SECTION_IDS
from core.utils import now_local

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a personal morning briefing editor.

Your task is to transform raw JSON data from multiple sources into a daily journal in Brazilian
Portuguese (pt-BR), returned as JSON. The journal is published as a web page whose layout, section
titles, emojis, date header and formatting are produced by code — you provide only the content.

Rules:
1. OUTPUT — return ONLY a JSON object shaped like this, with no code fences and no commentary:
   {"sections": [{"id": "mundo", "entries": [{"label": "Geopolítica", "text": "...",
     "url": "https://...", "url_label": "Ler"}]}]}
   - "id" must be one of, and the sections must appear in this order: clima, economia,
     investimentos, tecnologia, mundo, local, cultura_pop, games, futebol, historia. Never invent,
     rename, split or reorder an id.
   - "label" is a short bold prefix of 1-3 words, written WITHOUT a trailing colon (the page adds
     it). Omit the field when the entry has no natural subject.
   - "text" is PLAIN TEXT: no HTML tag, no Markdown asterisk, no "•" or "-" bullet marker, no
     literal URL. The page adds every bit of formatting. Emoji that comes from a payload string
     must be copied along with it.
   - "url" and "url_label" are optional and carry the link for that entry, taken from the payload's
     "url" field — never a URL you invented. Use them whenever the payload offers a link:
     "Resgatar" for a giveaway, "Ver oferta" for a paid deal, "Ver repo" for a GitHub repository,
     "Ler" for a news item.
2. Write in pt-BR with a clear, informative tone.
3. SECTION CONTENT — each entry is one fact. Never merge several facts into one entry.
   - clima: one entry per topic, in this order, using ONLY the preformatted strings from the
     payload — copy them verbatim, including any emoji, and never rewrite a number:
       label "Agora": start with "condition_now_emoji" (copy it as-is), then "condition_now",
         "temp_now" and then "(sensação de X)" with "feels_like"
       label "Hoje": "today_summary" — copy verbatim, with its leading emoji. Omit when null.
       label "Máxima e mínima": "temp_max" e "temp_min" (in that order)
       label "Chuva": "rain_summary". Omit the entry when it is null; never speculate about rain.
       label "Sol": "sun_summary"
       label "Vento": "wind" — include only when the value is present
       label "Índice UV": "uv_summary" — include ONLY when uv_label is "alto", "muito alto" or
         "extremo"
     If "vs_ontem" is present, append it to the "Máxima e mínima" entry (it carries its own
     leading 🔺/🔻 emoji — keep it).
   - economia: one entry per asset, in this order, with these labels: "Dólar" for usd_brl, "Euro"
     for eur_brl, "Peso argentino" for ars_brl, "Bitcoin" for btc_brl, "Ibovespa" for ibovespa.
     Each asset carries a preformatted "display" field — copy that string EXACTLY as the "text",
     including the percentage in parentheses. Never reformat a number, never convert currency, and
     never compute a percentage yourself. If an asset has no "display" field, omit that entry. The
     section is these entries and nothing else — no headline, no commentary about the market.
   - investimentos: built from the "investments" payload, which is data about interest rates plus,
     when present, a rotating set of real market tickers — not a portfolio and not personalized
     advice.
       - Open with one entry labelled "Referências", copying the "display" of each indicator in
         "indicators" (e.g. "Selic 14,00% a.a. · CDI 13,90% a.a. · IPCA 4,64% em 12 meses").
       - If "stocks.candidates" is present and non-empty, add ONE entry per candidate, labelled
         with its ticker and name (label "PETR4 (Petrobras)", text "R$ 38,50 (+1,20%) — faixa de
         R$ 30,10 a R$ 42,80 em 12 meses."). Copy each entry's "display" and "range_display"
         EXACTLY as given. You may name ONLY the tickers present in "stocks.candidates" — never any
         other stock, fund, cryptocurrency or issuer. This is factual reporting of price and range,
         not a recommendation: never tell the reader to buy or sell that ticker, and never predict
         where its price is going.
       - Then ONE entry labelled exactly "Insight do dia": one practical insight about personal
         finance in general, written from your own knowledge — budgeting, debt and credit cards,
         emergency reserve, compound interest, inflation, how taxes affect investments, retirement,
         consumption habits, behavioural biases, scams. ONE idea only, well-established and
         something the reader can apply — not news, not a market forecast, and not a comment on
         today's numbers. Prefer the specific and non-obvious (a mechanism, a rule of thumb, a
         common mistake and why it costs money) over platitudes everyone has heard, such as "tenha
         uma reserva de emergência" or "invista com constância". Do not state legal rates, limits or thresholds (tax brackets, FGC ceiling, exempt
         amounts): they change and cannot be checked here, so explain the mechanism instead.
       - "recent_insights", when present, lists the insights already published on previous days.
         Today's insight MUST be about a different topic from every one of them — the same idea
         reworded or seen from another angle counts as a repeat.
       - Apart from obviously illustrative examples in the insight ("quem guarda R$ 100 por
         mês..."), every numeric claim MUST come from "derived", "indicators" or
         "stocks.candidates" — copy the numbers as formatted there. NEVER compute a yield, a
         projection or a percentage yourself, and never state how much money something would return.
       - Outside of the tickers listed in "stocks.candidates", speak about CLASSES of asset only
         (Tesouro Selic, CDB de liquidez diária, Tesouro IPCA+, fundos de índice amplos, reserva em
         dólar). NEVER invent a specific stock, ticker, cryptocurrency, broker, fund or issuer that
         is not in the payload, and never tell the reader to buy or sell. Use "faz sentido
         considerar", not "compre".
   - NEWS SECTIONS (tecnologia, mundo, local, cultura_pop): every news entry gets the subject of
     the story as its "label" (1-3 words) and the fact as its "text" — label "OpenAI", text
     "Lançou o GPT-5.2 com foco em agentes." When an item has no single clear subject, use a short
     thematic label instead ("Geopolítica", "Streaming"). Publish up to
     metadata.items_per_news_section items per news section, ordered by relevance, and link each
     one with its payload "url" ("Ler"). This does NOT apply to the GitHub trending repos.
   - tecnologia: the most relevant tech news first, then up to metadata.max_github_repos GitHub
     trending repositories (label = repository name, text = what it does, url_label "Ver repo").
   - mundo: the most relevant global facts (wars, macroeconomics, historic events). Ignore
     clickbait.
   - local: relevant facts from "local". Prioritise municipal and state politics, the local
     economy, public works, transit changes and events. IGNORE obituaries ("lista de
     falecimentos"), horoscopes, lottery results and routine police blotter. Omit the section
     entirely if nothing qualifies.
   - cultura_pop: prioritise major film/streaming releases, anime, book adaptations and updates on
     competitive scenes or MOBA/tactical game patches. Ignore rumours, minor delays and celebrity
     gossip.
   - games: the "free_games" list comes FIRST and is the point of the section — these are real
     giveaways. At most 4 of them, one entry each, with the title as the label and the platform,
     what it is worth and the "ends_in" string in the text (copy "ends_in" verbatim; never compute
     a deadline yourself and never write "ontem"/"hoje"/"amanhã" from a raw date), linked with
     url_label "Resgatar". An item with "is_new": false has already appeared in a previous journal:
     keep it only when it is ending, and then say so plainly. Then add at most 3 "deals" (paid
     discounts) with url_label "Ver oferta"; those are already filtered for quality, so mention the
     Steam rating when available. Deal prices are in US dollars from international stores: write
     them as "US$ 0,71" and never convert to reais. If "free_games" is empty, still show the deals;
     if both are empty, omit the whole section.
   - futebol: lead with "next_matches" (opponent, competition, day and kick-off time) — that is the
     point of the section — then "last_matches" with the score, then the "news". Those match
     strings are already formatted: copy them, do not reword the dates. The news items carry NO
     date, so never write "ontem", "hoje" or "amanhã" about them.
   - historia: ONE entry with a historical fact for today's calendar day and month (see
     metadata.date), from your own knowledge, in a single sentence. No label needed.
4. OMITTING SECTIONS: omit entirely any section whose data failed (_error) or is empty (no items,
   no deals, no products). Do not write "nenhuma oferta hoje" or similar filler — just leave the
   section out. For partial sections (_warning), include the available data without commenting on
   the failure.
5. LENGTH — the page has no size limit, but it is read at a glance, so density matters:
   - Each news entry is ONE sentence of at most 30 words: the fact stated directly, no filler.
   - The "Insight do dia" entry is at most two sentences.
   - No introductory entry for a section, no closing remark, no commentary about the journal
     itself, no "vale acompanhar" filler.
6. Do not invent facts not present in the input data. The only exceptions are the "historia"
   section, which uses your own historical knowledge, and the "Insight do dia" of investimentos,
   which uses your own knowledge of personal finance.
7. ANTI-REPETITION POLICY:
   - The tag <RECENT_JOURNALS> contains the journals of the previous days, each with its date.
     A story may only reappear if it is genuinely new or has evolved since ALL of them.
   - This policy applies ONLY to these sections: tecnologia, mundo, local, cultura_pop and futebol.
     Do not repeat a story already covered yesterday unless there is a significant development, an
     impactful update, or the continuation of an ongoing event.
   - This policy NEVER applies to clima, economia, investimentos, games or historia. Those are
     expected to look similar every day and must ALWAYS be present regardless of yesterday's
     content. Never omit the weather or the exchange rates because they resemble yesterday's.
     The "Insight do dia" is the one part of them that must not repeat, and its rule is
     "recent_insights", not <RECENT_JOURNALS>.
"""

# O schema é a trava dura do contrato: o prompt explica o conteúdo, isto garante o formato.
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "enum": list(SECTION_IDS)},
                    "entries": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "text": {"type": "string"},
                                "url": {"type": "string"},
                                "url_label": {"type": "string"},
                            },
                            "required": ["text"],
                        },
                    },
                },
                "required": ["id", "entries"],
            },
        }
    },
    "required": ["sections"],
}

# Erros que não melhoram com nova tentativa: modelo inexistente, chave inválida, prompt malformado.
PERMANENT_ERROR_MARKERS = ("400", "401", "403", "404", "INVALID_ARGUMENT", "PERMISSION_DENIED")

DEFAULT_ITEMS_PER_NEWS_SECTION = 5
DEFAULT_MAX_GITHUB_REPOS = 5


def _build_user_prompt(
    payload: dict[str, Any], settings, previous_journals: list[tuple[str, str]] | None = None
) -> str:
    now = now_local()
    meta = {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M"),
        "timezone": "America/Sao_Paulo",
        "city": settings.get("weather", "city", default=""),
        "items_per_news_section": int(
            settings.get(
                "presentation", "items_per_news_section", default=DEFAULT_ITEMS_PER_NEWS_SECTION
            )
        ),
        "max_github_repos": int(
            settings.get("presentation", "max_github_repos", default=DEFAULT_MAX_GITHUB_REPOS)
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


def _entry(text: Any, label: str = "", url: str = "", url_label: str = "") -> dict[str, str] | None:
    body = " ".join(str(text).split()) if text not in (None, "") else ""
    if not body:
        return None
    entry = {"text": body}
    for key, value in (("label", label), ("url", url), ("url_label", url_label)):
        if value:
            entry[key] = value
    return entry


def _fallback_journal(payload: dict[str, Any]) -> dict[str, Any]:
    """Jornal mínimo para quando o LLM não responde: os mesmos dados, sem trabalho editorial.

    Devolve a mesma estrutura do modelo, então a página sai pelo renderer normal — só menor. A
    flag `degraded` faz a página avisar que o gerador de texto falhou.
    """
    sections: list[dict[str, Any]] = []

    def add(section_id: str, entries: list[dict[str, str] | None]) -> None:
        kept = [entry for entry in entries if entry]
        if kept:
            sections.append({"id": section_id, "entries": kept})

    weather = payload.get("weather") or {}
    if not weather.get("_error"):
        temp_max, temp_min = weather.get("temp_max"), weather.get("temp_min")
        add(
            "clima",
            [
                _entry(weather.get("temp_now"), "Agora"),
                _entry(weather.get("today_summary"), "Hoje"),
                _entry(f"{temp_max} / {temp_min}" if temp_max and temp_min else "", "Máxima e mínima"),
                _entry(weather.get("rain_summary"), "Chuva"),
                _entry(weather.get("sun_summary"), "Sol"),
            ],
        )

    finance = payload.get("finance") or {}
    if not finance.get("_error"):
        add(
            "economia",
            [
                _entry((finance.get(key) or {}).get("display"), label)
                for key, label in (
                    ("usd_brl", "Dólar"),
                    ("eur_brl", "Euro"),
                    ("ars_brl", "Peso argentino"),
                    ("btc_brl", "Bitcoin"),
                    ("ibovespa", "Ibovespa"),
                )
            ],
        )

    investments = payload.get("investments") or {}
    if not investments.get("_error"):
        indicators = investments.get("indicators") or {}
        add(
            "investimentos",
            [
                _entry(entry.get("display"), entry.get("label") or "")
                for entry in indicators.values()
                if isinstance(entry, dict)
            ],
        )

    for key, section_id in (
        ("tech_news", "tecnologia"),
        ("world_news", "mundo"),
        ("local", "local"),
        ("pop_culture", "cultura_pop"),
    ):
        section = payload.get(key) or {}
        items = section.get("items") or []
        add(
            section_id,
            [
                _entry(item.get("title"), url=item.get("url") or "", url_label="Ler")
                for item in items[:5]
                if isinstance(item, dict)
            ],
        )

    return {"sections": sections, "degraded": True}


def _coerce_journal(raw: Any) -> dict[str, Any]:
    """Normaliza a resposta: id desconhecido, entrada sem texto e campo faltando são descartados.

    O schema já restringe o formato, mas uma resposta fora dele deve dar página menor, nunca
    exceção no meio do pipeline. Resposta sem nada aproveitável levanta erro de propósito: aí vale
    gastar uma nova tentativa.
    """
    sections = raw.get("sections") if isinstance(raw, dict) else None
    if not isinstance(sections, list):
        raise ValueError("Resposta sem a lista 'sections'")

    cleaned: list[dict[str, Any]] = []
    for section in sections:
        if not isinstance(section, dict) or section.get("id") not in SECTION_IDS:
            continue

        entries: list[dict[str, str]] = []
        for candidate in section.get("entries") or []:
            if not isinstance(candidate, dict):
                continue
            entry = _entry(
                candidate.get("text"),
                label=str(candidate.get("label") or "").strip(),
                url=str(candidate.get("url") or "").strip(),
                url_label=str(candidate.get("url_label") or "").strip(),
            )
            if entry:
                entries.append(entry)

        if entries:
            cleaned.append({"id": section["id"], "entries": entries})

    if not cleaned:
        raise ValueError("Resposta sem nenhuma seção aproveitável")

    return {"sections": cleaned}


def _is_permanent(error: Exception) -> bool:
    message = str(error)
    return any(marker in message for marker in PERMANENT_ERROR_MARKERS)


def _generate_once(model: str, user_prompt: str, settings) -> dict[str, Any]:
    client = genai.Client(api_key=settings.llm_api_key)
    response = client.models.generate_content(
        model=model,
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=settings.llm_temperature,
            response_mime_type="application/json",
            response_schema=RESPONSE_SCHEMA,
        ),
    )
    content = (response.text or "").strip()
    if not content:
        raise ValueError("Resposta vazia do Gemini")
    return _coerce_journal(json.loads(content))


def generate_journal(
    payload: dict[str, Any], settings, previous_journals: list[tuple[str, str]] | None = None
) -> dict[str, Any]:
    if not settings.llm_api_key:
        logger.warning("Chave de API não configurada; usando fallback journal")
        return _fallback_journal(payload)

    user_prompt = _build_user_prompt(payload, settings, previous_journals)

    # Um 503 do Gemini é sobrecarga do modelo e costuma durar minutos, não segundos: o intervalo
    # fixo de 10s gastava as três tentativas em menos de um minuto e caía no fallback.
    delays = [10, 30, 90]
    models = [settings.llm_model, *settings.llm_fallback_models]

    for model in models:
        for attempt, delay in enumerate(delays, start=1):
            try:
                journal = _generate_once(model, user_prompt, settings)
                if model != settings.llm_model:
                    logger.warning("Jornal gerado pelo modelo de reserva %s", model)
                return journal
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

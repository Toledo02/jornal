"""Histórico diário do jornal.

Guarda três tipos de informação com finalidades distintas:

* o **texto** dos jornais recentes, para a regra anti-repetição — antes só existia o do dia
  anterior, então uma notícia podia voltar a cada dois dias sem ser detectada;
* as **métricas** numéricas (cotações, temperaturas), para o contexto comparativo: dizer
  "maior valor em 12 dias" é cálculo em Python sobre série histórica, não resumo de manchete;
* os **destaques** já publicados (títulos de jogos e ofertas), porque a regra anti-repetição do
  prompt só alcança o que o modelo consegue comparar lendo texto — e ele não consegue: os
  mesmos jogos voltavam todo dia com outra redação. Título repetido é decisão de conjunto, e
  conjunto se resolve em Python.

Retenção fixa em dias para o arquivo não crescer indefinidamente.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from core.utils import format_number_pt_br, now_local

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 30
DEFAULT_JOURNALS_IN_PROMPT = 3

# Janela padrão para considerar um destaque "já mostrado".
DEFAULT_REPEAT_WINDOW_DAYS = 7


def load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Histórico ilegível (%s); começando vazio", exc)
        return {}


def save(path: Path, history: dict[str, Any], retention_days: int = DEFAULT_RETENTION_DAYS) -> None:
    pruned = prune(history, retention_days)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pruned, ensure_ascii=False, indent=2), encoding="utf-8")


def prune(history: dict[str, Any], retention_days: int = DEFAULT_RETENTION_DAYS) -> dict[str, Any]:
    """Descarta dias além da janela de retenção, e chaves que não sejam datas.

    O "hoje" é sempre o do fuso do jornal, nunca o do servidor: `record` grava a data com
    `now_local()`, e numa VPS em UTC comparar com `date.today()` faria a janela deslizar meio dia.
    """
    cutoff = now_local().date() - timedelta(days=retention_days)
    kept: dict[str, Any] = {}
    for key, value in history.items():
        try:
            if datetime.strptime(key, "%Y-%m-%d").date() >= cutoff:
                kept[key] = value
        except ValueError:
            continue
    return dict(sorted(kept.items()))


def extract_metrics(payload: dict[str, Any]) -> dict[str, float]:
    """Números do dia que valem comparar ao longo do tempo."""
    metrics: dict[str, float] = {}

    finance = payload.get("finance") or {}
    for key in ("usd_brl", "eur_brl", "ars_brl", "btc_brl"):
        value = (finance.get(key) or {}).get("bid")
        if value is not None:
            try:
                metrics[key] = float(value)
            except (TypeError, ValueError):
                pass

    points = (finance.get("ibovespa") or {}).get("points")
    if points is not None:
        try:
            metrics["ibovespa"] = float(points)
        except (TypeError, ValueError):
            pass

    weather = payload.get("weather") or {}
    for source_key, metric_key in (("temp_max_c", "temp_max"), ("temp_min_c", "temp_min")):
        value = weather.get(source_key)
        if value is not None:
            try:
                metrics[metric_key] = float(value)
            except (TypeError, ValueError):
                pass

    return metrics


# Tokens que começam frase ou ligam oração: aparecem em maiúscula sem identificar assunto nenhum.
# Inclui inglês porque metade dos feeds é em inglês e vários usam Title Case nas manchetes.
_NOT_ENTITIES = {
    "a", "o", "as", "os", "um", "uma", "de", "do", "da", "dos", "das", "em", "no", "na", "nos",
    "nas", "por", "para", "com", "sem", "sobre", "apos", "ate", "entre", "contra", "durante",
    "que", "quem", "como", "quando", "onde", "mais", "menos", "novo", "nova", "novos", "novas",
    "veja", "confira", "saiba", "entenda", "the", "and", "for", "with", "from", "this", "that",
    "will", "new", "how", "why", "what", "who", "after", "before", "into", "over", "his", "her",
    "its", "their", "you", "your", "are", "was", "were", "has", "have", "had", "not", "但",
}

_ENTITY_RE = re.compile(r"\b[A-ZÀ-ÖØ-Þ][\wÀ-ÿ'’-]{2,}\b")
_WORD_RE = re.compile(r"[\wÀ-ÿ'’-]+")


def _fold(text: str) -> str:
    """Minúsculas e sem acento, para comparar título de feed com texto de jornal."""
    folded = unicodedata.normalize("NFKD", (text or "").lower())
    return "".join(char for char in folded if not unicodedata.combining(char))


def _entities(title: str) -> set[str]:
    """Nomes próprios do título — o que sobrevive à tradução e à reescrita do modelo.

    Comparar palavra a palavra não funciona: o feed diz "Amazon to build gas plant in Texas" e o
    jornal saiu "A Amazon investirá numa usina a gás no Texas". Substantivo comum não sobrevive à
    passagem pelo inglês; nome próprio sobrevive.

    O genitivo é aparado porque "Messi's" e "Messi" precisam bater — a BBC escreve um, o jornal
    escreve o outro.
    """
    entities: set[str] = set()
    for token in _ENTITY_RE.findall(title or ""):
        folded = re.sub(r"['’]s$", "", _fold(token)).strip("'’-")
        if len(folded) >= 3 and folded not in _NOT_ENTITIES:
            entities.add(folded)
    return entities


def _terms(text: str) -> set[str]:
    return set(_WORD_RE.findall(_fold(text)))


# Seções filtradas por cobertura, com o campo da lista e o texto que identifica o item.
# Promoções entram aqui pelo mesmo motivo das notícias, e não pelo rodízio dos jogos: o payload
# traz 10 ofertas e o modelo publica 4, então registrar o que foi *oferecido* apagaria 6 que
# nunca saíram.
COVERAGE_SECTIONS: dict[str, tuple[str, str, int]] = {
    # seção: (campo da lista, campo do texto, mínimo a preservar)
    "tech_news": ("items", "title", 3),
    "world_news": ("items", "title", 3),
    "pop_culture": ("items", "title", 3),
    "promotions": ("offers", "text", 4),
}

# Só o começo da oferta: o texto do canal tem 400 caracteres de emoji, hashtag e frete, e o
# produto — que é o que identifica a promoção — vem sempre na frente.
_OFFER_TEXT_LIMIT = 110


def filter_published_items(
    payload: dict[str, Any],
    history: dict[str, Any],
    days: int = DEFAULT_JOURNALS_IN_PROMPT,
    min_entities: int = 2,
    min_ratio: float = 0.6,
    sections: dict[str, tuple[str, str, int]] | None = None,
) -> None:
    """Tira das seções de notícia e de promoções o que os jornais recentes já contaram.

    Existe pela mesma razão do rodízio de jogos: a regra 12 do prompt pede ao modelo que compare
    a matéria de hoje com três jornais em prosa, e ele compara mal. A diferença é que aqui não dá
    para guardar "o que foi publicado" — o payload traz 15 candidatos por seção e o modelo escolhe
    3, então registrar os títulos oferecidos suprimiria 12 matérias que nunca saíram. Por isso a
    comparação é contra o **texto** dos jornais: se todos os nomes próprios de um título já
    apareceram num jornal recente, aquela matéria já foi contada.

    A correspondência é por proporção (`min_ratio`), não exata. Exigir que *todos* os nomes batam
    torna o filtro inerte na prática: o título traz "EUA" onde o jornal escreveu "Estados Unidos",
    e verbo em início de frase entra em maiúscula como se fosse nome ("Morre pai de Messi"). Com
    3 de 4 nomes coincidindo já é a mesma matéria.

    Três proteções contra falso positivo, que aqui custa caro (apagar notícia legítima):

    * nomes próprios presentes em **todos** os jornais recentes são pano de fundo — "Brasil",
      "Athletico", "Selic" saem toda manhã e não identificam matéria alguma;
    * pelo menos `min_entities` nomes precisam coincidir de fato, não só a proporção: com um nome
      só, "Bolsonaro" bastaria para apagar qualquer matéria sobre ele pelo resto da semana;
    * a seção nunca fica abaixo do mínimo definido em `COVERAGE_SECTIONS`. Ficar sem a seção
      Mundo é pior que repetir.
    """
    # Limitado por idade, não só por quantidade: `recent_journals` devolve os N mais recentes sem
    # olhar a data, então depois de uma interrupção do cron o jornal de um mês atrás entraria como
    # se fosse o de ontem e suprimiria notícia de hoje.
    cutoff = (now_local().date() - timedelta(days=days)).isoformat()
    journals = [
        _terms(text) for day, text in recent_journals(history, days) if text and day >= cutoff
    ]
    if not journals:
        return

    background = set.intersection(*journals) if len(journals) > 1 else set()

    for section, (list_field, text_field, min_keep) in (sections or COVERAGE_SECTIONS).items():
        data = payload.get(section)
        if not isinstance(data, dict) or not data.get(list_field):
            continue

        original = data[list_field]
        kept: list[dict[str, Any]] = []
        dropped: list[dict[str, Any]] = []

        for item in original:
            text = str(item.get(text_field, ""))[:_OFFER_TEXT_LIMIT]
            marks = _entities(text) - background
            best = max((len(marks & journal) for journal in journals), default=0)
            if (
                len(marks) >= min_entities
                and best >= min_entities
                and best / len(marks) >= min_ratio
            ):
                dropped.append(item)
            else:
                kept.append(item)

        if not dropped:
            continue

        if len(kept) < min_keep:
            # Recompõe na ordem original: o round-robin já equilibrou as fontes.
            restored = dropped[: min_keep - len(kept)]
            kept = [item for item in original if item in kept or item in restored]
            dropped = [item for item in dropped if item not in restored]

        logger.info(
            "%s: %s itens já cobertos nos últimos %s jornais (%s)",
            section,
            len(dropped),
            len(journals),
            "; ".join(str(item.get(text_field, ""))[:50] for item in dropped[:3]),
        )
        data[list_field] = kept
        if "count" in data:
            data["count"] = len(kept)


def _highlight_key(title: str) -> str:
    """Chave estável para comparar títulos entre dias.

    "Sid Meiers Civilization VI" e "Sid Meier's Civilization VI" são o mesmo jogo, e a fonte
    alterna entre as duas grafias.
    """
    return re.sub(r"[^a-z0-9]", "", (title or "").lower())


def extract_highlights(payload: dict[str, Any]) -> dict[str, list[str]]:
    """Títulos publicados hoje, para não repetir amanhã."""
    gaming = payload.get("gaming") or {}
    highlights: dict[str, list[str]] = {}
    for key in ("free_games", "deals"):
        titles = [
            _highlight_key(item.get("title", ""))
            for item in (gaming.get(key) or [])
            if isinstance(item, dict) and item.get("title")
        ]
        if titles:
            highlights[key] = titles
    return highlights


def _highlight_counts(history: dict[str, Any], key: str, window_days: int) -> Counter[str]:
    """Em quantos dos últimos `window_days` cada título já apareceu."""
    cutoff = (now_local().date() - timedelta(days=window_days)).isoformat()
    counts: Counter[str] = Counter()
    for day, entry in history.items():
        if day < cutoff or not isinstance(entry, dict):
            continue
        for title in (entry.get("highlights") or {}).get(key) or []:
            counts[title] += 1
    return counts


def apply_repeat_policy(
    payload: dict[str, Any],
    history: dict[str, Any],
    window_days: int = DEFAULT_REPEAT_WINDOW_DAYS,
    ends_soon_days: int = 2,
    min_free_games: int = 2,
) -> None:
    """Tira de cena os jogos que já saíram nos últimos dias.

    Regras diferentes para os dois blocos, porque a repetição significa coisas diferentes:

    * **Ofertas pagas** repetidas são descarte puro. O ranking do CheapShark é quase estático, e
      uma oferta que você já viu ontem e não comprou não melhora por aparecer de novo. Por isso o
      scraper devolve um lote maior que o exibido: aqui o que já saiu é removido e o próximo da
      fila sobe, em vez de a seção simplesmente encolher.
    * **Giveaways** repetidos ficam, mas só quando estão acabando (`ends_in`) ou quando não há
      novidade suficiente para preencher a seção. Um jogo grátis que expira amanhã é a informação
      mais útil do bloco justamente no dia em que já foi mostrado antes.
    """
    gaming = payload.get("gaming")
    if not isinstance(gaming, dict):
        return

    deals = gaming.get("deals") or []
    limit = int(gaming.pop("deals_display_limit", len(deals)) or len(deals))
    if deals:
        seen = _highlight_counts(history, "deals", window_days)
        fresh = [deal for deal in deals if _highlight_key(deal.get("title", "")) not in seen]
        dropped = len(deals) - len(fresh)
        gaming["deals"] = fresh[:limit]
        if dropped:
            logger.info("%s ofertas pagas já publicadas nos últimos %s dias", dropped, window_days)

    free_games = gaming.get("free_games") or []
    if free_games:
        seen = _highlight_counts(history, "free_games", window_days)
        for item in free_games:
            shown = seen.get(_highlight_key(item.get("title", "")), 0)
            item["days_shown"] = shown
            item["is_new"] = shown == 0

        new_items = [item for item in free_games if item["is_new"]]
        repeats = [
            item
            for item in free_games
            if not item["is_new"]
            and item.get("days_left") is not None
            and item["days_left"] <= ends_soon_days
        ]
        if len(new_items) < min_free_games:
            # Sem novidade, um repetido ainda vale mais que a seção vazia.
            extras = [item for item in free_games if not item["is_new"] and item not in repeats]
            repeats.extend(extras[: min_free_games - len(new_items)])

        kept = [item for item in free_games if item in new_items or item in repeats]
        if len(kept) != len(free_games):
            logger.info("%s giveaways repetidos omitidos", len(free_games) - len(kept))
        gaming["free_games"] = kept


def record(
    history: dict[str, Any],
    day: date,
    journal_text: str,
    metrics: dict[str, float],
    highlights: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    history = dict(history)
    entry: dict[str, Any] = {"journal": journal_text, "metrics": metrics}
    if highlights:
        entry["highlights"] = highlights
    history[day.isoformat()] = entry
    return history


def recent_journals(history: dict[str, Any], days: int = DEFAULT_JOURNALS_IN_PROMPT) -> list[tuple[str, str]]:
    """(data, texto) dos jornais mais recentes, do mais novo para o mais antigo."""
    entries = [
        (day, entry.get("journal", ""))
        for day, entry in sorted(history.items(), reverse=True)
        if isinstance(entry, dict) and entry.get("journal")
    ]
    return entries[:days]


def _series(history: dict[str, Any], metric: str) -> list[tuple[str, float]]:
    series: list[tuple[str, float]] = []
    for day, entry in sorted(history.items()):
        value = (entry or {}).get("metrics", {}).get(metric)
        if isinstance(value, (int, float)):
            series.append((day, float(value)))
    return series


def describe_metric(history: dict[str, Any], metric: str, current: float) -> str | None:
    """Frase curta de contexto, ou None quando não há histórico suficiente.

    Exemplos: "maior valor em 12 dias", "menor valor do mês".
    """
    values = [value for _, value in _series(history, metric)]
    # Com poucos dias, "maior valor em 3 dias" é ruído estatístico, não informação.
    if len(values) < 5:
        return None

    if current > max(values):
        return f"maior valor em {len(values)} dias"
    if current < min(values):
        return f"menor valor em {len(values)} dias"
    return None


def enrich_payload(payload: dict[str, Any], history: dict[str, Any]) -> None:
    """Injeta o contexto comparativo nos campos que o prompt já manda copiar."""
    finance = payload.get("finance") or {}
    for metric in ("usd_brl", "eur_brl", "btc_brl", "ibovespa"):
        quote = finance.get(metric)
        if not isinstance(quote, dict) or not quote.get("display"):
            continue
        current = quote.get("bid") if metric != "ibovespa" else quote.get("points")
        if current is None:
            continue
        try:
            note = describe_metric(history, metric, float(current))
        except (TypeError, ValueError):
            continue
        if note:
            quote["display"] = f"{quote['display']} — {note}"

    weather = payload.get("weather") or {}
    temp_max = weather.get("temp_max_c")
    if temp_max is not None:
        series = _series(history, "temp_max")
        if series:
            previous = series[-1][1]
            delta = float(temp_max) - previous
            if abs(delta) >= 3:
                direction = "acima" if delta > 0 else "abaixo"
                weather["vs_ontem"] = (
                    f"{format_number_pt_br(abs(delta), 1)}°C {direction} da máxima de ontem"
                )

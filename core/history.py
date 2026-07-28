"""Histórico diário do jornal.

Guarda dois tipos de informação com finalidades distintas:

* o **texto** dos jornais recentes, para a regra anti-repetição — antes só existia o do dia
  anterior, então uma notícia podia voltar a cada dois dias sem ser detectada;
* as **métricas** numéricas (cotações, temperaturas), para o contexto comparativo: dizer
  "maior valor em 12 dias" é cálculo em Python sobre série histórica, não resumo de manchete.

Retenção fixa em dias para o arquivo não crescer indefinidamente.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from core.utils import format_number_pt_br

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 30
DEFAULT_JOURNALS_IN_PROMPT = 3


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
    """Descarta dias além da janela de retenção, e chaves que não sejam datas."""
    cutoff = date.today() - timedelta(days=retention_days)
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


def record(
    history: dict[str, Any],
    day: date,
    journal_text: str,
    metrics: dict[str, float],
) -> dict[str, Any]:
    history = dict(history)
    history[day.isoformat()] = {"journal": journal_text, "metrics": metrics}
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

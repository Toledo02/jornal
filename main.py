"""Daily journal orchestrator."""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
from typing import Awaitable, Callable

from config.settings import load_settings
from core.ai_engine import generate_journal
from core.telegram_sender import inspect_message, send_alert, send_journal
from core.utils import ScraperResult, setup_logging, strip_html
from scrapers import (
    finance,
    football,
    gaming,
    github_trending,
    news_rss,
    promotions,
    weather,
)

ScraperFn = Callable[..., Awaitable[ScraperResult]]

SCRAPERS: list[tuple[str, ScraperFn]] = [
    ("weather", weather.fetch),
    ("finance", finance.fetch),
    ("tech_news", lambda s: news_rss.fetch(s, category="tech")),
    ("world_news", lambda s: news_rss.fetch(s, category="world")),
    ("pop_culture", lambda s: news_rss.fetch(s, category="pop_culture")),
    ("github_trending", github_trending.fetch),
    ("gaming", gaming.fetch),
    ("football", football.fetch),
    ("promotions", promotions.fetch),
]

LAST_JOURNAL_FILE = "last_journal.txt"


async def _run_scraper(name: str, fn: ScraperFn, settings) -> ScraperResult:
    logger = logging.getLogger("journal")
    try:
        result = await fn(settings)
        logger.info("Scraper %s finished with status=%s", name, result.status)
        return result
    except Exception as exc:
        logger.warning("Scraper %s raised exception: %s", name, exc)
        return ScraperResult(section=name, status="error", error=str(exc))


async def _collect_data(settings, only: list[str] | None = None) -> dict[str, ScraperResult]:
    logger = logging.getLogger("journal")
    timeout = int(settings.orchestrator.get("scraper_timeout_seconds", 30))

    selected = [(name, fn) for name, fn in SCRAPERS if not only or name in only]
    if only:
        unknown = set(only) - {name for name, _ in SCRAPERS}
        if unknown:
            logger.warning("Scrapers desconhecidos ignorados: %s", ", ".join(sorted(unknown)))

    tasks = {
        name: asyncio.create_task(_run_scraper(name, fn, settings), name=name)
        for name, fn in selected
    }

    results: dict[str, ScraperResult] = {}
    for name, task in tasks.items():
        try:
            results[name] = await asyncio.wait_for(task, timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("Scraper %s timed out after %ss", name, timeout)
            task.cancel()
            results[name] = ScraperResult(
                section=name, status="error", error=f"Timeout after {timeout}s"
            )
        except Exception as exc:
            logger.warning("Scraper %s future failed: %s", name, exc)
            results[name] = ScraperResult(section=name, status="error", error=str(exc))

    return results


def _build_payload(results: dict[str, ScraperResult]) -> dict:
    return {name: result.to_payload() for name, result in results.items()}


def _clean_error(text: str | None, limit: int = 200) -> str:
    """Colapsa o erro em uma linha e corta: mensagens de httpx trazem URLs e quebras de linha."""
    collapsed = " ".join((text or "erro não informado").split())
    return collapsed if len(collapsed) <= limit else f"{collapsed[:limit]}…"


def _collect_issues(results: dict[str, ScraperResult]) -> list[tuple[str, str, str]]:
    """Retorna (status, seção, erro) para cada fonte que não terminou 100% ok."""
    return [
        (result.status, name, _clean_error(result.error))
        for name, result in results.items()
        if result.status in {"error", "partial"}
    ]


def _format_issues(issues: list[tuple[str, str, str]]) -> str:
    """Formata os problemas para o Telegram. O emoji fica só aqui: o console do Windows
    usa cp1252 e quebraria ao escrever emoji no StreamHandler do log."""
    marks = {"error": "❌", "partial": "⚠️"}
    return "\n".join(f"{marks[status]} {name}: {error}" for status, name, error in issues)


def _successful_sections(results: dict[str, ScraperResult]) -> int:
    return sum(1 for result in results.values() if result.status in {"ok", "partial"})


def _notify(text: str, settings) -> None:
    """Avisa pelo Telegram sem nunca derrubar o pipeline por causa do aviso."""
    logger = logging.getLogger("journal")
    try:
        send_alert(text, settings)
        logger.info("Alert sent to Telegram")
    except Exception as exc:
        logger.error("Failed to send alert: %s", exc)


async def _run_pipeline(args: argparse.Namespace) -> int:
    settings = load_settings()
    logger = setup_logging(settings)
    logger.info("Starting daily journal pipeline%s", " [DRY RUN]" if args.dry_run else "")

    results = await _collect_data(settings, args.only)
    ok_count = _successful_sections(results)
    min_sections = int(settings.orchestrator.get("min_sections_for_send", 1))

    issues = _collect_issues(results)
    for status, name, error in issues:
        logger.warning("Section %s (%s): %s", name, status, error)

    if not args.only and ok_count < min_sections:
        logger.error("Not enough successful sections (%s/%s). Aborting.", ok_count, min_sections)
        if not args.dry_run:
            _notify(
                "🔥 Jornal Matinal NÃO foi gerado\n\n"
                f"Apenas {ok_count} de {min_sections} seções necessárias tiveram sucesso.\n\n"
                + _format_issues(issues),
                settings,
            )
        return 1

    payload = _build_payload(results)

    if args.no_llm:
        print(_dump_payload(payload))
        return 0

    last_journal_file = settings.project_root / "logs" / LAST_JOURNAL_FILE
    previous_journal_text = ""
    if last_journal_file.exists():
        try:
            previous_journal_text = last_journal_file.read_text(encoding="utf-8")
            logger.info("Previous journal context loaded successfully")
        except Exception as exc:
            logger.warning("Failed to read previous journal file: %s", exc)

    journal_text = generate_journal(payload, settings, previous_journal_text)
    logger.info("Journal generated (%s chars)", len(journal_text))

    if args.dry_run:
        _preview(journal_text, issues)
        return 0

    try:
        send_journal(journal_text, settings)
        logger.info("Journal sent to Telegram successfully")
        try:
            last_journal_file.write_text(journal_text, encoding="utf-8")
            logger.info("Current journal saved to last_journal.txt")
        except Exception as exc:
            logger.warning("Failed to save current journal: %s", exc)
    except Exception as exc:
        logger.error("Failed to send journal: %s", exc)
        _notify(
            f"🔥 Jornal Matinal não pôde ser enviado\n\n{_clean_error(str(exc), 500)}",
            settings,
        )
        return 1

    if issues:
        _notify(
            "🩺 O jornal foi enviado, mas há fontes com falha:\n\n" + _format_issues(issues),
            settings,
        )

    return 0


def _preview(journal_text: str, issues: list[tuple[str, str, str]]) -> None:
    """Mostra o jornal como ele será lido, mais o que só se vê no formato de transporte."""
    report = inspect_message(journal_text)
    sizes = report["chunk_sizes"]

    print()
    print("=" * 72)
    # Renderizado, não o HTML cru: "&amp;" chega ao Telegram como "&" e assustaria à toa.
    print(strip_html(report["sanitized"], keep_newlines=True))
    print("=" * 72)
    print()
    print(f"  {sum(sizes)} caracteres, {len(sizes)} mensagem(ns) {sizes}, {report['links']} links")
    print(f"  formatação: {'; '.join(report['problems']) if report['problems'] else 'ok'}")

    if issues:
        print()
        print("  Alerta que seria enviado:")
        for line in _format_issues(issues).splitlines():
            print(f"    {line}")


def _dump_payload(payload: dict) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, indent=2)


def _use_utf8_stdout() -> None:
    """O console do Windows usa cp1252 e estoura ao imprimir acentos e emoji.

    Só afeta os modos de inspeção (--dry-run / --no-llm); o envio ao Telegram sempre foi UTF-8.
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gera e envia o jornal matinal.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Gera o jornal e imprime no terminal, sem enviar ao Telegram.",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Só coleta os dados e imprime o payload, sem chamar o modelo. Não gasta requisição.",
    )
    parser.add_argument(
        "--only",
        type=lambda value: [name.strip() for name in value.split(",") if name.strip()],
        help=f"Roda apenas os scrapers indicados. Opções: {', '.join(n for n, _ in SCRAPERS)}",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.dry_run or args.no_llm:
        _use_utf8_stdout()
    try:
        return asyncio.run(_run_pipeline(args))
    except Exception as exc:
        logging.getLogger("journal").exception("Pipeline crashed")
        if not args.dry_run and not args.no_llm:
            try:
                _notify(
                    f"🔥 Jornal Matinal quebrou\n\n{_clean_error(str(exc), 500)}",
                    load_settings(),
                )
            except Exception:
                logging.getLogger("journal").error("Could not notify about the crash")
        return 1


if __name__ == "__main__":
    sys.exit(main())

"""Renderização do jornal: a estrutura devolvida pelo LLM vira página HTML e texto de histórico."""

from __future__ import annotations

import html
import os
from pathlib import Path
from typing import Any

# Ordem, emoji e título das seções. Esta é a camada de apresentação: o modelo devolve só o id e as
# entradas, então o mesmo id dá sempre o mesmo cabeçalho — antes isso vivia no prompt e variava.
SECTIONS: tuple[tuple[str, str, str], ...] = (
    ("clima", "🌦️", "Clima"),
    ("economia", "💵", "Economia"),
    ("investimentos", "📈", "Ideias de investimento"),
    ("tecnologia", "💻", "Tecnologia & Dev"),
    ("mundo", "🌍", "Mundo"),
    ("local", "📍", "Curitiba & Paraná"),
    ("cultura_pop", "🎬", "Cultura pop"),
    ("games", "🎮", "Ofertas & games grátis"),
    ("futebol", "⚽", "Futebol"),
    ("historia", "📚", "Neste dia na história"),
)

SECTION_IDS: tuple[str, ...] = tuple(section_id for section_id, _, _ in SECTIONS)

STYLE = """
:root {
  color-scheme: light dark;
  --bg: #f5f6f8; --card: #fff; --ink: #1a1d21; --muted: #5d646d;
  --line: #e4e7ec; --accent: #2160cc;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #13161a; --card: #1c2025; --ink: #e7eaee; --muted: #98a1ab;
    --line: #2b3137; --accent: #7fa9f6;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 0 1rem 2.5rem; background: var(--bg); color: var(--ink);
  font: 17px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
  -webkit-text-size-adjust: 100%;
}
.wrap { max-width: 44rem; margin: 0 auto; }
header { padding: 1.75rem 0 0; }
h1 { margin: 0; font-size: 1.45rem; line-height: 1.25; letter-spacing: -0.01em; }
.sub { color: var(--muted); font-size: 0.82rem; margin-top: 0.4rem; }
.warn {
  margin-top: 1rem; padding: 0.7rem 0.9rem; border-radius: 10px; font-size: 0.85rem;
  background: var(--card); border: 1px solid var(--line); color: var(--muted);
}
nav { display: flex; flex-wrap: wrap; gap: 0.4rem; margin: 1.15rem 0 1.4rem; }
nav a {
  text-decoration: none; color: var(--muted); background: var(--card);
  border: 1px solid var(--line); border-radius: 999px;
  padding: 0.3rem 0.72rem; font-size: 0.8rem; white-space: nowrap;
}
section {
  background: var(--card); border: 1px solid var(--line); border-radius: 14px;
  padding: 1.05rem 1.1rem; margin-bottom: 0.9rem; scroll-margin-top: 0.8rem;
}
h2 {
  display: flex; align-items: center; gap: 0.45rem; margin: 0 0 0.8rem;
  font-size: 0.86rem; letter-spacing: 0.06em; text-transform: uppercase; color: var(--muted);
}
h2 .emoji { font-size: 1.1rem; letter-spacing: 0; }
ul { list-style: none; margin: 0; padding: 0; }
li + li { margin-top: 0.72rem; padding-top: 0.72rem; border-top: 1px solid var(--line); }
.label { font-weight: 600; }
a.ref { color: var(--accent); text-decoration: none; font-size: 0.82rem; white-space: nowrap; }
footer { color: var(--muted); font-size: 0.76rem; text-align: center; padding-top: 0.9rem; }
"""


def _text(value: Any) -> str:
    return " ".join(str(value).split()) if value not in (None, "") else ""


def _safe_url(value: Any) -> str:
    """Só http(s) atravessa: `url` vem de feed de terceiros e vira href na página."""
    candidate = _text(value)
    return candidate if candidate.lower().startswith(("http://", "https://")) else ""


def _entries(journal: dict[str, Any], section_id: str) -> list[dict[str, Any]]:
    for section in journal.get("sections") or []:
        if isinstance(section, dict) and section.get("id") == section_id:
            return [entry for entry in (section.get("entries") or []) if isinstance(entry, dict)]
    return []


def _render_entry(entry: dict[str, Any]) -> str:
    label = _text(entry.get("label"))
    body = _text(entry.get("text"))
    if not body:
        return ""

    parts = []
    if label:
        parts.append(f'<span class="label">{html.escape(label)}:</span> ')
    parts.append(html.escape(body))

    url = _safe_url(entry.get("url"))
    if url:
        link_text = _text(entry.get("url_label")) or "Abrir"
        parts.append(f' <a class="ref" href="{html.escape(url, quote=True)}">{html.escape(link_text)} &rarr;</a>')

    return f"<li>{''.join(parts)}</li>"


def render_html(
    journal: dict[str, Any],
    *,
    date_header: str,
    generated_at: str,
    city: str = "",
) -> str:
    """Página única e autocontida — sem asset externo, para a publicação ser um arquivo só."""
    filled = [
        (section_id, emoji, title, rendered)
        for section_id, emoji, title in SECTIONS
        if (rendered := [item for item in (_render_entry(e) for e in _entries(journal, section_id)) if item])
    ]

    nav = "".join(
        f'<a href="#{section_id}">{emoji} {html.escape(title)}</a>'
        for section_id, emoji, title, _ in filled
    )
    body = "".join(
        f'<section id="{section_id}">'
        f'<h2><span class="emoji">{emoji}</span>{html.escape(title)}</h2>'
        f"<ul>{''.join(items)}</ul>"
        "</section>"
        for section_id, emoji, title, items in filled
    )

    subtitle = " · ".join(part for part in (html.escape(city), f"atualizado às {html.escape(generated_at)}") if part)
    warning = (
        '<div class="warn">Modo fallback — o gerador de texto falhou e a página saiu direto dos '
        "dados coletados.</div>"
        if journal.get("degraded")
        else ""
    )

    return f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Jornal — {html.escape(date_header)}</title>
<style>{STYLE}</style>
</head>
<body>
<div class="wrap">
<header>
<h1>{html.escape(date_header)}</h1>
<div class="sub">{subtitle}</div>
{warning}
</header>
<nav>{nav}</nav>
{body}
<footer>Jornal Matinal · gerado automaticamente</footer>
</div>
</body>
</html>
"""


def render_text(journal: dict[str, Any], *, date_header: str = "") -> str:
    """Projeção em texto puro — é o que vai para o `history.json`.

    O histórico alimenta a anti-repetição (`filter_published_items` compara nomes próprios contra o
    texto dos jornais recentes) e o `<RECENT_JOURNALS>` do prompt. Guardar o JSON cru quebraria as
    duas coisas, e guardar HTML encheria a comparação de nome de tag.
    """
    lines: list[str] = [date_header] if date_header else []

    for section_id, _, title in SECTIONS:
        entries = [
            f"• {label}: {body}" if (label := _text(entry.get("label"))) else f"• {body}"
            for entry in _entries(journal, section_id)
            if (body := _text(entry.get("text")))
        ]
        if entries:
            lines.extend(["", title.upper(), *entries])

    return "\n".join(lines).strip()


def write_page(markup: str, path: Path) -> Path:
    """Escreve a página de forma atômica: meia página servida ao celular é pior que a de ontem."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.tmp")
    temp.write_text(markup, encoding="utf-8")
    os.replace(temp, path)
    return path

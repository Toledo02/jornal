"""Renderização do jornal: a estrutura devolvida pelo LLM vira página HTML e texto de histórico."""

from __future__ import annotations

import html
import os
import re
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

# SVG no lugar de emoji: a página é HTML de verdade, não precisa depender da fonte de emoji do
# aparelho (que varia de cor e traço entre Android/iOS/Windows). Nuvem/sol/seta são preenchidos
# (leitura melhor em tamanho pequeno); os ícones de conceito (dólar, globo, bola...) são de traço.
_CLOUD = (
    '<circle cx="9" cy="14.2" r="3.3"/><circle cx="13.1" cy="11" r="4.2"/>'
    '<circle cx="17.2" cy="14.2" r="3"/><rect x="7" y="14.2" width="13" height="5.3" rx="2.65"/>'
)
_SUN_RAYS = "".join(
    f'<rect x="11.1" y="1.2" width="1.8" height="4.2" rx="0.9" transform="rotate({a} 12 12)"/>'
    for a in range(0, 360, 45)
)

# glifo -> (conteúdo interno do <svg>, preenchido?)
_ICON_DEFS: dict[str, tuple[str, bool]] = {
    "☀️": (f'<circle cx="12" cy="12" r="4.6"/>{_SUN_RAYS}', True),
    "⛅": (
        '<circle cx="7.6" cy="7.6" r="2.5"/>'
        '<rect x="6.8" y="1.6" width="1.6" height="2.8" rx="0.8"/>'
        '<rect x="6.8" y="1.6" width="1.6" height="2.8" rx="0.8" transform="rotate(65 7.6 7.6)"/>'
        '<rect x="6.8" y="1.6" width="1.6" height="2.8" rx="0.8" transform="rotate(-65 7.6 7.6)"/>'
        '<circle cx="10.2" cy="16" r="2.9"/><circle cx="14" cy="13.1" r="3.7"/>'
        '<circle cx="17.5" cy="16" r="2.7"/><rect x="8.2" y="16" width="12.5" height="4.9" rx="2.45"/>',
        True,
    ),
    "☁️": (_CLOUD, True),
    "🌫️": (
        _CLOUD + '<rect x="6" y="20" width="12" height="1.4" rx="0.7"/>'
        '<rect x="8.5" y="22.3" width="10" height="1.4" rx="0.7"/>',
        True,
    ),
    "🌦️": (
        _CLOUD
        + '<circle cx="9.5" cy="22" r="1"/><circle cx="13.5" cy="22.6" r="1"/><circle cx="17" cy="22" r="1"/>',
        True,
    ),
    "🌧️": (
        _CLOUD
        + '<rect x="8.3" y="20.3" width="1.6" height="4" rx="0.8" transform="rotate(20 9 22)"/>'
        '<rect x="12.3" y="20.3" width="1.6" height="4" rx="0.8" transform="rotate(20 13 22)"/>'
        '<rect x="16.3" y="20.3" width="1.6" height="4" rx="0.8" transform="rotate(20 17 22)"/>',
        True,
    ),
    "🌨️": (
        _CLOUD
        + '<circle cx="9.5" cy="21.5" r="1.1"/><circle cx="13.5" cy="23" r="1.1"/><circle cx="17" cy="21.5" r="1.1"/>',
        True,
    ),
    "⛈️": (_CLOUD + '<path d="M14 19.5l-3.4 4.6h2.6l-1.6 3.4 4.6-5.4h-2.6l1.4-2.6z"/>', True),
    "🔺": ('<path d="M12 5.5l7.5 12.5h-15z"/>', True),
    "🔻": ('<path d="M12 18.5L4.5 6h15z"/>', True),
    "💵": (
        '<line x1="12" y1="2" x2="12" y2="22"/><path d="M17 5.5H9.5a3 3 0 0 0 0 6h5a3 3 0 0 1 0 6H6.5"/>',
        False,
    ),
    "📈": ('<polyline points="3,17 9,11 13,15 21,6"/><polyline points="15,6 21,6 21,12"/>', False),
    "💻": (
        '<rect x="3" y="4" width="18" height="16" rx="2"/>'
        '<polyline points="7,9 10.5,12 7,15"/><line x1="12.5" y1="15" x2="16.5" y2="15"/>',
        False,
    ),
    "🌍": (
        '<circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="4" ry="9"/>'
        '<line x1="3" y1="12" x2="21" y2="12"/>',
        False,
    ),
    "📍": (
        '<path d="M12 21.5s-7-6.7-7-11.5a7 7 0 0 1 14 0c0 4.8-7 11.5-7 11.5z"/><circle cx="12" cy="10" r="2.4"/>',
        False,
    ),
    "🎬": (
        '<rect x="3" y="8.5" width="18" height="11.5" rx="2"/>'
        '<path d="M3 8.5l2.5-4h3.4l-2.5 4M10 8.5l2.5-4h3.4l-2.5 4M17 8.5l1.6-4h2.4v4"/>',
        False,
    ),
    "🎮": (
        '<rect x="2" y="8" width="20" height="9.5" rx="4.5"/>'
        '<line x1="7" y1="10.7" x2="7" y2="14.3"/><line x1="5.2" y1="12.5" x2="8.8" y2="12.5"/>'
        '<circle cx="16" cy="11.3" r="1" fill="currentColor" stroke="none"/>'
        '<circle cx="18.5" cy="13.8" r="1" fill="currentColor" stroke="none"/>',
        False,
    ),
    "⚽": (
        '<circle cx="12" cy="12" r="9"/><path d="M12 8.2l2.6 1.9-1 3h-3.2l-1-3z"/>'
        '<path d="M12 3v5.2M6.3 7.2l3.1 2.9M17.7 7.2l-3.1 2.9M7.4 17l2.4-3.9M16.6 17l-2.4-3.9'
        'M9.6 21l.8-4.6M14.4 21l-.8-4.6"/>',
        False,
    ),
    "📚": (
        '<path d="M12 5.2c-2.1-1.5-5.1-2-8.2-1.1v13.4c3.1-.9 6.1-.4 8.2 1.1 2.1-1.5 5.1-2 8.2-1.1'
        'V4.1c-3.1-.9-6.1-.4-8.2 1.1z"/><line x1="12" y1="5.2" x2="12" y2="18.6"/>',
        False,
    ),
}

# Só os emojis de clima/tendência ficam embutidos em texto solto — vêm prontos do Python
# (`_wmo_emoji`, as setas de `enrich_payload`) dentro de strings como "today_summary" ou
# "vs_ontem". Os ícones de conceito (dólar, globo, bola...) só aparecem no cabeçalho da seção,
# resolvidos à parte em `render_html`. Ordenado por tamanho para o regex não casar um prefixo.
_INLINE_GLYPHS = ("☀️", "⛅", "☁️", "🌫️", "🌦️", "🌧️", "🌨️", "⛈️", "🔺", "🔻")
_INLINE_RE = re.compile("|".join(re.escape(g) for g in sorted(_INLINE_GLYPHS, key=len, reverse=True)))


def _svg(inner: str, *, filled: bool, cls: str) -> str:
    style = (
        'fill="currentColor"'
        if filled
        else 'fill="none" stroke="currentColor" stroke-width="1.75" '
        'stroke-linecap="round" stroke-linejoin="round"'
    )
    return (
        f'<svg class="{cls}" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" '
        f'{style} aria-hidden="true">{inner}</svg>'
    )


def _icon(glyph: str, cls: str = "icon") -> str:
    definition = _ICON_DEFS.get(glyph)
    if not definition:
        return html.escape(glyph)
    inner, filled = definition
    return _svg(inner, filled=filled, cls=cls)


def _iconify(escaped_text: str) -> str:
    """Troca emoji de clima/tendência por SVG inline, depois do `html.escape`.

    A ordem importa: o emoji não é afetado pelo escape (não é `&`/`<`/`>`), e o SVG que entra no
    lugar dele é sempre markup nosso — nunca conteúdo de terceiro passando cru.
    """
    return _INLINE_RE.sub(lambda m: _icon(m.group(0), cls="icon-inline"), escaped_text)


# Estética "terminal escuro" fixada pelo usuário em model.html — de propósito só dark, sem
# variante clara: é um visual de linha de comando, não um tema que se espera trocar com o SO.
STYLE = """
:root {
  --bg: #09090b; --card: #18181b; --ink: #fafafa; --muted: #a1a1aa;
  --line: #27272a; --accent: #10b981;
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 0 1rem 2.5rem; background: var(--bg); color: var(--ink);
  font: 16px/1.6 'Inter', system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  -webkit-text-size-adjust: 100%;
}
.wrap { max-width: 44rem; margin: 0 auto; }
header { padding: 2.5rem 0 1rem; }
.branding {
  font-size: 12px; color: var(--accent); font-family: monospace; text-transform: uppercase;
  letter-spacing: 2px; margin-bottom: 1.2rem; display: block;
}
h1 { margin: 0; font-size: 1.45rem; line-height: 1.25; letter-spacing: -0.01em; font-weight: 600; }
.sub { color: var(--muted); font-size: 0.85rem; margin-top: 0.6rem; font-family: monospace; }
.warn {
  margin-top: 1rem; padding: 0.7rem 0.9rem; border-radius: 6px; font-size: 0.85rem;
  font-family: monospace; background: var(--card); border: 1px dashed var(--line); color: var(--muted);
}
nav { display: flex; flex-wrap: wrap; gap: 0.5rem; margin: 2rem 0; }
nav a {
  text-decoration: none; color: var(--muted); background: var(--card);
  border: 1px solid var(--line); border-radius: 6px;
  padding: 0.4rem 0.8rem; font-size: 0.85rem; white-space: nowrap;
  transition: all 0.2s ease;
}
nav a:hover { border-color: var(--accent); color: var(--ink); }
section {
  border-left: 2px solid var(--line);
  padding-left: 1.2rem; margin-bottom: 2rem; position: relative; scroll-margin-top: 1rem;
}
section::before {
  content: ''; position: absolute; left: -5px; top: 6px;
  width: 8px; height: 8px; background: var(--accent); border-radius: 50%;
}
h2 {
  display: flex; align-items: center; gap: 0.5rem; margin: 0 0 1rem;
  font-size: 1rem; font-weight: 500; color: var(--ink); letter-spacing: 0.03em; text-transform: uppercase;
}
.icon { width: 1.15em; height: 1.15em; flex: none; }
.icon-inline { width: 0.95em; height: 0.95em; vertical-align: -0.15em; margin: 0 0.03em; }
ul { list-style: none; margin: 0; padding: 0; }
li { color: var(--muted); font-size: 0.95rem; }
li + li { margin-top: 0.8rem; padding-top: 0.8rem; border-top: 1px dashed var(--line); }
.label { font-weight: 600; color: var(--ink); }
a.ref { color: var(--accent); text-decoration: none; font-size: 0.85rem; white-space: nowrap; font-weight: 600; }
a.ref:hover { text-decoration: underline; }
footer {
  color: var(--muted); font-size: 0.8rem; text-align: left; padding-top: 1.5rem;
  font-family: monospace; border-top: 1px solid var(--line); margin-top: 2rem;
}
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
    parts.append(_iconify(html.escape(body)))

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
        f'<a href="#{section_id}">{_icon(emoji, cls="icon-inline")} {html.escape(title)}</a>'
        for section_id, emoji, title, _ in filled
    )
    body = "".join(
        f'<section id="{section_id}">'
        f'<h2><span class="emoji">{_icon(emoji, cls="icon")}</span>{html.escape(title)}</h2>'
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
<span class="branding">./jornal_matinal_2.0.sh</span>
<h1>{html.escape(date_header)}</h1>
<div class="sub">{subtitle}</div>
{warning}
</header>
<nav>{nav}</nav>
{body}
<footer>&gt; _ gerado automaticamente</footer>
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

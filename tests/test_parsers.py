"""Testes das funções puras — sem rede, determinísticas.

Rodar com:  python -m pytest -q
"""

from __future__ import annotations

from datetime import datetime

import pytest

from core.telegram_sender import _open_tags, _split_message, sanitize_html
from core.utils import format_date_pt_br, format_number_pt_br, strip_html
from scrapers.news_rss import _interleave, _normalize_title
from scrapers.promotions import _parse_price, _slugify


# --------------------------------------------------------------------------- números


@pytest.mark.parametrize(
    "valor,casas,esperado",
    [
        (5.1288, 2, "5,13"),
        (329233.0, 0, "329.233"),
        (175334.45, 2, "175.334,45"),
        (-1.52, 2, "-1,52"),
        (0.0, 2, "0,00"),
    ],
)
def test_format_number_pt_br(valor, casas, esperado):
    assert format_number_pt_br(valor, casas) == esperado


@pytest.mark.parametrize(
    "texto,esperado",
    [
        ("R$ 1.234,56", 1234.56),
        ("1234,56", 1234.56),
        ("1,234.56", 1234.56),
        ("99", 99.0),
        ("sem numero", None),
        ("", None),
        # Preço sem centavos: três dígitos após o separador são milhar, não decimal.
        ("R$ 1.299", 1299.0),
        ("1.299", 1299.0),
        ("R$ 4.093", 4093.0),
        ("1.234.567", 1234567.0),
        # Duas casas continuam sendo decimal.
        ("89.90", 89.90),
        ("89,90", 89.90),
        ("0,50", 0.50),
    ],
)
def test_parse_price(texto, esperado):
    assert _parse_price(texto) == esperado


# --------------------------------------------------------------------------- datas


def test_format_date_pt_br_segunda():
    # 27/07/2026 é uma segunda-feira.
    assert format_date_pt_br(datetime(2026, 7, 27)) == "Segunda-feira, 27 de Julho de 2026"


def test_format_date_pt_br_domingo():
    assert format_date_pt_br(datetime(2026, 7, 26)) == "Domingo, 26 de Julho de 2026"


# --------------------------------------------------------------------------- HTML


def test_strip_html_colapsa_por_padrao():
    assert strip_html("<p>um</p>\n<p>dois</p>") == "um dois"


def test_strip_html_preserva_linhas_quando_pedido():
    assert strip_html("<b>a</b>\n\n<i>b</i>", keep_newlines=True) == "a\n\nb"


def test_sanitize_converte_br_em_quebra():
    assert sanitize_html("um<br>dois") == "um\ndois"


def test_sanitize_escapa_e_comercial_em_url():
    resultado = sanitize_html('<a href="http://x.com?a=1&b=2">L</a>')
    assert "&amp;b=2" in resultado


def test_sanitize_nao_duplica_entidade_existente():
    assert sanitize_html("a &amp; b") == "a &amp; b"


def test_sanitize_escapa_menor_que_solto():
    assert sanitize_html("5 < 10") == "5 &lt; 10"


def test_sanitize_preserva_tags_validas():
    assert sanitize_html("<b>x</b> <i>y</i>") == "<b>x</b> <i>y</i>"


def test_sanitize_bloco_vira_quebra_e_nao_cola_palavras():
    # Remover <p> sem substituir produziria "umdois".
    assert sanitize_html("<p>um</p><p>dois</p>") == "um\n\ndois"


def test_sanitize_remove_tag_desconhecida_inline():
    assert sanitize_html("<span>texto</span>") == "texto"


# --------------------------------------------------------------------------- split


def test_split_curto_nao_divide():
    assert _split_message("mensagem curta") == ["mensagem curta"]


def test_split_respeita_limite():
    texto = "\n".join(f"linha {i} com algum conteudo" for i in range(500))
    partes = _split_message(texto)
    assert len(partes) > 1
    assert all(len(p) <= 4096 for p in partes)


def test_split_nao_deixa_tag_aberta():
    texto = "\n".join(f'item <a href="http://exemplo.com/{i}">link {i}</a>' for i in range(400))
    partes = _split_message(texto)
    assert len(partes) > 1
    for parte in partes:
        assert _open_tags(parte) == [], "pedaço terminou com tag aberta"


def test_split_reabre_tag_no_pedaco_seguinte():
    texto = "<b>" + "\n".join(f"linha {i}" for i in range(1000)) + "</b>"
    partes = _split_message(texto)
    assert len(partes) > 1
    assert partes[1].startswith("<b>")
    assert partes[0].endswith("</b>")


# --------------------------------------------------------------------------- RSS


def test_normalize_title_ignora_caixa_e_pontuacao():
    assert _normalize_title("Olá, Mundo!") == _normalize_title("olá mundo")


def test_interleave_distribui_entre_feeds():
    feeds = [
        [{"title": f"a{i}"} for i in range(8)],
        [{"title": f"b{i}"} for i in range(8)],
        [{"title": f"c{i}"} for i in range(8)],
    ]
    itens = _interleave(feeds, 15)
    origens = [item["title"][0] for item in itens]
    assert len(itens) == 15
    # O bug corrigido: concatenar e truncar deixava o terceiro feed com zero itens.
    assert origens.count("c") == 5
    assert origens.count("a") == origens.count("b") == 5


def test_interleave_remove_duplicatas_entre_feeds():
    feeds = [[{"title": "Mesma Notícia"}], [{"title": "mesma noticia!"}]]
    assert len(_interleave(feeds, 10)) == 1


def test_interleave_respeita_feed_menor():
    feeds = [[{"title": f"a{i}"} for i in range(5)], [{"title": "b0"}]]
    itens = _interleave(feeds, 10)
    assert len(itens) == 6


# --------------------------------------------------------------------------- diversos


def test_slugify():
    assert _slugify("Headset XYZ  Pro!") == "headset-xyz-pro"

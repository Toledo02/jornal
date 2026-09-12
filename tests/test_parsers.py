"""Testes das funções puras — sem rede, determinísticas.

Rodar com:  python -m pytest -q
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from core import history
from core.ai_engine import _coerce_journal
from core.renderer import SECTIONS, render_html, render_text, write_page
from core.telegram_sender import _split_message
from core.utils import format_date_pt_br, format_number_pt_br, strip_html
from scrapers.finance import _invert_variation, _variation_suffix
from scrapers.gaming import _days_until, _ends_in_label, _worth_usd
from scrapers.investments import _annualize, _build_stock_candidate, _investment_ideas, _real_rate
from scrapers.news_rss import _interleave, _normalize_title
from scrapers.weather import _rain_summary, _rain_window, _uv_label, _wmo_emoji


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


# --------------------------------------------------------------------------- split


def test_split_curto_nao_divide():
    assert _split_message("mensagem curta") == ["mensagem curta"]


def test_split_respeita_limite():
    texto = "\n".join(f"linha {i} com algum conteudo" for i in range(500))
    partes = _split_message(texto)
    assert len(partes) > 1
    assert all(len(p) <= 4096 for p in partes)


# --------------------------------------------------------------------------- página

JORNAL = {
    "sections": [
        {"id": "clima", "entries": [{"label": "Agora", "text": "⛅ Nublado, 18°C"}]},
        {
            "id": "mundo",
            "entries": [
                {"label": "Geopolítica", "text": "Cúpula em Genebra terminou sem acordo.",
                 "url": "https://exemplo.com/a", "url_label": "Ler"}
            ],
        },
    ]
}


def _render(jornal) -> str:
    return render_html(jornal, date_header="Sexta-feira, 12 de Setembro de 2026", generated_at="05:55")


def test_render_html_escapa_conteudo_de_feed():
    # Título de feed é conteúdo de terceiro e vai direto para a página.
    pagina = _render({"sections": [{"id": "mundo", "entries": [{"text": "<script>x</script>"}]}]})
    assert "<script>" not in pagina
    assert "&lt;script&gt;" in pagina


def test_render_html_descarta_url_que_nao_e_http():
    pagina = _render(
        {"sections": [{"id": "mundo", "entries": [{"text": "nota", "url": "javascript:alert(1)"}]}]}
    )
    assert "javascript:" not in pagina


def test_render_html_omite_secao_sem_entrada_aproveitavel():
    pagina = _render({"sections": [{"id": "clima", "entries": [{"label": "Agora", "text": ""}]}]})
    assert 'id="clima"' not in pagina


def test_render_html_respeita_a_ordem_das_secoes():
    # O modelo devolveu fora de ordem; a ordem de exibição é a do SECTIONS.
    pagina = _render({"sections": list(reversed(JORNAL["sections"]))})
    assert pagina.index('id="clima"') < pagina.index('id="mundo"')


def test_render_html_usa_titulo_e_emoji_do_codigo():
    pagina = _render(JORNAL)
    for section_id, emoji, titulo in SECTIONS:
        if section_id in {"clima", "mundo"}:
            assert emoji in pagina and titulo in pagina


def test_render_html_avisa_quando_degradado():
    assert "fallback" in _render({**JORNAL, "degraded": True}).lower()
    assert "fallback" not in _render(JORNAL).lower()


def test_render_text_traz_label_e_texto_de_cada_entrada():
    texto = render_text(JORNAL, date_header="Sexta-feira, 12 de Setembro de 2026")
    assert "Sexta-feira, 12 de Setembro de 2026" in texto
    assert "• Agora: ⛅ Nublado, 18°C" in texto
    assert "• Geopolítica: Cúpula em Genebra terminou sem acordo." in texto
    assert "<" not in texto, "a projeção do histórico é texto puro"


def test_render_text_alimenta_a_anti_repeticao():
    # O histórico guarda esta projeção; é dela que `filter_published_items` extrai os nomes.
    historico = {date.today().isoformat(): {"journal": render_text(JORNAL), "metrics": {}}}
    payload = {"world_news": {"items": [{"title": "Cúpula em Genebra terminou sem acordo"}]}}
    history.filter_published_items(
        payload, historico, days=3, sections={"world_news": ("items", "title", 0)}
    )
    assert payload["world_news"]["items"] == []


def test_write_page_cria_diretorio_e_escreve(tmp_path):
    destino = write_page("<html></html>", tmp_path / "site" / "index.html")
    assert destino.read_text(encoding="utf-8") == "<html></html>"
    assert not list(tmp_path.glob("site/*.tmp")), "o arquivo temporário não deve sobrar"


def test_coerce_journal_descarta_secao_desconhecida_e_entrada_vazia():
    limpo = _coerce_journal(
        {
            "sections": [
                {"id": "promocoes", "entries": [{"text": "seção que não existe mais"}]},
                {"id": "mundo", "entries": [{"text": ""}, {"text": "vale"}, "não é dict"]},
            ]
        }
    )
    assert limpo == {"sections": [{"id": "mundo", "entries": [{"text": "vale"}]}]}


@pytest.mark.parametrize("resposta", [{}, {"sections": "texto"}, {"sections": []}])
def test_coerce_journal_rejeita_resposta_inutil(resposta):
    # Erro aqui é de propósito: aciona o retry em vez de publicar uma página vazia.
    with pytest.raises(ValueError):
        _coerce_journal(resposta)


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


# --------------------------------------------------------------------------- economia


def test_variation_suffix_positivo_leva_sinal():
    assert _variation_suffix({"variation_percent": 0.098}) == " (+0,10%)"


def test_variation_suffix_negativo():
    assert _variation_suffix({"variation_percent": -2.13}) == " (-2,13%)"


def test_variation_suffix_ausente_nao_polui():
    assert _variation_suffix({}) == ""


def test_invert_variation_troca_o_sinal():
    # Se ARS-BRL sobe 1%, o que 1 BRL compra em ARS cai ~0,99%.
    assert _invert_variation(1.0) == -0.99
    assert _invert_variation(-1.0) == 1.01


def test_invert_variation_sem_dado():
    assert _invert_variation(None) is None


# --------------------------------------------------------------------------- clima


def test_rain_window_encontra_intervalo():
    horas = list(range(24))
    probs = [0] * 14 + [60, 70, 80] + [10] * 7
    assert _rain_window(horas, probs, 40) == "14h-16h"


def test_rain_window_hora_unica():
    horas = list(range(24))
    probs = [0] * 9 + [55] + [0] * 14
    assert _rain_window(horas, probs, 40) == "9h"


def test_rain_window_sem_chuva():
    assert _rain_window(list(range(24)), [5] * 24, 40) is None


@pytest.mark.parametrize(
    "indice,esperado",
    [(0, "baixo"), (2.9, "baixo"), (5.65, "moderado"), (7, "alto"), (9, "muito alto"), (12, "extremo")],
)
def test_uv_label(indice, esperado):
    assert _uv_label(indice) == esperado


@pytest.mark.parametrize(
    "code,esperado",
    [
        (0, "☀️"), (1, "☀️"), (2, "⛅"), (3, "☁️"), (45, "🌫️"), (53, "🌦️"),
        (63, "🌧️"), (75, "🌨️"), (81, "🌧️"), (95, "⛈️"), (None, ""), (7, ""),
    ],
)
def test_wmo_emoji(code, esperado):
    assert _wmo_emoji(code) == esperado


def test_wmo_emoji_estavel_para_o_mesmo_codigo():
    # A razão de montar em Python: o mesmo código sempre dá o mesmo ícone.
    assert _wmo_emoji(63) == _wmo_emoji(63) == "🌧️"


# --------------------------------------------------------------------------- gaming


@pytest.mark.parametrize("texto,esperado", [("$19.99", 19.99), ("$2.99", 2.99), ("N/A", 0.0), (None, 0.0)])
def test_worth_usd(texto, esperado):
    assert _worth_usd(texto) == esperado


# --------------------------------------------------------------------------- histórico


def _historico(dias: int, metrica: str = "usd_brl") -> dict:
    hoje = date.today()
    registro: dict = {}
    for i in range(dias, 0, -1):
        dia = hoje - timedelta(days=i)
        registro = history.record(registro, dia, f"jornal {dia}", {metrica: 5.0 + i * 0.01})
    return registro


def test_prune_respeita_a_janela():
    podado = history.prune(_historico(40), 30)
    assert len(podado) == 30


def test_prune_descarta_chave_invalida():
    assert history.prune({"nao-e-data": {}, date.today().isoformat(): {}}, 30) == {
        date.today().isoformat(): {}
    }


def test_recent_journals_do_mais_novo_para_o_mais_antigo():
    dias = [dia for dia, _ in history.recent_journals(_historico(10), 3)]
    assert dias == sorted(dias, reverse=True)
    assert len(dias) == 3


def test_describe_metric_detecta_maxima():
    assert history.describe_metric(_historico(30), "usd_brl", 99.0) == "maior valor em 30 dias"


def test_describe_metric_detecta_minima():
    assert history.describe_metric(_historico(30), "usd_brl", 0.1) == "menor valor em 30 dias"


def test_describe_metric_silencia_no_meio_da_faixa():
    assert history.describe_metric(_historico(30), "usd_brl", 5.15) is None


def test_describe_metric_exige_serie_minima():
    # Com 4 dias, "maior valor em 4 dias" seria ruído, não informação.
    assert history.describe_metric(_historico(4), "usd_brl", 99.0) is None


def test_enrich_payload_acrescenta_sem_perder_o_display():
    payload = {"finance": {"usd_brl": {"bid": 99.0, "display": "R$ 99,00 (+1,00%)"}}}
    history.enrich_payload(payload, _historico(30))
    assert payload["finance"]["usd_brl"]["display"] == "R$ 99,00 (+1,00%) — maior valor em 30 dias"


def test_enrich_payload_ignora_ativo_sem_display():
    payload = {"finance": {"usd_brl": {"bid": 99.0}}}
    history.enrich_payload(payload, _historico(30))
    assert "display" not in payload["finance"]["usd_brl"]


def test_extract_metrics_le_cotacoes_e_temperaturas():
    payload = {
        "finance": {"usd_brl": {"bid": 5.12}, "ibovespa": {"points": 175334.45}},
        "weather": {"temp_max_c": 26.3, "temp_min_c": 16.5},
    }
    assert history.extract_metrics(payload) == {
        "usd_brl": 5.12,
        "ibovespa": 175334.45,
        "temp_max": 26.3,
        "temp_min": 16.5,
    }


# --------------------------------------------------------------------------- clima por tópico


def test_rain_summary_junta_probabilidade_volume_e_janela():
    assert _rain_summary(80, 3.1, "16h-19h") == (
        "80% de chance, 3,1 mm previstos, mais provável entre 16h-19h"
    )


def test_rain_summary_sem_janela_omite_o_trecho():
    assert _rain_summary(30, None, None) == "30% de chance"


def test_rain_summary_zero_por_cento_diz_que_nao_chove():
    assert _rain_summary(0, 0, None) == "sem chuva prevista"


def test_rain_summary_sem_dado_nenhum():
    assert _rain_summary(None, None, None) is None


# --------------------------------------------------------------------------- prazo dos giveaways


def test_days_until_conta_a_partir_de_hoje():
    amanha = (date.today() + timedelta(days=1)).isoformat()
    assert _days_until(f"{amanha} 23:59:00") == 1


def test_days_until_aceita_data_sem_hora():
    assert _days_until(date.today().isoformat()) == 0


@pytest.mark.parametrize("valor", [None, "N/A", "sem data"])
def test_days_until_sem_data(valor):
    assert _days_until(valor) is None


@pytest.mark.parametrize(
    "dias,esperado",
    [(0, "último dia"), (-1, "último dia"), (1, "termina amanhã"), (5, "termina em 5 dias")],
)
def test_ends_in_label(dias, esperado):
    assert _ends_in_label(dias) == esperado


# --------------------------------------------------------------------------- investimentos


def test_real_rate_usa_fisher_e_nao_subtracao():
    # 13,90 - 4,64 daria 9,26; o correto é 8,85.
    assert round(_real_rate(13.90, 4.64), 2) == 8.85


def test_annualize_converte_taxa_mensal():
    assert round(_annualize(0.6717), 2) == 8.36


def test_investment_ideas_nao_repete_a_comparacao_com_o_cdi():
    indicadores = {
        "cdi": {"label": "CDI", "value": 13.9, "display": "13,90% a.a."},
        "poupanca": {"label": "Poupança", "value": 0.6717, "display": "0,67% a.m."},
    }
    derivados = {
        "poupanca_anual": {
            "value": 8.36,
            "display": "8,36% a.a. equivalentes — cerca de 60% do CDI",
            "percent_of_cdi": 60.0,
        }
    }
    ideia = next(i for i in _investment_ideas(indicadores, derivados) if i["id"] == "poupanca")
    # Reaproveitar o `display` (que já embute a comparação) repetia "60% do CDI" na mesma frase.
    # A menção a "100% do CDI" é outra coisa: é o CDB com que a poupança está sendo comparada.
    assert ideia["text"].count("60% do CDI") == 1
    assert "8,36% a.a." in ideia["text"]


def test_investment_ideas_sem_indicador_nao_inventa_frase():
    assert _investment_ideas({}, {}) == []


def test_investment_ideas_tem_id_estavel():
    indicadores = {
        "selic": {"label": "Selic", "value": 14.0, "display": "14,00% a.a."},
        "cdi": {"label": "CDI", "value": 13.9, "display": "13,90% a.a."},
        "ipca_12m": {"label": "IPCA", "value": 4.64, "display": "4,64% em 12 meses"},
    }
    derivados = {"juro_real": {"value": 8.85, "display": "8,85% a.a. acima da inflação"}}
    ids = [i["id"] for i in _investment_ideas(indicadores, derivados)]
    assert ids[:2] == ["selic", "juro_real"]
    assert "ipca_plus" in ids  # juro real >= 5


_PETR4 = {"ticker": "PETR4", "name": "Petrobras", "class": "ação"}


def test_build_stock_candidate_calcula_variacao_e_faixa():
    candidato = _build_stock_candidate(_PETR4, [30.10, 42.80, 38.00, 38.50])
    assert candidato["ticker"] == "PETR4"
    assert candidato["display"] == "R$ 38,50 (+1,32%)"
    assert candidato["range_display"] == "mín. R$ 30,10 / máx. R$ 42,80 em 12 meses"


def test_build_stock_candidate_sinal_negativo():
    candidato = _build_stock_candidate(_PETR4, [40.00, 38.00])
    assert candidato["display"] == "R$ 38,00 (-5,00%)"


def test_build_stock_candidate_sem_closes_suficientes_retorna_none():
    assert _build_stock_candidate(_PETR4, []) is None
    assert _build_stock_candidate(_PETR4, [38.50]) is None


# --------------------------------------------------------------------------- repetição de jogos


def _payload_gaming(free_games=None, deals=None, limit=2) -> dict:
    return {
        "gaming": {
            "free_games": list(free_games or []),
            "deals": list(deals or []),
            "deals_display_limit": limit,
        }
    }


def _historico_com_destaques(titulos: dict[str, list[str]], dias_atras: int = 1) -> dict:
    dia = date.today() - timedelta(days=dias_atras)
    destaques = {
        chave: [history._highlight_key(t) for t in valores] for chave, valores in titulos.items()
    }
    return history.record({}, dia, "jornal", {}, destaques)


def test_extract_highlights_normaliza_o_titulo():
    payload = _payload_gaming(deals=[{"title": "Sid Meier's Civilization VI"}])
    assert history.extract_highlights(payload)["deals"] == ["sidmeierscivilizationvi"]


def test_repeat_policy_descarta_oferta_paga_ja_publicada():
    payload = _payload_gaming(
        deals=[{"title": "The Witcher 3"}, {"title": "Disco Elysium"}, {"title": "MORDHAU"}]
    )
    history.apply_repeat_policy(payload, _historico_com_destaques({"deals": ["The Witcher 3"]}))
    assert [d["title"] for d in payload["gaming"]["deals"]] == ["Disco Elysium", "MORDHAU"]


def test_repeat_policy_respeita_o_limite_de_exibicao():
    payload = _payload_gaming(deals=[{"title": f"Jogo {i}"} for i in range(10)], limit=3)
    history.apply_repeat_policy(payload, {})
    assert len(payload["gaming"]["deals"]) == 3
    # A chave auxiliar não deve sobrar no payload que vai ao modelo.
    assert "deals_display_limit" not in payload["gaming"]


def test_repeat_policy_mantem_giveaway_repetido_que_esta_acabando():
    payload = _payload_gaming(
        free_games=[
            {"title": "Breathedge", "days_left": 1},
            {"title": "Moonlighter", "days_left": 6},
            {"title": "Novo Jogo", "days_left": 4},
            {"title": "Outro Novo", "days_left": 4},
        ]
    )
    historico = _historico_com_destaques({"free_games": ["Breathedge", "Moonlighter"]})
    history.apply_repeat_policy(payload, historico)

    titulos = [g["title"] for g in payload["gaming"]["free_games"]]
    # Breathedge fica por estar acabando; Moonlighter sai porque já saiu e ainda tem prazo.
    assert titulos == ["Breathedge", "Novo Jogo", "Outro Novo"]


def test_repeat_policy_completa_com_repetidos_quando_falta_novidade():
    payload = _payload_gaming(
        free_games=[{"title": "Breathedge", "days_left": 6}, {"title": "Moonlighter", "days_left": 6}]
    )
    historico = _historico_com_destaques({"free_games": ["Breathedge", "Moonlighter"]})
    history.apply_repeat_policy(payload, historico)
    # Nenhum é novo, mas seção vazia é pior: mantém o mínimo.
    assert len(payload["gaming"]["free_games"]) == 2


def test_repeat_policy_marca_o_que_e_novo():
    payload = _payload_gaming(free_games=[{"title": "Novo Jogo", "days_left": 3}])
    history.apply_repeat_policy(payload, _historico_com_destaques({"free_games": ["Breathedge"]}))
    assert payload["gaming"]["free_games"][0]["is_new"] is True
    assert payload["gaming"]["free_games"][0]["days_shown"] == 0


def test_repeat_policy_ignora_payload_sem_gaming():
    payload = {"weather": {"city": "Curitiba"}}
    history.apply_repeat_policy(payload, {})
    assert payload == {"weather": {"city": "Curitiba"}}


# --------------------------------------------------------------------------- rodízio de ações


def _payload_stocks(tickers: list[str], limit: int = 3) -> dict:
    return {
        "investments": {
            "stocks": {
                "candidates": [{"ticker": t, "display": t} for t in tickers],
                "display_limit": limit,
            }
        }
    }


def _historico_com_stocks(tickers: list[str], dias_atras: int = 1) -> dict:
    dia = date.today() - timedelta(days=dias_atras)
    return history.record({}, dia, "jornal", {}, {"stocks": tickers})


def test_extract_highlights_inclui_tickers_de_acoes():
    payload = _payload_stocks(["PETR4", "VALE3"])
    assert history.extract_highlights(payload)["stocks"] == ["PETR4", "VALE3"]


def test_stock_rotation_prefere_tickers_nao_vistos():
    payload = _payload_stocks(["PETR4", "VALE3", "ITUB4", "WEGE3"], limit=2)
    history.apply_stock_rotation(payload, _historico_com_stocks(["PETR4", "VALE3"]))
    tickers = [c["ticker"] for c in payload["investments"]["stocks"]["candidates"]]
    assert tickers == ["ITUB4", "WEGE3"]


def test_stock_rotation_completa_com_repetidos_quando_falta_novidade():
    payload = _payload_stocks(["PETR4", "VALE3"], limit=2)
    history.apply_stock_rotation(payload, _historico_com_stocks(["PETR4", "VALE3"]))
    tickers = [c["ticker"] for c in payload["investments"]["stocks"]["candidates"]]
    assert tickers == ["PETR4", "VALE3"]


def test_stock_rotation_remove_display_limit_do_payload():
    payload = _payload_stocks(["PETR4", "VALE3", "ITUB4"], limit=1)
    history.apply_stock_rotation(payload, {})
    assert "display_limit" not in payload["investments"]["stocks"]
    assert len(payload["investments"]["stocks"]["candidates"]) == 1


def test_stock_rotation_ignora_payload_sem_stocks():
    payload = {"investments": {"indicators": {}}}
    history.apply_stock_rotation(payload, {})
    assert payload == {"investments": {"indicators": {}}}


# --------------------------------------------------------------------------- ideia de investimento


def _payload_ideas(ids: list[str]) -> dict:
    return {"investments": {"ideas": [{"id": i, "text": f"frase {i}"} for i in ids]}}


def _historico_com_ideias(ids: list[str], dias_atras: int = 1) -> dict:
    dia = date.today() - timedelta(days=dias_atras)
    return history.record({}, dia, "jornal", {}, {"investment_idea": ids})


def test_investment_idea_escolhe_a_menos_usada():
    payload = _payload_ideas(["selic", "juro_real", "poupanca"])
    hist = _historico_com_ideias(["selic"])
    hist = history.record(hist, date.today() - timedelta(days=2), "j", {}, {"investment_idea": ["juro_real"]})
    history.apply_investment_idea(payload, hist)
    assert payload["investments"]["idea_of_the_day"]["id"] == "poupanca"
    # O pool sai do payload que vai ao modelo.
    assert "ideas" not in payload["investments"]


def test_investment_idea_empate_mantem_a_ordem_do_pool():
    payload = _payload_ideas(["selic", "juro_real", "poupanca"])
    history.apply_investment_idea(payload, {})
    assert payload["investments"]["idea_of_the_day"]["id"] == "selic"


def test_investment_idea_registrada_nos_highlights():
    payload = {"investments": {"idea_of_the_day": {"id": "poupanca", "text": "x"}}}
    assert history.extract_highlights(payload)["investment_idea"] == ["poupanca"]


def test_investment_idea_ignora_payload_sem_ideias():
    payload = {"investments": {"indicators": {}}}
    history.apply_investment_idea(payload, {})
    assert payload == {"investments": {"indicators": {}}}

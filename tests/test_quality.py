"""Testes das regras de relevância — o que entra no jornal e o que é cortado.

Separado de `test_parsers.py`, que cobre formatação e parsing. Aqui a pergunta é outra: dado o
que as fontes trouxeram e o que já foi publicado, o que sobra? Também sem rede.

Rodar com:  python -m pytest -q
"""

from __future__ import annotations

from datetime import date, timedelta

import httpx
import pytest

from core import history
from core.ai_engine import _fallback_journal
from core.telegram_sender import sanitize_html
from core.utils import format_date_pt_br, now_local
from scrapers.football import _is_out_of_plan
from scrapers.weather import _rain_summary, _rain_window, _rains_most_of_day


# --------------------------------------------------------------------------- janela de chuva

# Dados reais de Curitiba em 08/08/2026, que expuseram o bug: um resmungo de 47%/42% na
# madrugada e a chuva de verdade das 13h às 17h. O jornal daquele dia anunciou "0h-1h".
CHUVA_08_08 = [47, 42, 31, 27, 36, 50, 61, 63, 61, 61, 64, 67, 73, 83, 94, 100, 95, 85, 76, 70, 65, 61, 59, 59]
HORAS = list(range(24))


def test_rain_window_ignora_a_madrugada_que_ja_passou():
    # Lido às 5h55, "chuva entre 0h e 1h" descreve a madrugada que a pessoa dormiu.
    assert _rain_window(HORAS, CHUVA_08_08, 40, 5) == "13h-17h"


def test_rain_window_escolhe_o_pico_e_nao_o_primeiro_bloco():
    # Mesmo começando à meia-noite, o bloco de 47% não deve ganhar do de 100%.
    assert _rain_window(HORAS, CHUVA_08_08, 40, 0) == "13h-17h"


def test_rain_window_estreita_bloco_longo_demais_para_ser_aviso():
    # Das 5h às 23h tudo passa do limiar, e "chuva entre 5h e 23h" não ajuda ninguém.
    inicio, fim = (int(parte.rstrip("h")) for parte in _rain_window(HORAS, CHUVA_08_08, 40, 5).split("-"))
    assert fim - inicio + 1 <= 6


def test_rain_window_no_fim_do_dia_so_olha_para_frente():
    assert _rain_window(HORAS, CHUVA_08_08, 40, 18) == "18h-23h"


def test_rain_window_bloco_curto_fica_intacto():
    probabilidades = [0] * 14 + [60, 70, 80] + [10] * 7
    assert _rain_window(HORAS, probabilidades, 40, 0) == "14h-16h"


def test_rain_window_sem_chuva():
    assert _rain_window(HORAS, [5] * 24, 40, 0) is None


def test_rains_most_of_day_detecta_dia_chuvoso():
    assert _rains_most_of_day(HORAS, CHUVA_08_08, 40, 5) is True


def test_rains_most_of_day_falso_em_pancada_isolada():
    probabilidades = [0] * 14 + [60, 70, 80] + [10] * 7
    assert _rains_most_of_day(HORAS, probabilidades, 40, 0) is False


def test_rain_summary_diz_mais_forte_quando_chove_o_dia_todo():
    assert _rain_summary(100, 3.1, "13h-17h", all_day=True) == (
        "100% de chance, 3,1 mm previstos, chuva praticamente o dia todo, mais forte entre 13h-17h"
    )


# --------------------------------------------------------------------------- futebol fora do plano


def _erro_http(status: int) -> httpx.HTTPStatusError:
    requisicao = httpx.Request("GET", "https://api.football-data.org/v4/teams/764/matches")
    return httpx.HTTPStatusError(
        "erro", request=requisicao, response=httpx.Response(status, request=requisicao)
    )


def test_403_e_falta_de_cobertura_no_plano():
    assert _is_out_of_plan(_erro_http(403)) is True


@pytest.mark.parametrize("status", [400, 429, 500, 503])
def test_outros_erros_http_continuam_sendo_falha(status):
    assert _is_out_of_plan(_erro_http(status)) is False


def test_erro_generico_nao_e_falta_de_cobertura():
    assert _is_out_of_plan(ValueError("timeout")) is False


# --------------------------------------------------------------------------- matéria já contada


def _com_jornal(texto: str, dias_atras: int = 1) -> dict:
    return history.record({}, date.today() - timedelta(days=dias_atras), texto, {})


def _noticias(*titulos: str) -> dict:
    return {"world_news": {"items": [{"title": t} for t in titulos], "count": len(titulos)}}


def test_entities_apara_o_genitivo():
    # A BBC escreve "Messi's" e o jornal escreve "Messi"; sem aparar, os dois não batem.
    assert "messi" in history._entities("Messi's father Jorge dies aged 68")


def test_entities_ignora_conectivo_em_maiuscula():
    assert "the" not in history._entities("The New Deal With Amazon")


def test_filtro_remove_materia_ja_contada():
    payload = _noticias(
        "Irã divulga lista de exigências aos EUA para reabertura do Estreito de Ormuz",
        "Incêndio florestal provoca evacuações na Colômbia Britânica",
        "Madonna homenageia o produtor William Orbit",
        "Britney Spears mostra procedimento estético",
    )
    jornal = "O Ira divulgou uma lista de exigencias aos Estados Unidos para reabrir o Estreito de Ormuz."
    history.filter_published_items(payload, _com_jornal(jornal))
    assert all("Ormuz" not in item["title"] for item in payload["world_news"]["items"])


def test_filtro_atravessa_a_traducao():
    # O feed vem em inglês e o jornal saiu em português: só o nome próprio sobrevive à tradução.
    payload = _noticias(
        "Messi's father Jorge dies aged 68 after illness",
        "Spain imposes border controls against Italy",
        "Madonna pays tribute to producer William Orbit",
        "Britney Spears shows off cosmetic procedure",
    )
    jornal = "Jorge Messi, pai e empresario de Lionel Messi, faleceu aos 68 anos na Argentina."
    history.filter_published_items(payload, _com_jornal(jornal))
    assert all("Messi" not in item["title"] for item in payload["world_news"]["items"])


def test_filtro_preserva_materia_nova_sobre_a_mesma_pessoa():
    payload = _noticias(
        "Trump anuncia nova tarifa sobre semicondutores da Coreia do Sul",
        "Incêndio florestal na Colômbia Britânica",
        "Madonna homenageia William Orbit",
        "Britney Spears desabafa sobre procedimento",
    )
    jornal = "Trump confirmou Todd Blanche como procurador-geral dos Estados Unidos."
    history.filter_published_items(payload, _com_jornal(jornal))
    assert any("semicondutores" in item["title"] for item in payload["world_news"]["items"])


def test_filtro_exige_mais_de_um_nome_coincidindo():
    # Com um nome só, "Bolsonaro" apagaria qualquer matéria sobre ele pelo resto da semana.
    payload = _noticias(
        "Bolsonaro se pronuncia sobre a decisão",
        "Incêndio na Colômbia Britânica",
        "Madonna homenageia William Orbit",
        "Britney Spears desabafa",
    )
    history.filter_published_items(payload, _com_jornal("Bolsonaro falou ontem sobre outro assunto."))
    assert any("Bolsonaro" in item["title"] for item in payload["world_news"]["items"])


def test_filtro_nunca_esvazia_a_secao():
    # Se tudo já saiu ontem, ficar sem a seção Mundo é pior que repetir.
    payload = _noticias("Guerra na Ucrânia avança", "Ucrânia recebe apoio da Otan", "Otan reforça a Ucrânia")
    history.filter_published_items(
        payload, _com_jornal("Guerra na Ucrania avanca e a Otan reforca o apoio a Ucrania.")
    )
    assert len(payload["world_news"]["items"]) == 3


def test_filtro_mantem_o_count_coerente():
    payload = _noticias(
        "Irã divulga exigências sobre o Estreito de Ormuz",
        "Incêndio na Colômbia Britânica",
        "Madonna homenageia William Orbit",
        "Britney Spears desabafa",
    )
    history.filter_published_items(payload, _com_jornal("O Ira fez exigencias sobre o Estreito de Ormuz."))
    assert payload["world_news"]["count"] == len(payload["world_news"]["items"])


def test_filtro_sem_historico_nao_mexe_em_nada():
    payload = _noticias("Uma", "Duas", "Três")
    history.filter_published_items(payload, {})
    assert len(payload["world_news"]["items"]) == 3


def test_filtro_alcanca_a_secao_local():
    payload = {
        "local": {
            "items": [
                {"title": "Prefeitura de Curitiba anuncia obra na Linha Verde"},
                {"title": "Câmara de Curitiba aprova novo plano diretor"},
                {"title": "Show de Caetano Veloso na Ópera de Arame"},
            ],
            "count": 3,
        }
    }
    jornal = "A Prefeitura de Curitiba anunciou o início da obra na Linha Verde nesta semana."
    history.filter_published_items(payload, _com_jornal(jornal))
    titulos = [i["title"] for i in payload["local"]["items"]]
    assert not any("Linha Verde" in t for t in titulos)
    assert any("plano diretor" in t for t in titulos)


# --------------------------------------------------------------------------- fallback do LLM

# Vale testar porque é o caminho que roda no pior dia: em 01/07/2026 as três tentativas do Gemini
# falharam e o fallback foi realmente enviado — com os asteriscos de Markdown do item 4.4.


def _payload_completo() -> dict:
    return {
        "weather": {
            "city": "Curitiba",
            "temp_now": "20,8°C",
            "temp_max": "21,1°C",
            "temp_min": "12,8°C",
            "rain_summary": "100% de chance, 3,1 mm previstos",
            "sun_summary": "nascer 06:50 · pôr 17:55",
        },
        "finance": {"usd_brl": {"display": "R$ 5,08 (+0,04%)"}},
        "investments": {
            "indicators": {"selic": {"label": "Selic (meta)", "display": "14,00% a.a."}},
            "idea_of_the_day": {"id": "selic", "text": "A Selic está em 14,00% a.a."},
        },
        "world_news": {"items": [{"title": "Uma notícia"}, {"title": "Outra notícia"}]},
    }


def test_fallback_atravessa_o_sanitizador_intacto():
    # É a razão de ser do texto puro: a versão antiga usava asteriscos de Markdown mas era
    # enviada como HTML, então os asteriscos apareciam literalmente na mensagem.
    texto = _fallback_journal(_payload_completo(), None)
    assert sanitize_html(texto) == texto.strip()


def test_fallback_nao_usa_marcacao():
    texto = _fallback_journal(_payload_completo(), None)
    assert "*" not in texto
    assert "<" not in texto


def test_fallback_traz_data_e_secoes_com_dado():
    texto = _fallback_journal(_payload_completo(), None)
    assert format_date_pt_br(now_local()) in texto
    for esperado in ("CLIMA", "ECONOMIA", "IDEIAS DE INVESTIMENTO", "MUNDO", "R$ 5,08 (+0,04%)"):
        assert esperado in texto


def test_fallback_omite_secao_que_falhou():
    payload = _payload_completo()
    payload["finance"] = {"_error": "sem cotação"}
    payload["investments"] = {"_error": "BCB fora do ar"}
    texto = _fallback_journal(payload, None)
    assert "ECONOMIA" not in texto
    assert "IDEIAS DE INVESTIMENTO" not in texto
    assert "CLIMA" in texto


def test_fallback_com_payload_vazio_ainda_produz_cabecalho():
    texto = _fallback_journal({}, None)
    assert format_date_pt_br(now_local()) in texto
    assert "fallback" in texto


def test_fallback_nao_quebra_com_ativo_sem_display():
    payload = {"finance": {"usd_brl": {"bid": 5.08}}, "weather": {"city": "Curitiba"}}
    assert _fallback_journal(payload, None)


def test_filtro_ignora_jornal_antigo_demais():
    # Depois de uma interrupção do cron, o jornal de um mês atrás não pode passar por "ontem".
    payload = _noticias(
        "Irã divulga exigências sobre o Estreito de Ormuz",
        "Incêndio na Colômbia Britânica",
        "Madonna homenageia William Orbit",
        "Britney Spears desabafa",
    )
    antigo = _com_jornal("O Ira fez exigencias sobre o Estreito de Ormuz.", dias_atras=30)
    history.filter_published_items(payload, antigo, days=3)
    assert len(payload["world_news"]["items"]) == 4

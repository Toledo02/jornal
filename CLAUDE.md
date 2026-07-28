# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Visão geral

Agente Python que coleta dados de várias fontes (clima, câmbio, RSS, GitHub, gaming, futebol,
preços), consolida via Gemini e envia um jornal matinal em pt-BR pelo Telegram. Roda como job único
via cron às 05:55, não como serviço.

[PLANO.md](PLANO.md) mantém o backlog de melhorias com o diagnóstico que originou cada item — vale
consultar antes de mexer em algo que pareça estranho, a causa provavelmente está documentada lá.

## Comandos

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp config/.env.example config/.env

python main.py                        # pipeline completo (envia ao Telegram)
python main.py --dry-run              # gera e imprime, sem enviar
python main.py --no-llm               # só coleta e imprime o payload, sem gastar requisição
python main.py --only gaming,finance  # subconjunto de scrapers
python -m pytest -q                   # 66 testes, sem rede
```

Ao iterar em scrapers use `--no-llm --only <scraper>`: não consome cota do Gemini nem envia mensagem.

## Arquitetura

Pipeline linear em [main.py](main.py): `load_settings` → scrapers em paralelo → `generate_journal`
(LLM) → `sanitize_html` → `send_journal`.

**Contrato dos scrapers.** Cada um expõe `async def fetch(settings) -> ScraperResult` e é registrado
em `SCRAPERS` ([main.py](main.py)). `ScraperResult` ([core/utils.py](core/utils.py)) tem `status` =
`ok` | `partial` | `error`; `to_payload()` injeta `_error`/`_warning`, que o prompt sabe interpretar.
Scrapers **nunca propagam exceção** — capturam e devolvem `status="error"`.

**Observabilidade não é opcional aqui.** Seções `error`/`partial` geram alerta no Telegram
(`_collect_issues` → `send_alert`). Isso existe porque dois feeds ficaram 404 por três semanas e a
AwesomeAPI 47 dias, sem ninguém notar: a cadeia de fallback é boa o bastante para esconder falhas.
Ao adicionar uma fonte, garanta que a falha dela apareça no `status` — mas só quando o resultado
final for pior por causa dela (ver "Alerta é sobre resultado" abaixo).

**Camada LLM.** [core/ai_engine.py](core/ai_engine.py) usa `google-genai`, com backoff 10s→30s→90s e
fallback para os modelos de `llm_fallback_models`. Erros 4xx não são repetidos (`_is_permanent`).
Se tudo falhar, `_fallback_journal` produz um resumo em **texto puro** — sem marcação, para
atravessar o sanitizador intacto.

**Envio.** [core/telegram_sender.py](core/telegram_sender.py) faz `sanitize_html` (mantém só o
subset do Telegram, converte tags de bloco em quebra, escapa `&`/`<`/`>`), divide acima de 4096
fechando e reabrindo tags no corte, e em caso de erro reenvia com as tags removidas.

## Pontos de atenção

**Prod ≠ local — isto é a regra mais importante do repositório.** A VPS recebe respostas diferentes
das da sua máquina. O CheapShark exige User-Agent descritivo (por isso `USER_AGENT` em
[core/utils.py](core/utils.py) **não** imita navegador) e a AwesomeAPI aplica cota por IP e responde
429 lá desde 11/06/2026. Validar scraper só localmente não prova nada; rode `--no-llm --only X` na
VPS. Quando algo falhar com código estranho, **leia o corpo da resposta antes de teorizar** — o 400
do CheapShark parecia bloqueio de IP e a resposta dizia exatamente qual era o problema.

**Dois User-Agents, de propósito.** `USER_AGENT` (descritivo) para APIs e feeds; `BROWSER_HEADERS`
para os alvos de scraping de HTML — InfoMoney, GE Globo, Buscapé, canais do Telegram e GitHub Trending.
As duas famílias de site querem coisas opostas.

**O `SYSTEM_PROMPT` é a camada de apresentação.** Seções, ordem, emojis, onde links são permitidos e
formatação vivem no prompt ([core/ai_engine.py](core/ai_engine.py)), não em código. Restrições que
já custaram bug: só `<b>`, `<i>` e `<a href>`; bullets com `•` (o modelo recorre a `*` do Markdown se
não mandarem o contrário, e `*` aparece literal em HTML); a regra anti-repetição é escopada às
seções de notícia — Clima e Economia são exemplos de conteúdo que **deve** repetir todo dia.

**Números vêm formatados de Python, não do LLM.** Cada ativo em `finance` carrega um campo `display`
já em pt-BR, e o prompt manda copiá-lo literalmente. Modelo formatando número produz `R$ 0.0034278`,
e pior: em produção ele chegou a calcular uma variação percentual do IBOVESPA que ninguém pediu.

**A cadeia de cotações preenche ativo a ativo**, não tudo-ou-nada
([finance.py](scrapers/finance.py)): HG Brasil → yfinance → AwesomeAPI, cada fonte cobrindo o que
faltou. `BTC-BRL` saiu do Yahoo e é derivado de `BTC-USD × USD-BRL`.

**Config-driven é regra.** URLs, feeds, seletores CSS, times, coordenadas e produtos ficam em
[config/config.yaml](config/config.yaml). Nos feeds RSS, os itens são intercalados em round-robin
antes do corte — concatenar e truncar fazia os primeiros feeds consumirem todos os slots.

**Scrapers de HTML são frágeis por natureza.** Futebol (GE), promoções (Buscapé) e
GitHub Trending dependem de seletores CSS. Nunca use seletor amplo como `"div, section"`: ele casa
com a página inteira e injeta o body todo no payload do LLM. Prefira seletores específicos com
alternativas por vírgula, limite de tamanho por bloco, e uma lista de ruído quando o site mistura
chamadas de engajamento ao feed.

**Nomes `OPENAI_*` são herança.** `Settings` usa `llm_*` e o `.env` aceita `LLM_*`, `GEMINI_*` ou
`OPENAI_*` nessa ordem de precedência ([config/settings.py](config/settings.py)). `_resolve_model`
ignora nomes que não sejam do Gemini, porque o `.env.example` antigo distribuía `gpt-4o-mini`.

**Emoji não vai para o log.** O console do Windows usa cp1252 e quebra no `StreamHandler`; os
alertas montam o emoji só na hora de enviar (`_format_issues`). Pelo mesmo motivo, `--dry-run` e
`--no-llm` chamam `_use_utf8_stdout`.

**Estado local em `logs/`:** `history.json` ([core/history.py](core/history.py)) guarda 30 dias com
poda automática — o texto dos jornais alimenta a anti-repetição (3 últimos vão ao prompt) e as
métricas alimentam o contexto comparativo ("maior valor em 30 dias"). Só é gravado após envio
bem-sucedido: um jornal que não chegou não pode suprimir as notícias dele amanhã. Migra sozinho o
`last_journal.txt` da versão anterior. `football_teams.json` cacheia os ids resolvidos da API.

**Alerta é sobre resultado, não sobre fonte.** Uma fonte que falha mas é coberta pelo fallback vai
só para o log — a AwesomeAPI responde 429 todo dia e alertar sobre isso treinaria o leitor a
ignorar os alertas. `_fetch_quotes` só reporta o que ficou faltando no fim.

**Promoções vêm de canais do Telegram**, lidos via `https://t.me/s/<canal>` — a prévia web pública,
sem bot e sem token ([promotions.py](scrapers/promotions.py)). O monitoramento de produto por
Buscapé é secundário e opcional; Mercado Livre e Magalu bloqueiam, e o Zoom devolve o mesmo
catálogo do Buscapé.

**Futebol separa jogo de notícia.** A API football-data.org dá adversário, data e placar; o GE dá
notícia, filtrada por `relevance_keywords` ([config.yaml](config/config.yaml)). Sem o token a seção
degrada para só notícias, e é omitida quando não há nada relevante — antes ela repetia as mesmas
manchetes por dias.

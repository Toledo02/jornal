# Plano de melhorias — Jornal Matinal

Levantamento feito em 27/07/2026 sobre o commit `040ae1a` (v2.1), com base em duas execuções reais
(local v2.0 às 20:27 e VPS v2.1 às 05:55) e em testes isolados dos scrapers.

As fases estão em ordem de execução recomendada. Cada item tem o arquivo afetado e um critério de
pronto. Marque `[x]` conforme implementarmos.

---

## Fase 0 — Segurança (fazer antes de qualquer código)

- [ ] **0.1 Revogar o token do bot.** ⚠️ **Ação sua, ainda pendente.** O token aparece em texto puro
  nos logs anteriores a 27/07 e foi exposto em conversa. Gerar novo no BotFather (`/revoke`) e
  atualizar `config/.env` local e da VPS.
- [x] **0.2 Silenciar o `httpx`.** Feito em `setup_logging` ([core/utils.py](core/utils.py)) —
  `httpx` e `httpcore` em WARNING. Verificado no mesmo arquivo de log: a execução anterior à
  correção gravou 18 linhas de requisição e 1 token; a posterior, zero e zero.
- [ ] **0.3 Apagar os logs antigos** que contêm o token, local e na VPS
  (`rm ~/jornal/logs/journal_*.log`). Fazer depois de 0.1.

**Pronto quando:** nenhum arquivo em `logs/` contém `api.telegram.org/bot<token>`.

---

## Fase 1 — Observabilidade

Motivação concreta: os feeds do Omelete e do JovemNerd retornam **404 desde 04/07** e o jornal
continuou chegando bonito por três semanas. A falha existe, mas é invisível.

- [x] **1.1 Notificar falhas pelo Telegram.** Feito. `_collect_issues` / `_format_issues` em
  [main.py](main.py) e `send_alert` em [core/telegram_sender.py](core/telegram_sender.py). O aviso
  vai em texto puro (sem `parse_mode`), porque mensagens de erro carregam `<`, `>` e `&` que
  quebrariam o parser HTML justamente quando algo já deu errado.
- [x] **1.2 Notificar crash do pipeline.** Feito, em três pontos: seções insuficientes, falha no
  envio do jornal, e exceção não tratada em `main()`. O `_notify` nunca propaga exceção — um aviso
  que falha não pode derrubar o pipeline.
- [x] **1.3 Auditar o log de prod.** Feito em 27/07 — resultado abaixo.

**Pronto quando:** derrubar um feed de propósito gera aviso no Telegram no mesmo dia.

### Resultado da auditoria (47 dias de log, 11/06 a 27/07)

| Falha | Desde | Frequência | Item |
|---|---|---|---|
| AwesomeAPI `429 Too Many Requests` | 11/06 | **todo dia, sem exceção** | 5.6 |
| Omelete + JovemNerd `404` | 04/07 | todo dia | 2.2 |
| CheapShark `400 Bad Request` | **10/07** | todo dia | 7.1 |
| Gemini `503 UNAVAILABLE` | 11/06 | ~7 dias esparsos | 9.1 |
| Telegram `can't parse entities` | 11/06 | 4 ocorrências, cessou em 13/06 | Fase 4 |

Três conclusões que mudam o diagnóstico:

1. **A AwesomeAPI nunca funcionou em prod.** 429 em *todas* as execuções desde 11/06. Com 1
   requisição por dia é impossível ser estouro de cota real — é bloqueio por reputação da faixa de
   IP da Oracle Cloud. Ou seja, a fonte primária de cotação está morta há 47 dias e o jornal vem
   silenciosamente do HG Brasil / yfinance.
2. **O 400 do CheapShark não é culpa dos parâmetros.** A v2.1 subiu em 04/07 e o scraper funcionou
   normalmente de 04/07 a 09/07 — o erro começa em **10/07**, sem nenhuma mudança no código.
   Foi o CheapShark que passou a rejeitar a VPS.
3. **Duas APIs distintas bloqueando o mesmo servidor** apontam para causa única: o IP da VPS.
   Confirmar com o teste da Fase 7.1 antes de decidir a correção.

Em 01/07 as 3 tentativas do Gemini falharam e o **jornal de fallback foi realmente enviado** — com
os asteriscos de Markdown e o JSON cru descritos no item 4.4.

---

## Fase 2 — RSS: distribuição e fontes

Contagem real de itens por feed **depois** do corte de `max_items_per_category: 15`:

```
tech         →  8 TechCrunch |  7 Verge      |  0 TabNews
world        →  8 G1         |  7 BBC        |  0 CNN Brasil
pop_culture  →  0 Omelete    |  0 JovemNerd  |  8 IGN  |  7 Polygon
```

- [x] **2.1 Intercalar em round-robin antes de truncar.** Hoje `all_items[:max_items]`
  ([news_rss.py:71](scrapers/news_rss.py#L71)) corta em ordem de chegada, então os dois primeiros
  feeds consomem todos os slots. Coletar 1 item de cada feed por rodada até atingir o limite.
  **Fazer antes do item 2.2** — com 4 feeds em pop_culture, corrigir só as URLs apenas inverte quem
  fica de fora.
- [x] **2.2 Trocar os feeds 404.** Omelete e JovemNerd derrubaram o RSS público (testei `/rss`,
  `/rss.xml`, `/feed`, `/feed/rss`, com e sem `www` — todos 404). Substitutos validados:
  - `https://br.ign.com/feed.xml` → 40 entradas, pt-BR (troca direta do IGN em inglês)
  - `https://www.legiaodosherois.com.br/feed` → 10 entradas
- [x] **2.3 Filtrar por recência.** Nenhum filtro de data hoje: um feed parado há dias contribui
  notícia velha para o jornal *matinal*. Usar `published_parsed` e descartar acima de ~30h.
- [x] **2.4 Remover HTML dos `summary`.** Vão crus para o Gemini
  ([news_rss.py:33](scrapers/news_rss.py#L33)), gastando tokens com tags e entidades.
- [x] **2.5 Revisar `entries_per_feed` / `max_items_per_category`** para o cenário de 4 feeds.

**Pronto quando:** todo feed configurado contribui itens, e nenhum fica em 0 sem aviso.

---

## Fase 3 — Prompt

- [x] **3.1 Escopar a regra anti-repetição.** A regra 11
  ([ai_engine.py:53](core/ai_engine.py#L53)) proíbe repetir notícia de ontem sem dizer a quais
  seções se aplica. Clima e Economia são repetitivos por natureza — há risco real do modelo suprimir
  a cotação do dólar por "já ter saído ontem". Listar explicitamente as seções sujeitas (Mundo,
  Tecnologia, Cultura Pop, Futebol) e isentar Clima, Economia e Neste Dia na História.
- [x] **3.2 Definir o caractere de bullet.** A regra 1 proíbe asteriscos e a regra 4 exige bullet
  points; sem alternativa dada, o modelo usa `*` do Markdown, que com `parse_mode="HTML"` aparece
  literal na mensagem (visível na saída real: `*   Dólar (USD): Compra 5.1288`). Mandar usar `•`.
- [x] **3.3 Travar a lista de seções:** proibir adicionar, renomear, remover ou reordenar.
- [x] **3.4 Corrigir o limite de tamanho.** A regra 8 pede 1500–2500 caracteres; a saída real deu
  3557 com 8 seções, e prod tem 9. Ou ajustar para uma faixa realista, ou impor corte por seção.

**Pronto quando:** nenhum `*` literal na mensagem e as seções saem sempre iguais e na mesma ordem.

---

## Fase 4 — Envio ao Telegram

- [x] **4.1 Split ciente de HTML.** `_split_message` ([core/telegram_sender.py:16](core/telegram_sender.py#L16))
  quebra em 4096 sem noção de marcação: pode cortar no meio de uma tag e invalidar o chunk seguinte.
  Ainda não estourou porque o jornal tem ~3,5k, mas cresceu de 8 para 9 seções.
- [x] **4.2 Remover tags no retry de texto puro.** Quando o envio HTML falha, o mesmo texto é
  reenviado sem `parse_mode` ([telegram_sender.py:78](core/telegram_sender.py#L78)) — então a
  mensagem chega com `<b>` e `<a href>` visíveis. Passar por um strip antes.
- [x] **4.3 Escapar `&`, `<`, `>`** em conteúdo dinâmico. URLs com `&` quebram o parser HTML do
  Telegram e derrubam a mensagem inteira para o fallback.
- [x] **4.4 Corrigir o fallback do LLM.** `_fallback_journal` ([ai_engine.py:89](core/ai_engine.py#L89))
  monta o texto com asteriscos de Markdown mas é enviado como HTML, e ainda despeja JSON cru
  (com `&` nas URLs). Gerar texto puro e enviar com `parse_mode=None` direto.

**Pronto quando:** um jornal de 8k caracteres com links é entregue íntegro em dois chunks.

---

## Fase 5 — Economia

- [x] **5.1 Fallback por ativo.** `_fetch_quotes` ([finance.py:143](scrapers/finance.py#L143))
  retorna assim que `usd_brl.bid` existe, sem verificar os demais ativos. Em prod isso produz
  "Bitcoin: Cotação indisponível": a AwesomeAPI está fora (5.6), o HG Brasil assume mas sua chave
  `free` não devolve o campo `bitcoin`, e como o USD veio preenchido ninguém consulta o yfinance —
  que **tem** `BTC-BRL` e resolveria. Preencher ativo a ativo com a próxima fonte da cadeia.
- [x] **5.2 Formatar números em Python.** Hoje vão floats crus para o modelo (`329233`,
  `175334.45`, `0.0034278`). Formatação numérica é justamente onde LLM erra. Entregar string pronta
  em pt-BR: `R$ 329.233`, `175.334 pts`, `R$ 5,13`.
- [x] **5.3 Calcular a variação do IBOVESPA em Python.** Quando a fonte é o yfinance, o payload traz
  `previous_close` e `last_close` mas nenhum campo de variação — e a saída de prod exibiu
  "variação de -1.52%", ou seja, aritmética feita pelo modelo. Dado financeiro não deve depender
  disso.
- [x] **5.4 Repensar o ARS.** `0.0034278` com 7 casas não informa nada; inverter para `1 BRL = X ARS`
  ou remover.
- [x] **5.5 Validar o scrape do InfoMoney** (`h2 a, h3 a`) — conferir se traz manchete ou menu.
- [x] **5.6 Rebaixar ou remover a AwesomeAPI.** Diagnóstico fechado: `{"code":"QuotaExceeded"}`,
  cota do plano gratuito contada por IP — não é UA nem bloqueio. Persiste desde 11/06 atravessando a
  virada de mês, então o contador daquele IP não reseta sozinho. Como é a primeira da cadeia, todo
  dia se gasta uma requisição inútil e um WARNING antes de cair para o HG Brasil.
  Duas saídas:
  - **Rebaixar para o fim da cadeia ou remover** (recomendado). O HG Brasil sustenta a seção sozinho
    há 47 dias em prod — só não sabíamos.
  - **Obter um `AWESOMEAPI_TOKEN` gratuito**, que vincula a cota à conta em vez do IP. ⚠️ Antes de
    concluir que resolve, conferir o método de autenticação: o código envia o token no header
    `x-api-key` ([finance.py:51](scrapers/finance.py#L51)), mas a AwesomeAPI documenta o parâmetro
    de query `?token=`. Se o nome estiver errado, o token é ignorado e o 429 continua.

- [x] **5.7 Reavaliar a ordem da cadeia por confiabilidade real.** A ordem atual assume que a
  AwesomeAPI é a melhor fonte, mas em prod ela nunca respondeu. E o HG Brasil é chamado com
  `key=free` ([finance.py:61](scrapers/finance.py#L61)) — chave de demonstração, também sujeita a
  limite, ou seja, o mesmo tipo de fragilidade que já nos mordeu. O yfinance é o único sem cota nem
  chave. Considerar promovê-lo, aceitando que é mais lento (5 chamadas sequenciais, ver 5.8).

- [x] **5.8 Paralelizar o yfinance.** `_fetch_yfinance_sync` busca ticker por ticker em sequência
  ([finance.py:105](scrapers/finance.py#L105)); `yf.download` faz em lote.

---

## Fase 6 — Futebol

- [ ] **6.1 Substituir o scraping do GE por uma API.** Os campos se chamam `next_match`/`last_match`
  mas o scraper só extrai blocos de texto solto: nas duas execuções a Seleção saiu como "não há
  informações sobre jogos". Avaliar football-data.org ou api-futebol.com.br.
- [x] **6.2 Paliativo imediato:** remover o fallback `match_items: "div, section"`
  ([config.yaml:57](config/config.yaml#L57)), que casa com todas as divs da página e injeta lixo no
  payload.
- [x] **6.3 Atualizar o slug do Athletico.** O log mostra dois redirects 301 encadeados:
  `/futebol/times/atletico-pr/` → `/pr/futebol/times/atletico-pr/` → `/pr/futebol/times/athletico-pr/`.
  Apontar direto para a URL final.
- [x] **6.4 Passar datas explícitas.** O modelo escreveu "venceu o Internacional ontem" inferindo de
  texto sem data — risco de alucinação temporal.

---

## Fase 7 — Gaming

- [x] **7.1 O CheapShark exige User-Agent descritivo.** Causa confirmada em 27/07 pelo corpo da
  resposta 400 (servida por Cloudflare):
  ```json
  {"error": "Missing or generic User-Agent header detected. Please identify your client
   with a descriptive User-Agent (e.g., 'MyApp/1.0 (contact@example.com)')."}
  ```
  O UA atual, `Mozilla/5.0 (compatible; DailyJournalBot/2.0)`
  ([core/utils.py:13](core/utils.py#L13)), se disfarça de navegador — é justamente o padrão que a
  regra deles rejeita. Bate com a linha do tempo: funcionou até 09/07, quebrou em 10/07, quando eles
  ligaram a proteção. **Não é bloqueio de IP.**

  Correção: trocar `USER_AGENT` por algo identificável, no formato que eles pedem —
  `JornalMatinal/2.1 (+https://github.com/Toledo02/Jornal)`. Prefira a URL do repositório ao e-mail
  pessoal: cumpre a exigência de contato sem expor endereço pessoal a todo site que o agente acessa.

  **Validado na VPS em 27/07: esse UA retorna `200`.**

  ✅ **Aplicado** em [core/utils.py](core/utils.py). Confirmar na VPS após o deploy.

- [x] **7.1a Usar UA diferente para APIs e para scraping de HTML.** Feito. `BROWSER_HEADERS` em
  [core/utils.py](core/utils.py), aplicado nos quatro alvos de HTML: InfoMoney
  ([finance.py](scrapers/finance.py)), GE Globo ([football.py](scrapers/football.py)), Mercado Livre
  e Buscapé ([promotions.py](scrapers/promotions.py)), GitHub Trending
  ([gaming.py](scrapers/gaming.py)). APIs e feeds RSS seguem com o UA descritivo.
  Testado localmente: gaming, finance e football continuam `status=ok`.
  **Falta validar na VPS** — é lá que o comportamento difere.

- [x] **7.1b AwesomeAPI tem causa distinta.** Testada com o mesmo UA descritivo: continua `429`, e o
  corpo é claro — `{"code":"QuotaExceeded"}`. Não é User-Agent nem bloqueio: é cota de plano
  gratuito, contada por IP. Tratado no item 5.6.

- [ ] **7.1c Plano B, só se o 7.1 não bastar:** substituir o CheapShark. Candidatas alinhadas com a
  seção "Ofertas & Games Grátis" (validar cada uma **na VPS**):
  - GamerPower (`gamerpower.com/api/giveaways`) — jogos grátis e giveaways, exatamente o tema
  - Epic Games free games (endpoint público de `freeGamesPromotions`)
  - IsThereAnyDeal (exige chave gratuita)
- [x] **7.2 Filtrar shovelware.** `sortBy=Savings` seleciona o maior desconto, que é justamente o
  jogo que ninguém compra. Retorno real: das 7 ofertas, só o Destiny 2 prestava — o resto era
  *Ship Graveyard Simulator*, *3 Stars of Destiny*, *Asguaard*. O CheapShark aceita `steamRating` e
  `metacritic`; `steamRating=70` cortaria quase tudo isso.
- [x] **7.3 Mover `github_trending` para fora do gaming.** Desde a v2.1 o scraper de gaming é só
  ofertas, mas os repos continuam nele ([gaming.py:129](scrapers/gaming.py#L129)) e são consumidos
  pela seção de Tecnologia.
- [x] **7.4 Corrigir o nome do repo.** Sai como `permissionlesstech /bitchat`, com espaço antes da
  barra ([gaming.py:102](scrapers/gaming.py#L102)).

---

## Fase 8 — DX e manutenção

- [x] **8.1 `--dry-run`** — imprime o jornal no stdout sem enviar. Hoje a única forma de validar é
  esperar o cron ou disparar de verdade no Telegram.
- [x] **8.2 `--only weather,finance`** — roda um subconjunto de scrapers.
- [x] **8.3 Pinar versões** no `requirements.txt`, hoje todo sem versão. Um release quebrado de
  `feedparser` ou `yfinance` derruba o cron num dia qualquer.
- [x] **8.4 Testes dos parsers puros:** `_parse_price`, `_split_message`, `_normalize_title`,
  `format_date_pt_br` e o round-robin da Fase 2.1. São determinísticos e sem rede.
- [x] **8.5 Renomear `openai_*` → `llm_*`** em `Settings` ([config/settings.py](config/settings.py))
  e atualizar o `.env.example`, que ainda manda `OPENAI_MODEL=gpt-4o-mini` — quem seguir o exemplo
  do repo cai direto no fallback.
- [x] **8.6 Timezone dos logs.** `setup_logging` usa `datetime.now()` local
  ([core/utils.py:40](core/utils.py#L40)) enquanto o `ai_engine` fixa `America/Sao_Paulo`; numa VPS
  em UTC o arquivo de log vira o dia antes do jornal.
- [x] **8.7 Atualizar o README**, que ainda descreve setup da OpenAI e a estrutura da v2.0.
- [x] **8.8 Resolver a seção de promoções.** `product_names: []` está vazio
  ([config.yaml:62](config/config.yaml#L62)), então a seção 🛒 aparece todo dia dizendo que não há
  nada. Popular a lista ou remover a seção do prompt.
- [x] **8.9 Reavaliar `min_sections_for_send: 1`** — com 8 scrapers, envia um jornal de uma seção só.

---

## Fase 9 — Resiliência do LLM

- [x] **9.1 Melhorar a estratégia de retry do Gemini.** O log registra `503 UNAVAILABLE` em ~7 dias
  distintos, e em 01/07 as três tentativas falharam dentro de ~60s, resultando no envio do jornal de
  fallback. O `retry_delay` é fixo em 10s ([ai_engine.py:129](core/ai_engine.py#L129)): curto demais
  para sobrecarga de modelo, que costuma durar minutos. Usar backoff exponencial (10s → 30s → 90s).
- [x] **9.2 Fallback de modelo.** Antes de desistir e cair no template, tentar um modelo alternativo
  (ex.: `gemini-2.0-flash`). Um 503 é do modelo específico, não da conta.
- [x] **9.3 Não repetir retry em erro permanente.** Hoje um 400 (modelo inexistente, chave inválida)
  gasta os mesmos 3 ciclos com 20s de espera de um 503 transitório. Distinguir 4xx de 5xx.
- [x] **9.4 `time.sleep` bloqueante.** `generate_journal` é síncrona mas chamada de dentro do loop
  async ([main.py:105](main.py#L105)); o sleep trava o event loop. Inofensivo hoje (os scrapers já
  terminaram), mas quebra se algo passar a rodar em paralelo.

---

## Notas de contexto

- **Prod ≠ local.** CheapShark e AwesomeAPI recusam a VPS e atendem a sua máquina normalmente.
  Toda validação de scraper que dependa de rede precisa acontecer *na VPS*, não só localmente.
- **Ler o corpo do erro antes de teorizar.** O 400 do CheapShark foi atribuído a bloqueio de IP com
  base em três testes de User-Agent que davam o mesmo código — mas as três variantes testadas eram
  igualmente "genéricas" pela regra deles, então o resultado uniforme não significava nada. O corpo
  da resposta explicava a causa em uma frase. Custa 30 segundos e evita reescrever o scraper à toa.
- **Falhas parciais são invisíveis hoje.** `status="partial"` só aparece no log. Foi assim que o 404
  dos feeds brasileiros passou três semanas despercebido, e o 429 da AwesomeAPI, 47 dias — é o que a
  Fase 1 resolve.
- **A cadeia de fallback funcionou bem demais.** Ela salvou o jornal todos os dias, e por isso
  escondeu que a fonte primária estava morta. Resiliência sem observabilidade vira dívida silenciosa:
  por isso a Fase 1 vem antes de qualquer correção de conteúdo.

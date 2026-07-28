# Jornal Matinal — Agente de Clipping Pessoal

Agente Python config-driven que coleta dados de múltiplas fontes, consolida via LLM (Google Gemini)
e envia um jornal matinal personalizado pelo Telegram.

## Estrutura

```
Jornal/
├── config/
│   ├── .env              # Segredos (não versionado)
│   ├── .env.example      # Template de variáveis
│   ├── config.yaml       # Fontes, URLs, times, produtos
│   └── settings.py       # Carregador de config
├── scrapers/             # Coletores de dados
├── core/                 # IA e Telegram
├── tests/                # Testes das funções puras
├── main.py               # Orquestrador
└── PLANO.md              # Backlog de melhorias com diagnóstico
```

## Requisitos

- Python 3.10+
- VPS Linux (ex.: Oracle Cloud Infrastructure) ou máquina local

## Instalação

```bash
cd /path/to/Jornal
python -m venv .venv
source .venv/bin/activate   # Linux/macOS
# .venv\Scripts\activate    # Windows

pip install -r requirements.txt
cp config/.env.example config/.env
```

Edite `config/.env`:

| Variável | Descrição |
|----------|-----------|
| `LLM_API_KEY` | Chave do Gemini ([AI Studio](https://aistudio.google.com/apikey)) |
| `LLM_MODEL` | Modelo (padrão: `gemini-2.5-flash`) |
| `LLM_FALLBACK_MODELS` | Modelos tentados se o principal der 503 |
| `TELEGRAM_BOT_TOKEN` | Token do BotFather |
| `TELEGRAM_CHAT_ID` | ID do chat de destino |
| `AWESOMEAPI_TOKEN` | Opcional; a AwesomeAPI é a última fonte da cadeia de cotações |

Os nomes `OPENAI_*` continuam aceitos por compatibilidade (herança de quando o projeto usava
OpenAI), mas os `LLM_*` têm precedência.

## Execução

```bash
python main.py                      # pipeline completo: coleta, gera e envia
python main.py --dry-run            # gera e imprime no terminal, sem enviar
python main.py --no-llm             # só coleta e imprime o payload (não gasta requisição)
python main.py --only weather,finance   # roda um subconjunto de scrapers
```

Testes:

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

Logs diários em `logs/journal_YYYYMMDD.log` (data no fuso de São Paulo).

## Configuração dinâmica (`config/config.yaml`)

**Regra de ouro:** URLs, feeds, seletores, times e produtos ficam no YAML — não no código.

```yaml
rss_feeds:
  tech:
    - "https://techcrunch.com/feed/"
    - "https://seu-novo-feed.com/rss"   # basta adicionar aqui

weather:
  city: "Curitiba"
  lat: -25.4284
  lon: -49.2733

promotions:
  product_names:
    - "Headset XYZ"     # busca preço no Mercado Livre e Buscapé
```

Os feeds são intercalados em round-robin antes do corte por `max_items_per_category`, então cada
fonte contribui proporcionalmente. Entradas mais velhas que `max_age_hours` são descartadas.

## Resiliência

| Cenário | Comportamento |
|---------|---------------|
| Um scraper falha | Jornal enviado com as demais seções, **e um alerta no Telegram** |
| Uma fonte de cotação falha | Os ativos faltantes são buscados na fonte seguinte, um a um |
| Menos de `min_sections_for_send` seções | Pipeline aborta e avisa pelo Telegram |
| LLM retorna 503 | Backoff 10s → 30s → 90s, depois tenta os modelos de reserva |
| LLM falha de vez | Fallback em texto puro |
| Telegram rejeita o HTML | Reenvia sem marcação, com as tags removidas |
| Mensagem acima de 4096 chars | Dividida sem cortar tags no meio |

Falhas parciais **sempre** geram alerta. Antes elas ficavam só no log, e foi assim que dois feeds
quebrados passaram três semanas despercebidos.

## Módulos de dados

1. **Clima** — Open-Meteo
2. **Economia** — HG Brasil → yfinance → AwesomeAPI, preenchendo ativo a ativo
3. **Tech** — RSS
4. **Mundo** — RSS (LLM seleciona 3 fatos globais)
5. **Cultura Pop** — RSS
6. **GitHub Trending** — scraping
7. **Gaming** — CheapShark, filtrado por nota da Steam
8. **Futebol** — scraping GE Globo Esporte
9. **Promoções** — monitoramento de preços com histórico local

## Deploy na VPS (cron)

```bash
crontab -e
```

```
55 5 * * * cd /home/ubuntu/jornal && /home/ubuntu/jornal/.venv/bin/python main.py >> /home/ubuntu/jornal/logs/cron.log 2>&1
```

Certifique-se de que `config/.env` existe na VPS.

> **Atenção:** a VPS recebe respostas diferentes das da sua máquina. O CheapShark exige
> User-Agent descritivo e a AwesomeAPI aplica cota por IP. Valide scrapers **na VPS**, não só
> localmente — use `python main.py --no-llm --only gaming`.

# Jornal Matinal — Agente de Clipping Pessoal

Agente Python config-driven que coleta dados de múltiplas fontes, consolida via LLM e envia um jornal matinal personalizado pelo Telegram.

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
├── main.py               # Orquestrador
└── requirements.txt
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

Edite `config/.env` com suas chaves:

| Variável | Descrição |
|----------|-----------|
| `OPENAI_API_KEY` | Chave da API OpenAI |
| `OPENAI_MODEL` | Modelo (ex.: `gpt-4o-mini`) |
| `TELEGRAM_BOT_TOKEN` | Token do BotFather |
| `TELEGRAM_CHAT_ID` | ID do chat de destino |
| `AWESOMEAPI_TOKEN` | Opcional, melhora limites da cotação |

## Configuração dinâmica (`config/config.yaml`)

**Regra de ouro:** URLs, feeds, times e produtos ficam no YAML — não no código.

### Adicionar feed RSS de tecnologia

```yaml
rss_feeds:
  tech:
    - "https://techcrunch.com/feed/"
    - "https://seu-novo-feed.com/rss"   # basta adicionar aqui
```

### Monitorar preço de produto

```yaml
promotions:
  products:
    - name: "Headset XYZ"
      url: "https://loja.com/produto"
      price_selector: ".preco-vista"
```

### Alterar cidade do clima

```yaml
weather:
  city: "Curitiba"
  lat: -25.4284
  lon: -49.2733
```

## Execução

```bash
python main.py
```

O pipeline:

1. Carrega config e secrets
2. Executa scrapers em paralelo (com timeout individual)
3. Continua mesmo se algum scraper falhar
4. Gera jornal via LLM (prompt em inglês, resposta em pt-BR)
5. Envia ao Telegram (divide mensagens > 4096 caracteres)

Logs diários em `logs/journal_YYYYMMDD.log`.

## Deploy na VPS OCI (cron)

```bash
# Exemplo: todo dia às 07:00 (horário do servidor)
crontab -e
```

```
0 7 * * * cd /home/ubuntu/Jornal && /home/ubuntu/Jornal/.venv/bin/python main.py >> /home/ubuntu/Jornal/logs/cron.log 2>&1
```

Certifique-se de que `config/.env` existe na VPS com as chaves corretas.

## Resiliência

| Cenário | Comportamento |
|---------|---------------|
| Um scraper falha | Jornal enviado com as demais seções |
| Todos falham | Pipeline aborta (exit 1) |
| LLM falha | Fallback com template simples |
| Telegram falha | Exit 1, erro registrado no log |

## Módulos de dados

1. **Clima** — Open-Meteo (gratuito)
2. **Economia** — AwesomeAPI + scraping configurável
3. **Tech** — RSS + GitHub Trending
4. **Mundo** — RSS (LLM filtra 3 fatos globais)
5. **Gaming** — CheapShark + RSS
6. **Futebol** — Scraping GE Globo Esporte
7. **Promoções** — Monitoramento de preços com histórico local

## Desenvolvimento

Para testar scrapers individualmente:

```python
from config.settings import load_settings
from scrapers import weather

settings = load_settings()
print(weather.fetch(settings))
```

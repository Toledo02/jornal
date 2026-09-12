# Jornal Matinal — Agente de Clipping Pessoal

Agente Python config-driven que coleta dados de múltiplas fontes, consolida via LLM (Google Gemini)
e publica um jornal matinal personalizado como página web. O Telegram recebe só o aviso com o link.

## Estrutura

```
Jornal/
├── config/
│   ├── .env              # Segredos (não versionado)
│   ├── .env.example      # Template de variáveis
│   ├── config.yaml       # Fontes, URLs, times, produtos
│   └── settings.py       # Carregador de config
├── scrapers/             # Coletores de dados
├── core/                 # IA, renderização da página e Telegram
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
| `LLM_MODEL` | Modelo (padrão: `gemini-3.6-flash`; os `pro` dão 429 no plano gratuito) |
| `LLM_FALLBACK_MODELS` | Modelos tentados se o principal der 503 |
| `TELEGRAM_BOT_TOKEN` | Token do BotFather |
| `TELEGRAM_CHAT_ID` | ID do chat de destino (recebe o aviso com o link) |
| `JOURNAL_HTML_PATH` | Onde a página é escrita (vazio: `<projeto>/site/index.html`) |
| `JOURNAL_URL` | Endereço da página, usado no aviso do Telegram |
| `FOOTBALL_DATA_TOKEN` | Jogos e placares ([football-data.org](https://www.football-data.org/client/register), grátis) |
| `AWESOMEAPI_TOKEN` | Opcional; a AwesomeAPI é a última fonte da cadeia de cotações |

Os nomes `OPENAI_*` continuam aceitos por compatibilidade (herança de quando o projeto usava
OpenAI), mas os `LLM_*` têm precedência.

## Execução

```bash
python main.py                      # pipeline completo: coleta, gera, publica e avisa
python main.py --dry-run            # gera e escreve logs/preview.html, sem publicar nem avisar
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
```

Os feeds são intercalados em round-robin antes do corte por `max_items_per_category`, então cada
fonte contribui proporcionalmente. Entradas mais velhas que `max_age_hours` são descartadas.

## Resiliência

| Cenário | Comportamento |
|---------|---------------|
| Um scraper falha | Página publicada com as demais seções, **e um alerta no Telegram** |
| Uma fonte de cotação falha | Os ativos faltantes são buscados na fonte seguinte, um a um |
| Menos de `min_sections_for_send` seções | Pipeline aborta e avisa pelo Telegram |
| LLM retorna 503 | Backoff 10s → 30s → 90s, depois tenta os modelos de reserva |
| LLM devolve JSON inválido | Conta como falha e entra no mesmo backoff |
| LLM falha de vez | Página montada direto do payload, avisando que saiu em modo fallback |
| Aviso do Telegram falha | Só vai para o log: a página já está publicada |

Falhas parciais geram alerta **quando faltam dados no jornal final**. Uma fonte que falha mas é
coberta pelo fallback fica só no log: alertar todo dia sobre algo que não afeta o resultado
treinaria você a ignorar os alertas.

## Histórico

`logs/history.json` guarda 30 dias (`history.retention_days`), podados a cada gravação. Serve a
três coisas: os 3 jornais mais recentes vão ao prompt para a regra anti-repetição, as métricas
numéricas alimentam o contexto comparativo — `R$ 5,60 (+0,10%) — maior valor em 30 dias` — e os
títulos já publicados alimentam o rodízio de jogos (`history.repeat_window_days`), que é o que
impede a mesma oferta de voltar todo dia.

## Módulos de dados

1. **Clima** — Open-Meteo, entregue por tópico (agora, hoje, máx/mín, chuva, sol, vento, UV), com
   emoji de condição montado em Python
2. **Economia** — HG Brasil → yfinance → AwesomeAPI, preenchendo ativo a ativo
3. **Investimentos** — séries do Banco Central (SGS): Selic, CDI, IPCA e poupança, com juro real
   calculado em Python e uma "ideia do dia" rotacionada pelo histórico
4. **Tech** — RSS
5. **Mundo** — RSS (o LLM seleciona os fatos globais, até `presentation.items_per_news_section`)
6. **Curitiba & Paraná** — RSS local (Gazeta do Povo PR, Tribuna PR)
7. **Cultura Pop** — RSS
8. **GitHub Trending** — scraping
9. **Gaming** — giveaways da GamerPower + ofertas do CheapShark, filtradas por nota, volume de
   avaliações e faixa de preço, com rodízio entre dias
10. **Futebol** — jogos via football-data.org + notícias filtradas do GE

## Deploy na VPS (cron)

```bash
crontab -e
```

```
55 5 * * * cd /home/ubuntu/jornal && /home/ubuntu/jornal/.venv/bin/python main.py >> /home/ubuntu/jornal/logs/cron.log 2>&1
```

Certifique-se de que `config/.env` existe na VPS.

### Servindo a página (nginx, só na tailnet)

O job escreve um `index.html` estático; o nginx só o serve. Não há processo novo rodando.

```bash
sudo mkdir -p /var/www/jornal
sudo chown ubuntu:www-data /var/www/jornal && sudo chmod 755 /var/www/jornal
```

O diretório fica fora de `/home/ubuntu` porque o home é `0750` e o `www-data` não o atravessa —
servir de dentro do repo daria 403. Site em `/etc/nginx/sites-available/jornal`:

```nginx
server {
    listen 100.74.153.56:8446 ssl;          # IP da tailnet, não a interface pública
    server_name oci-vps.taildcf79e.ts.net;

    ssl_certificate     /etc/ssl/tailscale/oci-vps.taildcf79e.ts.net.crt;
    ssl_certificate_key /etc/ssl/tailscale/oci-vps.taildcf79e.ts.net.key;

    root /var/www/jornal;
    index index.html;
    charset utf-8;
    add_header Cache-Control "no-store";     # senão o celular abre o jornal de ontem

    location / { try_files $uri $uri/ =404; }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/jornal /etc/nginx/sites-enabled/jornal
sudo nginx -t && sudo systemctl reload nginx
```

E no `config/.env` da VPS:

```
JOURNAL_HTML_PATH=/var/www/jornal/index.html
JOURNAL_URL=https://oci-vps.taildcf79e.ts.net:8446/
```

> **Atenção:** a VPS recebe respostas diferentes das da sua máquina. O CheapShark exige
> User-Agent descritivo e a AwesomeAPI aplica cota por IP. Valide scrapers **na VPS**, não só
> localmente — use `python main.py --no-llm --only gaming`.

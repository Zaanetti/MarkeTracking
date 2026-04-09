# MarkeTracking

Aplicacao para coleta, leitura e tratamento de cupons fiscais, com foco em extracao de dados de compra e acompanhamento de precos.

## Status atual

Este repositorio agora possui o scaffold inicial do projeto:

- app web em `FastAPI`
- templates server-rendered para a interface inicial
- modelagem base do banco em `SQLAlchemy`
- estrutura pronta para parsers, services e workers
- `Dockerfile` e `docker-compose.yml`
- `PostgreSQL` e `MinIO` no ambiente local

A logica de leitura de QRCode, OCR e scraping ainda nao foi implementada. O objetivo desta etapa foi deixar a fundacao pronta.

## Stack definida

- Front-end: FastAPI + Jinja templates + CSS
- Back-end: Python + FastAPI
- Banco: PostgreSQL
- Storage: MinIO (compativel com S3)
- Infra local: Docker Compose

## Estrutura do projeto

```text
src/marketracking/
  api/         # rotas de API
  core/        # configuracoes centrais
  db/          # sessao e modelos
  parsers/     # parsers por origem/site
  schemas/     # contratos Pydantic
  services/    # servicos de negocio
  static/      # css e assets
  templates/   # html server-rendered
  web/         # rotas de paginas
  workers/     # processamento assincrono futuro
```

## Como subir o ambiente

1. Copie o arquivo de ambiente:

```powershell
Copy-Item .env.example .env
```

2. Suba os containers:

```powershell
docker compose up --build
```

3. Aplique a migration inicial:

```powershell
docker compose run --rm app alembic upgrade head
```

4. Acesse os servicos:

- App: [http://localhost:8000](http://localhost:8000)
- Docs da API: [http://localhost:8000/docs](http://localhost:8000/docs)
- MinIO Console: [http://localhost:9001](http://localhost:9001)

Credenciais padrao do MinIO no exemplo:

- usuario: `minioadmin`
- senha: `minioadmin`

## Rotas iniciais

- `GET /` interface inicial do projeto
- `GET /api/health` healthcheck da aplicacao

## Proximos passos

1. Implementar a pipeline de submissao de imagem e entrada manual.
2. Integrar storage para gravar o arquivo original.
3. Ler QRCode primeiro e OCR como fallback.
4. Consultar a pagina do cupom e extrair os dados normalizados.
5. Persistir recibos e itens no banco.

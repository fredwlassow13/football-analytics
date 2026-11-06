# Football Analytics

Base inicial do monólito modular em Python 3.13 e PostgreSQL 17.

## Docker (API + banco)

```sh
docker compose up --build -d
```

O Compose aguarda o PostgreSQL e aplica as migrações antes de iniciar a API.

- API: http://localhost:8000/health
- Banco disponível: http://localhost:8000/health/ready
- OpenAPI: http://localhost:8000/docs

```sh
docker compose logs backend migrate
docker compose down
```

O volume preserva os dados ao parar os serviços. Credenciais locais de desenvolvimento
estão no Compose; devem ser substituídas antes de qualquer implantação externa.

O PostgreSQL fica acessível no Mac em `localhost:5433`.

## Desenvolvimento no VS Code

Na raiz desta pasta:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -c constraints.txt -e '.[dev]'
cp .env.example .env
uvicorn football_analytics.main:app --reload
```

Para executar a API localmente, mantenha apenas o banco no Docker:
`docker compose up -d db`. Execute `alembic upgrade head` para preparar o banco.
Se a API Docker já estiver rodando, pare-a com `docker compose stop backend`
antes de executar Uvicorn localmente. A porta 8000 deve estar livre.
Selecione `.venv/bin/python` em **Python: Select Interpreter** no VS Code.

## Qualidade

```sh
ruff check .
ruff format --check .
mypy src
pytest
```

## Escopo atual

API com health checks e consultas de competições/temporadas persistidas; client StatsBomb;
ingestão auditada de catálogo e equipes; tabelas de partidas e mapeamentos externos.
A normalização de partidas está implementada e testada, mas sua persistência ainda não.
Não há métricas analíticas nem endpoints de partidas/equipes nesta etapa.
SQLAlchemy está sendo usado com SQL explícito; ainda não há modelos ORM registrados.
As migrações são manuais. `--autogenerate` é bloqueado enquanto o metadata estiver vazio.

## Primeira integração: StatsBomb

Consulte `/api/v1/providers/statsbomb/competitions` em `/docs`.
Aceita `country`, `limit` (1–100) e `offset`. O catálogo não exige chave de API.
Esta consulta usa a fonte externa e ainda não grava no banco.
Detalhes e atribuição: [StatsBomb](docs/integrations/statsbomb.md).

## Ingestão e consultas persistidas

A primeira carga seleciona Copa do Mundo 2022. Execute com os containers iniciados:

```sh
docker compose exec backend python -m football_analytics.ingestion.catalog --competition 43 --season 106
```

Consulte `/api/v1/competitions` e copie o UUID retornado para consultar
`/api/v1/competitions/{competition_id}/seasons`. Esses endpoints leem o banco.
A prévia `/api/v1/providers/statsbomb/competitions` continua consultando a fonte externa.
Detalhes de auditoria, idempotência e testes: [pipeline](docs/ingestion/catalog.md).

## Carga de seleções da Copa de 2022

Após aplicar as migrações, com `.venv` ativo:

```sh
python -m football_analytics.ingestion.world_cup_teams
```

O pipeline preserva o JSON das partidas em raw e cadastra equipes e mapeamentos em core.
Reexecutar não duplica equipes. Novos payloads e execuções são preservados na auditoria.
A seleção 43/106 é fixa nesta primeira versão; não existe vínculo equipe/temporada ainda.
Para executar dentro do Docker após alterar arquivos, reconstrua a imagem com
`docker compose up --build -d` antes de usar `docker compose exec backend python -m ...`.

## Testes com PostgreSQL

Os testes de banco são ignorados quando `TEST_DATABASE_URL` não está definido.
Crie um banco separado uma única vez:

```sh
docker compose exec db createdb -U football football_test
```

Se ele já existir, pule esse comando. Aplique as migrações e execute a suíte completa:

```sh
DATABASE_URL=postgresql+psycopg://football:football_local@localhost:5433/football_test alembic upgrade head
TEST_DATABASE_URL=postgresql+psycopg://football:football_local@localhost:5433/football_test pytest -q
```

Os testes de pipelines preservam suas auditorias e fixtures no banco separado.
Nunca use o banco principal como destino desses testes. Há um aviso de depreciação
na integração TestClient/HTTPX das versões atuais; a migração dessa dependência é futura.

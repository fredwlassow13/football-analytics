# Carga do catálogo StatsBomb

A carga inicial seleciona FIFA World Cup 2022 (StatsBomb competição 43, temporada 106).

```sh
docker compose exec backend python -m football_analytics.ingestion.catalog --competition 43 --season 106
```

Cada execução preserva o catálogo completo em `raw.provider_payloads`: JSONB, texto original,
SHA-256 do corpo retornado pelo HTTPX, URL, data de coleta e ID da execução. Respostas com
JSON inválido preservam o texto, com payload JSON nulo. Falhas HTTP/transporte são registradas
na execução e em `raw.ingestion_errors`; não existe payload quando a consulta falha.

Somente o par selecionado é normalizado em `core.competitions` e `core.seasons`.
UUIDs internos são independentes dos IDs StatsBomb. O mapeamento de temporada usa
`competition_id:season_id`, pois uma temporada externa pode aparecer em várias competições.
O campo country_name mantém o rótulo do fornecedor (inclusive International); a dimensão
countries, com códigos oficiais, será adicionada antes da integração de outros fornecedores.

O lock transacional PostgreSQL serializa os escritores deste pipeline. Constraints e FKs
protegem a integridade. Reexecutar preserva os IDs internos e adiciona apenas a auditoria raw.
Mudanças no nome, país ou gênero do cadastro existente falham para revisão; o payload novo
permanece disponível. A transação core é revertida integralmente em caso de falha.
Uma execução interrompida abruptamente pode permanecer running; reconciliação será futura.
As migrações atuais são explícitas em SQL; autogenerate está bloqueado até registrar os modelos
no metadata do SQLAlchemy, para evitar propostas de remoção de tabelas existentes.

## API do banco

- GET /api/v1/competitions — IDs internos UUID, paginação limit/offset.
- GET /api/v1/competitions/{competition_id}/seasons — temporadas do UUID interno.

Essas consultas não chamam StatsBomb. O endpoint providers permanece uma prévia externa.
Partidas e jogadores ainda não fazem parte desta carga. Equipes são importadas pelo
pipeline separado `python -m football_analytics.ingestion.world_cup_teams`.

## Testes

Os testes unitários simulam a fonte. O teste de integração exige banco separado, já migrado:

```sh
DATABASE_URL=postgresql+psycopg://football:football_local@localhost:5433/football_test alembic upgrade head
TEST_DATABASE_URL=postgresql+psycopg://football:football_local@localhost:5433/football_test pytest
```

O teste cria entidades identificadas por IDs aleatórios, testa repetição e falhas, sem apagar
registros. CI prepara seu PostgreSQL isolado e executa essa mesma validação.

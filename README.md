# Football Analytics

Plataforma de análise de futebol em Python 3.13, FastAPI e PostgreSQL 17,
organizada como monólito modular. O desenvolvimento começa pela Copa do Mundo
2022, aprofundando uma partida antes de replicar as análises.

## Estado atual

| Área | Implementado |
| --- | --- |
| Infraestrutura | Docker Compose, SQLAlchemy, Alembic, Ruff, MyPy, Pytest e CI |
| Fonte | StatsBomb Open Data, com adapters independentes do formato interno |
| Copa 2022 | Competições/temporadas, 32 equipes e 64 partidas importadas no ambiente validado |
| Final de 2022 | 50 jogadores listados, escalações e classificação de titulares |
| Participação temporal | Relógio por período, entradas/saídas, intervalos e conferência coletiva |
| Armazenamento analítico | Cálculos identificados por versão e hashes das fontes, resultados e intervalos |
| API | Competições, temporadas, partidas e participação de um cálculo explícito |
| Auditoria | Execuções, erros e respostas originais com SHA-256 |

Os dados são carregados por comandos explícitos: não acompanham o código no Git.
O fluxo temporal da final foi conferido com 50 resultados, 35 jogadores com
intervalos, 15 com duração ausente e 36 intervalos. Esse recorte não representa
uma validação de todos os casos possíveis de futebol.

A primeira métrica é `elapsed_on_field_ms`, fórmula `1.0.0`. Ela inclui acréscimos
e paralisações dentro dos períodos, desconta ausências temporárias identificadas
e exclui intervalos entre períodos e disputa de pênaltis. **Não equivale a tempo de
bola em jogo nem a minutos estatísticos convencionais usados em indicadores por 90.**
A definição completa está em [participação temporal](docs/analytics/elapsed-on-field.md).

## Executar com Docker

Na raiz do repositório, com Docker em execução:

```sh
docker compose up --build -d
```

O Compose aguarda o PostgreSQL e aplica as migrações antes de iniciar a API.

- Processo: <http://localhost:8000/health>
- Disponibilidade do banco: <http://localhost:8000/health/ready>
- Documentação OpenAPI: <http://localhost:8000/docs>
- PostgreSQL no host: `localhost:5433`; entre containers: `db:5432`.

```sh
docker compose logs backend migrate
docker compose down
```

O volume do PostgreSQL preserva os dados ao parar os serviços. As credenciais
incluídas no Compose são de desenvolvimento local.

## Desenvolvimento no VS Code

Abra a pasta `football-analytics` como raiz do workspace:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -c constraints.txt -e '.[dev]'
```

Se ainda não houver `.env`, copie `.env.example` para `.env`. O exemplo usa o
PostgreSQL local na porta 5433. Nunca versione `.env` ou `.venv`.

Para executar a API fora do Docker:

```sh
docker compose up -d db
docker compose stop backend
alembic upgrade head
uvicorn football_analytics.main:app --reload
```

Selecione `.venv/bin/python` em **Python: Select Interpreter**. Se um novo terminal
não encontrar Ruff, MyPy ou Pytest, ative novamente o ambiente virtual.

## Reproduzir o recorte validado

Com `.venv` ativo, banco disponível e migrações aplicadas, execute nesta ordem:

```sh
python -m football_analytics.ingestion.catalog --competition 43 --season 106
python -m football_analytics.ingestion.world_cup_teams
python -m football_analytics.ingestion.world_cup_matches
python -m football_analytics.ingestion.final_players
python -m football_analytics.ingestion.match_lineups --match-id 3869685
python -m football_analytics.ingestion.match_participation_job --match-id 3869685
```

`43`, `106` e `3869685` são IDs externos StatsBomb. UUIDs internos são gerados pela
aplicação e variam entre instalações. O comando do cálculo informa seu UUID.
Equipes e partidas ainda usam o recorte fixo Copa 2022; `final_players` seleciona a
final explicitamente. Escalações e participação recebem `--match-id`, mas exigem
os cadastros e mapeamentos prévios. Não há execução em lote de todo o catálogo.

Para executar esses módulos no container, use
`docker compose exec backend python -m ...`. Após alterações no código ou nas
migrações, reconstrua a imagem com `docker compose up --build -d`.

As cargas repetidas preservam novas execuções e payloads em `raw` sem duplicar os
cadastros. O cálculo reutiliza o mesmo resultado se partida, métrica, versão e
hashes das duas fontes forem iguais. Fonte ou fórmula diferente identifica outro
cálculo, preservando o anterior.

Detalhes: [catálogo](docs/ingestion/catalog.md) e
[pipeline de participação](docs/ingestion/participation.md).

## Consultar a API

| Endpoint | Origem e comportamento |
| --- | --- |
| `GET /api/v1/providers/statsbomb/competitions` | Prévia externa; acessa StatsBomb; `country`, `limit`, `offset` |
| `GET /api/v1/competitions` | Banco; paginação `limit`/`offset` |
| `GET /api/v1/competitions/{id}/seasons` | Banco; UUID interno da competição |
| `GET /api/v1/matches` | Banco; filtro `season_id`, paginação `limit`/`offset` |
| `GET /api/v1/matches/{id}/participation?calculation_id={uuid}` | Banco; resultado do cálculo solicitado para a partida |

Use o UUID **interno** da partida e o UUID emitido pelo comando de cálculo. O
endpoint de participação retorna nomes, titularidade, evidência, duração em
milissegundos, apresentação legível e intervalos. Uma duração ausente é `null`;
não é convertida em zero. Cálculo inexistente ou de outra partida retorna 404;
parâmetros inválidos retornam 422. Falhas SQLAlchemy recebem resposta 503.
O limite 100 nas listagens significa registros por página, não cota de requisições.

## Qualidade e testes

```sh
ruff check .
ruff format --check .
mypy src
pytest -q
```

Os testes que exigem PostgreSQL são ignorados quando `TEST_DATABASE_URL` não está
definido. Para rodar a suíte completa, crie um banco separado uma única vez:

```sh
docker compose exec db createdb -U football football_test
```

Se ele já existir, pule a criação e execute:

```sh
DATABASE_URL=postgresql+psycopg://football:football_local@localhost:5433/football_test alembic upgrade head
TEST_DATABASE_URL=postgresql+psycopg://football:football_local@localhost:5433/football_test pytest -q
```

Nunca aponte os testes ao banco principal. Alguns testes de pipeline preservam
fixtures/auditoria no banco de testes; outros usam rollback. As consultas externas
são simuladas nos testes automatizados. Há um aviso de depreciação na integração
TestClient/HTTPX das versões atuais, sem falhas na suíte validada.

O GitHub Actions verifica lint, formatação, tipos, ciclo de migrações em banco
isolado, testes e build Docker. SQLAlchemy é usado com SQL explícito; ainda não há
modelos ORM registrados. As migrações são manuais; `--autogenerate` está bloqueado
com metadata vazio.

## Limites e próximos passos

- Eventos são preservados em `raw` e interpretados para participação; ainda não há
  uma tabela geral de eventos normalizados nem estatísticas completas de cartões.
- Ainda não calculamos passes, finalizações, formações por trecho ou métricas espaciais.
- Durações sem evidência permanecem ausentes. Expulsão de jogador já fora de campo
  e outras sequências não suportadas podem interromper o cálculo para revisão.
- A reutilização de um cálculo verifica identidade por fontes/versão e contagens;
  não compara novamente cada valor já armazenado.
- Execuções interrompidas abruptamente podem permanecer `running`; não há
  reconciliação automática, agendamento ou atualização ao vivo.
- `kickoff_at` permanece ausente enquanto o fuso da fonte não for tratado.
- Outros fornecedores, frontend e infraestrutura de produção são etapas futuras.
- Posição média de ações e posição média contínua são análises distintas; tracking
  e identificações individuais em dados 360 não são presumidos disponíveis.

Próximo bloco: normalização geral dos eventos da final, contexto tático e
estatísticas com definições explícitas, antes de expandir para os demais jogos.

## Documentação e atribuição

- [Arquitetura](docs/architecture/system-design.md)
- [Integração StatsBomb](docs/integrations/statsbomb.md)
- [Ingestão do catálogo](docs/ingestion/catalog.md)
- [Pipeline de participação](docs/ingestion/participation.md)
- [Fórmula temporal e validação da final](docs/analytics/elapsed-on-field.md)
- [Revisão histórica do projeto](docs/reviews/project-review.md)

Dados fornecidos por **StatsBomb Open Data**. Repositório oficial:
<https://github.com/statsbomb/open-data>. Consulte suas condições, `LICENSE.pdf`
e requisitos de atribuição antes de publicar análises ou redistribuir dados.

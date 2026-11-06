# Arquitetura inicial

Monólito modular com FastAPI, SQLAlchemy e PostgreSQL.

Fluxo planejado: fornecedor → adapter → ingestão → raw → core → analytics → API.
Cada fornecedor terá contrato próprio, adaptado antes de chegar aos domínios.
IDs internos independem dos fornecedores. Dados ausentes permanecem ausentes.
Timestamps serão armazenados com fuso, em UTC. Métricas terão fórmula e versão.

As migrações criam os schemas e as tabelas de auditoria, competições, temporadas,
equipes, partidas e mapeamentos externos. Partidas ainda não têm pipeline de persistência.
O acesso ao banco usa SQL explícito via SQLAlchemy; o metadata da Base permanece vazio.
Autogenerate é bloqueado até que todas as tabelas estejam registradas no metadata.

Os endpoints de banco convertem falhas SQLAlchemy em HTTP 503, sem expor detalhes internos.
A prévia externa converte indisponibilidade do fornecedor em HTTP 502.
O lock compartilhado CORE_INGESTION_LOCK_ID serializa as cargas atuais de core.
Ainda não há métricas, política automatizada de reconciliação ou execução em produção.

Referências: https://fastapi.tiangolo.com/deployment/docker/ e
https://alembic.sqlalchemy.org/en/latest/tutorial.html

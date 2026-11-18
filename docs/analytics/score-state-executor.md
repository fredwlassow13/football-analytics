# Executor por situação do placar — primeira integração

Extensão explícita `score_state_contract_version=1.0.0`, implementada em
`analytics/score_state_execution.py` e encaminhada pelo `execute_query`/`run_query`
existentes. Os contratos e consultas MetricQuery comuns permanecem inalterados.

## Escopo

- Agrupamento por equipe; métricas das famílias passes, shots, xg e dribbles.
- Situações leading, drawing e trailing, relativas à equipe da própria ação.
- Períodos, seleção de equipes, uma ou mais partidas e agregação selected_matches.
- Reconstrução do placar completo antes de filtrar ações e períodos; um recorte
  do segundo tempo não reinicia o placar. O gol pertence ao contexto anterior.
- Gols contra usam a política conciliada existente, contando um par utilizável
  uma vez para a equipe beneficiada. Período 5 permanece excluído.

Criação, defesa, goleiros, movimento, tempo de participação e agrupamento por
jogador são recusados nesta primeira integração. Não se pode filtrar criação
cortando os dois lados do vínculo passe–chute, nem inferir presença de um jogador
em uma situação do placar a partir de sua presença no período inteiro.

Esta entrega integra os valores escalares filtrados. Não adiciona endpoint,
curvas cumulativas ao resultado do executor, cache ou novas definições de métricas.
As curvas temporais continuam no exportador local de evolução já existente.
Posse e disputa experimentais ficam fora deste adaptador.

## Uso

```python
from sqlalchemy import create_engine
from football_analytics.analytics.executor import run_query
from football_analytics.analytics.score_state_execution import ScoreStateQuery

query = ScoreStateQuery(
    match_ids=(3869685,),
    group_by="team",
    metrics=("passes.attempted", "shots.total", "shots.xg"),
    periods=(2,),
    score_state="leading",
)

# Use a URL configurada para a base com fontes e participação já persistidas.
engine = create_engine(database_url)
try:
    result = run_query(engine, query)
    print(result.model_dump_json(indent=2))
finally:
    engine.dispose()
```

ScoreStateQuery herda os campos do pedido existente e acrescenta a situação
obrigatória e a versão da extensão. Usar explicitamente essa classe. O contrato
MetricQuery comum continua rejeitando JSON com score_state, evitando descarte
silencioso do filtro. A entrada geral run_query reconhece o tipo especializado
antes de executar o caminho sem filtro.

O retorno é ScoreStateResult, preservando query.score_state ao serializar.
Para reler o JSON, usar ScoreStateResult.model_validate_json. O leitor de
QueryResult comum não substitui o leitor da extensão.

## Fonte e cobertura

O caminho de banco reutiliza load_query_sources: payloads vinculados ao cálculo
de participação, verificação do corpo/hash, IDs/endpoint, JSON e fontes atuais.
A participação é reconstruída e comparada à persistência antes do cálculo.
run_query mantém a transação REPEATABLE READ e READ ONLY existente.

O resultado acrescenta score_evidence por partida, com hash de eventos, política,
status de evidência de gol, issues e placar final na ordem explícita de team_ids.
O placar final é reconstruído dos eventos; não é uma nova leitura independente
do placar oficial. As comparações com o cadastro das partidas ficam no script
de aceitação sobre os arquivos brutos.

Evidência de gol não resolvida mantém todos os valores daquele recorte como
unknown com razão unresolved_score_state. Não se publica zero por falta de
reconstrução. Regressões de relógio já diagnosticadas aparecem em review_reasons;
a atribuição segue a ordem original dos eventos, como no núcleo temporal.

Com fonte e placar resolvidos, ausência de ações da equipe na situação selecionada
produz contagem zero. Isso não mede tempo em vantagem e não afirma que essa
situação tenha ocorrido por duração positiva. Sem passes, completion_pct é não
aplicável. A cobertura de eventos refere-se à população após os filtros.

xG e razões usam o conversor comum do executor: xG ausente preserva incompletude;
soma disponível não é promovida a xG completo; percentuais agregados recompõem
numerador e denominador. Se uma partida aplicável estiver desconhecida, o agregado
não oculta essa ausência. Gols de Shot continuam distintos do placar com gols contra.

## Verificação

Testes da extensão cobrem encaminhamento pelo executor, placar anterior à ação,
períodos, gol contra, evidência ambígua, exclusão da disputa, filtros, dribles,
xG parcial, agregação de partidas, desconhecidos e round-trip do JSON com hash.
A camada de banco usa mocks nesses testes; a suíte completa pode ignorar testes
de banco se o ambiente PostgreSQL de teste não estiver configurado.

O script verify_score_state_executor.py compara sete métricas por equipe e
situação com contadores independentes dos eventos brutos: passes tentados,
chutes, gols de Shot, xG completo/disponível, chutes sem xG e dribles tentados.
Também compara o placar reconstruído bruto com o cadastro de partidas. Não
executa SQL nem confirma os dados de uma instalação de banco do usuário.

```sh
.venv/bin/python scripts/verify_score_state_executor.py \
  --events-dir outputs/audits/statsbomb-2022-2026-11-13/sources/events \
  --matches outputs/audits/statsbomb-2022-2026-11-13/sources/matches/43/106.json \
  --output outputs/verifications/score-state-executor.json
```

A saída exige caminho novo. A próxima conferência no ambiente do usuário pode
executar uma consulta da final contra PostgreSQL, antes de ampliar a integração
às demais famílias ou publicar endpoints.

Checkpoint desta entrega: 51 testes focados passaram; suíte completa com
2.381 aprovados, 16 ignorados e o aviso TestClient/HTTPX já conhecido. Ruff,
formatação e mypy aprovados na cópia isolada. Os contadores independentes
conciliaram 64 partidas e 2.688 comparações, sem divergências. Este checkpoint
não inclui uma consulta real PostgreSQL da nova extensão no ambiente do usuário.

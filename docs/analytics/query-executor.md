# Executor analítico — primeira integração (catálogo 1.2.0)

Atualização: defesa, duelos, jogo aéreo e goleiros foram integrados no catálogo 1.3.0. Veja [Executor defensivo](defensive-executor.md). Este documento preserva a aceitação da primeira integração.

Esta entrega conecta passes, chutes, xG e participação ao contrato comum. São **36 definições habilitadas** para jogador; 35 para equipe, pois tempo em campo não é duração de equipe. As demais 168 definições numéricas e os recursos estruturados continuam registrados e desabilitados no executor. Não foi criada interface, rota ou novo plot.

## Uso como serviço Python

```python
from football_analytics.analytics.contracts import MetricQuery
from football_analytics.analytics.executor import run_query

query = MetricQuery(
    match_ids=(3869151, 3869321),
    group_by="team",
    metrics=("passes.attempted", "passes.completion_pct", "shots.total", "shots.xg"),
    aggregation="selected_matches",
    periods=(1, 2),
)
result = run_query(engine, query)
# result.model_dump_json(indent=2)
```

`engine` é um SQLAlchemy Engine configurado pelo chamador. O serviço não incorpora URL de banco, partida, competição ou hash fixos. `run_query` usa transação REPEATABLE READ e READ ONLY. `execute_query(connection, query)` permite integração dentro de transação administrada pelo chamador, que deve garantir consistência/leitura. Ainda não há CLI ou frontend para esse serviço.

O resultado é um `QueryResult` validado, com filtros, valores/estados, componentes das razões e proveniência. Métrica cadastrada mas não integrada é recusada antes de consultar o banco. Os campos `execution_available` são derivados da mesma declaração de capacidades usada pelo executor.

## Seleção e validação das fontes

1. Localiza as últimas fontes de eventos e escalações armazenadas para cada partida.
2. Exige cálculo persistido de participação `elapsed_on_field_ms` 1.0.0 com os mesmos dois hashes. Isso é requisito também para consultas por equipe nesta primeira integração.
3. Carrega os IDs exatos dos payloads vinculados ao cálculo. Payload equivalente com outro ID não substitui silenciosamente a referência do cálculo.
4. Confere fornecedor, endpoint/partida, corpo bruto versus SHA-256 e JSON armazenado versus corpo bruto.
5. Reconstrói participação e compara todos os resultados/intervalos com a persistência, não apenas suas contagens.
6. Aplica períodos e calcula com os módulos existentes de resumo ofensivo, roster e participação no recorte.

Falta de fontes, cálculo antigo diante de fontes novas, divergência de persistência ou período indisponível causa erro da consulta. Não se omite a partida problemática nem se publica cobertura completa. Seleção histórica por IDs explícitos de payload ainda não é oferecida.

`source_complete` significa cobertura da população normalizada no payload selecionado e validado; não é prova independente de que o fornecedor registrou todos os acontecimentos reais da partida.

## Regras dos resultados

- Jogador com ações e presença compatível: contagens observadas.
- Jogador com presença no recorte e sem ações selecionadas: zero com presença confirmada.
- Jogador sem evidência suficiente: desconhecido, não zero.
- Participação conhecida apenas fora dos períodos selecionados: ações não aplicáveis; tempo no recorte pode ser zero, conforme o módulo de participação.
- Aproveitamento sem tentativas: não aplicável, valor nulo e motivo `zero_denominator`.
- Nenhum chute com população/fonte conhecidas: xG zero.
- Chutes sem xG: total completo nulo/incompleto; soma disponível parcial explicitamente incompleta. Se todos carecem de xG, soma disponível permanece nula.
- `with_xg` e `missing_xg` continuam contagens conhecidas quando a ausência do atributo está identificada.
- Disputa excluída; gols de Shot não incluem gols contra.

O roster é o da fonte de cada partida. Um atleta não listado em uma partida não recebe uma linha fictícia nela. Filtros de jogador/equipe que não encontram correspondência em nenhuma partida selecionada causam erro explícito.

O executor não usa nomes/apelidos/nacionalidade para estabelecer identidade: usa os IDs do fornecedor. Não resolve as variantes de metadados registradas na carga anterior, nem certifica presença em cada instante de cada ação (a integração atual valida participação no recorte).

## Agregação entre partidas

`per_match` mantém uma linha por entidade e partida. `selected_matches` agrupa por equipe e jogador, quando aplicável, preservando os IDs de todas as partidas solicitadas como escopo da seleção. A contribuição efetiva vem das partidas em que a entidade está listada; ausência do cadastro naquela escalação não é inventada como participação.

Contagens e tempo são somados. Percentuais são recompostos a partir dos numeradores e denominadores somados, com ROUND_HALF_UP em duas casas. Um desconhecido aplicável mantém o agregado desconhecido, mesmo que outras partidas tenham valores conhecidos. Casos não aplicáveis são excluídos da soma; se todos forem não aplicáveis, o agregado também será. Incompletude de xG é preservada; a soma parcial não vira total completo.

A cobertura de participação usa uma observação por jogador–partida; as demais coberturas se referem aos eventos elegíveis da métrica. Isso não cria métricas por 90.

## Aceitação com a Copa 2022

Resultados salvos em `outputs/reviews/executor-2026-11-16/`:

- 64 partidas; 128 linhas equipe–partida e 3.244 linhas jogador–partida.
- 35 definições por equipe e 36 por jogador.
- **121.264 verificações** de valores/estados contra SQL independente e participação persistida.
- **768 comparações** com campos do baseline anterior.
- **1.120 comparações** dos agregados das 32 seleções.
- Nenhuma divergência nesses recortes.
- Estados: 76.281 disponíveis, 44.964 desconhecidos e 19 não aplicáveis. Os desconhecidos correspondem à preservação de ausência de evidência, não a falhas convertidas em zero.

`verify_runtime.py` preserva a verificação SQL executada; é artefato de aceitação, não outro motor de métricas. Os relatórios JSON completos incluem proveniência por partida. Casos sintéticos nos testes cobrem xG ausente/parcial, filtros, exclusão de disputa, soma ponderada e integridade de fontes, que a competição real isoladamente não exercita em todas as combinações.

Próxima integração: defesa, duelos no chão, jogo aéreo e goleiros, mantendo as mesmas exigências de população, fonte e comparação independente. Nenhuma dessas famílias foi habilitada por consequência automática desta entrega.

Validação final: 2.105 testes passaram (14 específicos do executor), com um aviso conhecido de depreciação. Ruff, formatação e mypy aprovados; 151 arquivos fonte verificados.

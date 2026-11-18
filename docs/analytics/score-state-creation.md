# Criação por situação do placar

Primeira entrega da família creation no adaptador ScoreStateQuery. Agrupamento
por equipe, extensão explícita score_state_contract_version=1.1.0. Consultas
anteriores 1.0.0 continuam válidas para passes, chutes/xG e dribles; criação
continua recusada em 1.0.0 para não ampliar silenciosamente o contrato antigo.

## Regra de seleção

1. Normalizar as ações e classificar todos os vínculos da partida, incluindo
   referências, ambiguidades e períodos, com a política existente de criação.
2. Reconstruir o placar completo antes de selecionar períodos/situação.
3. Selecionar os passes pelo período e placar anterior ao próprio passe.
4. Resumir esses passes com a classificação global intacta. O chute associado
   não precisa pertencer ao filtro. Não remover o chute e reclassificar o par
   como inexistente apenas porque ele está fora do recorte.

Um vínculo entre períodos continua review por period_mismatch: preservá-lo não
significa promovê-lo a utilizável. Pênaltis, passe incompleto, referências
ambíguas e demais casos seguem a classificação existente. Não há alteração da
política global de vínculos ou inferência de novas assistências/xA.

## Métricas

As nove definições do catálogo são suportadas:

- creation.pass_count
- creation.candidate_pairs
- creation.usable_pairs
- creation.review_pairs
- creation.recorded_shot_assists
- creation.recorded_goal_assists
- creation.passes_with_any_recorded_flag
- creation.passes_with_both_recorded_flags
- creation.usable_pairs_without_positive_flag

Flags positivas contam nas métricas de assistências registradas somente em
pares utilizáveis, conforme a fórmula existente. União/interseção de IDs evita
contar duas vezes um passe com ambas as flags. Flag positiva sem vínculo
utilizável não vira assistência inferida; permanece nos diagnósticos/IDs.
Flags ausentes continuam desconhecidas na evidência de cobertura das flags.

A cobertura dos escalares usa a população de passes selecionados, como no
executor de criação já existente. Contagens de pares podem ser maiores que o
número de passes quando existem ambiguidades: isso é um diagnóstico, não uma
razão percentual. Pares em revisão não tornam desconhecido um contador definido
como número de pares utilizáveis; os motivos aparecem em review_reasons.

## Evidência no resultado

score_evidence[*].creation_scope contém uma entrada por equipe da partida,
mesmo quando a consulta filtra uma só equipe. Cada entrada preserva resumo,
IDs dos passes/chutes, pares classificados e versões de política. A seleção
é pass_period_and_pre_action_score. match_reference_issues é deliberadamente
da partida completa, não uma alegação de que todas as referências problemáticas
pertençam à população selecionada.

Com placar não resolvido, todos os valores filtrados continuam unknown e não
há criação selecionada publicada como se o recorte fosse conhecido. Grupos
conhecidos sem passes têm zero com evidência vazia. A soma selected_matches
usa a política comum, sem ocultar partidas aplicáveis desconhecidas.

## Exemplo

```python
query = ScoreStateQuery(
    score_state_contract_version="1.1.0",
    match_ids=(3869685,),
    group_by="team",
    score_state="drawing",
    metrics=(
        "creation.usable_pairs",
        "creation.recorded_shot_assists",
        "creation.recorded_goal_assists",
    ),
)
result = run_query(engine, query)
```

O encaminhamento no executor já integrado é reutilizado; somente
score_state_execution.py precisa ser substituído. Não há mudança de schema,
endpoint, migração, posse ou disputa nesta entrega.

## Verificação

verify_score_state_creation.py possui modo local e --database. Ambos comparam
nove métricas por equipe nos três estados: 54 comparações por partida e 3.456
nas 64 partidas. Também conferem IDs dos passes selecionados e pares preservados,
valores, estados e cobertura. O relatório guarda as evidências de criação;
no modo banco, acrescenta as referências das fontes retornadas pelo executor.

A atribuição de passes ao placar é conferida por um percurso independente dos
eventos brutos, incluindo gols contra e excluindo a disputa de pênaltis. A
classificação de criação de referência reutiliza o módulo global já existente:
esta verificação valida a nova seleção/agregação, não é uma reimplementação
independente do classificador de vínculos. O relatório declara essa limitação.

O modo --database usa get_engine() e uma transação REPEATABLE READ / READ ONLY
para todo o lote, com savepoint por partida. Falhas são registradas sem promover
sucesso parcial a lote aprovado. O arquivo de saída precisa ser novo.

Na preparação, 64 partidas locais e 3.456 comparações passaram, sem falhas.
A execução PostgreSQL desta nova família fica para o usuário após aplicar e
passar os testes. Os testes de banco do verificador usam conexões simuladas.

Checkpoint dos testes: 78 testes focados aprovados; suíte completa com
2.412 aprovados, 16 ignorados e o aviso TestClient/HTTPX conhecido. Ruff,
formatação e mypy aprovados. Não foi executada consulta PostgreSQL desta
nova família durante a preparação; os testes do verificador usam mocks.

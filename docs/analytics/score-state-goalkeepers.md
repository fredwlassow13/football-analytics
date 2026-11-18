# Goleiros por situação do placar — extensão 1.3.0

Agrupamento por equipe, com as 24 definições existentes da família goalkeepers.
Usar score_state_contract_version="1.3.0". Essa versão também aceita as famílias
já integradas; as versões anteriores continuam válidas em seus escopos anteriores.

## Instante e perspectiva

Cada par goleiro–chute usa período e placar **anteriores ao chute**, na perspectiva
da equipe do goleiro identificado na relação. Não usa o placar anterior ao evento
Goal Keeper, que pode vir depois do próprio gol ou de outros eventos.
Um gol que muda 0–0 para 1–0 pertence ao recorte drawing da equipe que sofreu.

Classificação e resolução dos vínculos usam a partida completa, antes dos filtros.
Um par entre períodos permanece em revisão e é selecionado pelo período do chute,
sem apagar sua referência ao evento do goleiro fora do recorte. Pares com qualquer
lado no período 5 ficam excluídos e são listados separadamente. A disputa de
pênaltis continua no fluxo próprio do projeto.

Gols contra alteram o contexto de placar pelo núcleo conciliado existente, mas
não são inventados como gol de Shot sofrido pelo goleiro. As métricas de gols
sofridos desta família continuam referentes aos pares com gols de Shot classificados.

## Métricas e partições

As seis contagens são pair_count, shot_goals_conceded, on_target_saves,
off_target_saves, other_shots e review_pairs. Cada uma existe em counts,
penalty_counts, non_penalty_counts e unknown_penalty_counts (24 definições).
As partições de pênalti recompõem o total campo a campo. Tipo ausente não vira
não pênalti; pares ambíguos continuam review; Shot Faced não vira defesa.
Não há novas fórmulas de save percentage, xG pós-chute ou métricas por minuto.

As contagens medem **pares observados classificados**, não uma garantia de que
todos os chutes adversários tenham sido vinculados. A cobertura numérica usa
pair_count, como no executor original. Para não ocultar lacunas, cada escopo
publica também a população de chutes adversários selecionados e seus IDs sem
par selecionado. Zero pares classificados não significa zero chutes enfrentados.

## Função e participação

O caminho PostgreSQL reutiliza fontes vinculadas à participação persistida,
verifica corpo/hash e participação reconstruída, e usa a política existente de
função de goleiro no recorte de períodos. A equipe precisa ter evidência de
função observada/confirmada nesse recorte para receber números disponíveis.
Sem essa evidência, os escalares de goleiro ficam unknown com
reason=goalkeeper_role_unknown, mesmo quando outras famílias da consulta são
calculáveis. Não se presume a função pela simples existência da equipe.

Isso preserva a validação existente por períodos; não é uma nova auditoria da
posição e presença de cada atleta em cada milissegundo de chute. Agrupamento
por jogador e taxas dependentes de tempo exigem uma integração posterior.

Na função pura prepare_score_state, keeper_population é uma entrada explícita
para uso com fontes já verificadas. Se omitida, não presume função conhecida.
O modo local do verificador a obtém das escalações e da política existente.

## Evidência no resultado

score_evidence[*].goalkeeper_scope contém os dois times e preserva:

- população da função, quatro partições e pares classificados completos;
- goalkeeper_event_ids: fontes dos goleiros participantes dos pares selecionados;
- candidate_shot_event_ids: chutes desses pares, sem duplicar um chute ambíguo;
- selected_opponent_shot_event_ids: chutes adversários no estado/período selecionado;
- unlinked_opponent_shot_event_ids: seleção dos chutes sem vínculo no classificador global;
- opponent_shots_without_selected_pair_ids: chutes da seleção sem um par elegível
  naquele escopo, inclusive pares excluídos por envolverem período 5;
- match_unlinked_goalkeeper_event_ids: ações de goleiro sem vínculo na partida;
- match_unlinked_shot_action_ids: subconjunto de tipos associados a finalizações;
- match_source_issues e match_excluded_shootout_pairs: diagnósticos da fonte e exclusões.

Os campos match_* referem-se à partida inteira. Uma ação sem chute associado
não recebe artificialmente um contexto pré-chute. Nenhuma referência por instante
ou proximidade de tempo é criada para forçar um vínculo.

Pares múltiplos podem superar o número de chutes únicos; permanecem em revisão.
Um vínculo de equipe contraditória também não deve ser promovido a ação válida.
As evidências preservam os IDs e os motivos da classificação original.

## Exemplo

```python
query = ScoreStateQuery(
    score_state_contract_version="1.3.0",
    match_ids=(3869685,),
    group_by="team",
    score_state="drawing",
    metrics=(
        "goalkeepers.counts.shot_goals_conceded",
        "goalkeepers.counts.on_target_saves",
        "goalkeepers.penalty_counts.review_pairs",
    ),
)
result = run_query(engine, query)
```

Filtros de estado não alteram o instante das outras famílias: criação usa o
passe e defesa usa a própria ação. Para goleiros, o contrato explicita o chute.
Agregação entre partidas soma as contagens e mantém desconhecido de uma partida
aplicável quando faltar evidência. Os pares/evidências por partida são preservados.

## Verificação

verify_score_state_goalkeepers.py aceita --events-dir, --lineups-dir, --matches,
--output e --database opcional. Sem --database executa localmente; com essa opção,
usa get_engine() em REPEATABLE READ / READ ONLY com savepoint por partida.
O arquivo de saída precisa ser novo.

O verificador reutiliza o classificador de pares e a política de função já
existentes. Confere separadamente a atribuição pelo placar pré-chute usando um
percurso dos eventos brutos, e monta as 24 contagens de referência sem chamar
o novo resumidor. Isso valida a integração; não é uma reimplementação independente
da classificação nem da inferência de função.

Conferidos valores, estados, cobertura, pares, chutes adversários e função das
duas equipes nos três estados: 144 comparações por partida, 9.216 em 64 partidas.
No modo PostgreSQL, hashes de eventos **e escalações** precisam coincidir com os
arquivos locais, além das verificações do executor. O relatório guarda ambos
os hashes e as referências devolvidas pelo banco.

A execução local das 64 partidas passou sem falhas. A execução real no PostgreSQL
da nova extensão fica para o usuário. Os testes do verificador usam conexões
simuladas, sem alteração do banco ou fontes originais.

Checkpoint da entrega: 93 testes focados aprovados; suíte completa com 2.466
aprovados, 16 ignorados e o aviso TestClient/HTTPX conhecido. Ruff, formatação e
mypy aprovados na cópia isolada. Fontes táticas inválidas continuam sujeitas às
recusas da validação existente; a integração não as converte em função conhecida.

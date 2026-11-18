# Defesa, duelos e jogo aéreo por situação do placar

Extensão explícita score_state_contract_version=1.2.0, por equipe. As versões
1.0.0 e 1.1.0 continuam válidas nos escopos anteriores; métricas defensivas
exigem 1.2.0. Criação pode ser combinada com defesa usando a versão 1.2.0.

## Escopo

As 84 definições existentes das famílias defense, duels e aerial são suportadas.
Isso inclui contagens por tipo, flags true/false/unknown, resultados específicos
de desarmes, projeções aéreas e aproveitamento aéreo. Nenhuma definição do
catálogo ou fórmula do conversor defensive_value foi alterada.

A seleção usa o placar anterior ao evento, relativo à equipe que executa a ação.
Uma pressão da França com a Argentina em posse usa a situação da França,
independentemente do possession_team. O placar completo é reconstruído antes do
recorte de períodos. Os gols de Shot e gols contra seguem o núcleo conciliado.
A disputa de pênaltis é excluída.

Goleiros e agrupamento por jogador permanecem fora deste adaptador. Exigem uma
integração própria para vínculos goleiro–chute, função em campo e participação.
Posse/disputa experimental continua fora das métricas consolidadas.

## Projeções e preservação de referências

Os normalizadores existentes leem o payload completo. Ações defensivas e
projeções aéreas são selecionadas com o mesmo contexto do evento de origem,
antes de build_defensive_summary. A validação compartilhada preserva a
compatibilidade de cabeçalhos e os vínculos entre as duas visões.

Um Clearance com aerial_won=true pertence às duas visões, mas representa uma
fonte única em defense.unique_sources. Pass, Shot e Miscontrol também podem
fornecer projeções aéreas ganhas. Portanto, defense.unique_sources é a união
definida no catálogo; não equivale a somar as categorias de defesa nem a contar
somente tipos de evento tradicionalmente defensivos.

O resultado acrescenta score_evidence[*].defensive_scope para as duas equipes,
com resumo, IDs das ações, IDs aéreos, IDs compartilhados e união sem duplicatas.
Os related_events originais são preservados como related_ids_by_source.

- related_targets_outside_selected_union: alvos que existem na partida, mas não
  pertencem à união selecionada da equipe. Podem ser passes adversários, eventos
  de outra família, outro período ou outro recorte. Isso não é erro por si só.
- missing_related_target_ids: alvos ausentes do catálogo completo da partida;
  adicionam defensive_related_targets_missing aos diagnósticos. Não criam novas
  ações nem alteram a atribuição de equipe.
- None no mapa de relações preserva ausência da informação; tupla vazia preserva
  uma lista explicitamente vazia. Não se substituem os dois casos um pelo outro.

## Valores, cobertura e agregação

Com fonte e placar resolvidos, grupos sem eventos têm zero nas contagens e
evidência vazia. Sem disputas aéreas observadas, aerial.win_pct é nulo e não
aplicável, com zero_denominator. Não se publica 0% de sucesso nesse caso.

Flags ausentes continuam na categoria unknown. Won, Success e Success In Play
permanecem resultados distintos de Tackle, sem inventar uma classificação
binária de sucesso. Dados contraditórios continuam sujeitos às recusas dos
normalizadores/agregadores existentes; a integração não os converte em zero.

Evidência de gol não resolvida mantém todos os valores filtrados desconhecidos.
A cobertura e as fórmulas são as do conversor defensivo existente. Agregação
selected_matches soma contagens e recompõe aerial.win_pct por soma de vitórias
sobre soma de disputas, sem média simples de percentuais.

## Consulta

```python
query = ScoreStateQuery(
    score_state_contract_version="1.2.0",
    match_ids=(3869685,),
    group_by="team",
    score_state="trailing",
    metrics=(
        "defense.pressures",
        "defense.interceptions",
        "duels.tackles.total",
        "aerial.won",
        "aerial.win_pct",
    ),
)
result = run_query(engine, query)
```

A entrada é o run_query existente e usa get_engine() na configuração já testada.
O resultado é ScoreStateResult; seu leitor JSON preserva a versão, o filtro e
as novas evidências. Não há rota HTTP, migração ou persistência nova nesta etapa.

## Verificação local e PostgreSQL

verify_score_state_defense.py compara todas as 84 métricas por equipe nos três
estados, sem recorte de períodos: 504 comparações por partida e 32.256 em 64 jogos.
Também confere IDs das projeções/união, referências preservadas, valor, estado,
população, cobertura e componentes da razão aérea. O cadastro local de partidas
serve para conferir o placar bruto reconstruído, incluindo gols contra.

A referência usa diretamente os campos dos eventos brutos e um percurso próprio
do placar anterior à ação. Não usa normalizadores, núcleo temporal ou resumo
defensivo da implementação. O catálogo é usado para identificar as definições,
famílias e seletores de campos, não para executar os cálculos de referência.

Sem --database, calcula localmente. Com --database, executa a extensão no banco
configurado, em uma transação REPEATABLE READ / READ ONLY, com savepoint por
partida. Exige que o hash dos eventos no banco corresponda ao arquivo local.
O relatório salva os resultados, as referências do banco e as evidências;
falhas geram código de saída 1. O arquivo de saída precisa ser novo.

```sh
.venv/bin/python scripts/verify_score_state_defense.py \
  --events-dir outputs/audits/statsbomb-2022-2026-11-13/sources/events \
  --matches outputs/audits/statsbomb-2022-2026-11-13/sources/matches/43/106.json \
  --database \
  --output outputs/verifications/score-state-defense-postgresql.json
```

A preparação foi validada com 96 testes focados e conferência local das 64
partidas, com 32.256 comparações sem falhas. Os testes do caminho PostgreSQL
usam conexões simuladas; a execução real dessa nova família fica para o usuário.

Checkpoint: suíte completa com 2.439 testes aprovados, 16 ignorados e o aviso
TestClient/HTTPX conhecido. Ruff, formatação e mypy aprovados na cópia isolada.

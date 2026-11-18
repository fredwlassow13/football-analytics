# Catálogo e contratos analíticos — primeira entrega

Atualização: passes/chutes/xG/participação estão conectados no catálogo 1.2.0. Veja [Executor analítico](query-executor.md). O texto abaixo preserva o escopo da entrega original do catálogo.

Este documento descreve a primeira entrega de 24 definições. O catálogo atual foi ampliado; veja [Catálogo ampliado](complete-catalogue.md). As regras gerais do contrato abaixo continuam aplicáveis.

Esta camada fica em `src/football_analytics/analytics/`. Não cria telas, endpoint, comando interativo, tabelas ou alterações no banco. Também não executa consultas analíticas: valida definições, pedidos e resultados para o futuro serviço compartilhado.

## Catálogo 1.0.0

`catalogue.METRICS` contém 24 definições imutáveis e `get_metric(id)` resolve um identificador. Cada definição registra família, unidade, versão da definição, fórmula, agrupamentos, fornecedor, filtros previstos, agregação, denominador, política de ausência, referência da implementação e testes existentes.

A versão da definição do catálogo não inventa uma versão histórica para módulos que não possuíam versionamento. As referências apontam para funções/modelos existentes, sem copiar as funções de cálculo. `execution_available=False` em todas as definições distingue registro/validação de disponibilidade no futuro executor.

| Família | Identificadores |
| --- | --- |
| Passes (9) | `passes.attempted`, `passes.completed`, `passes.incomplete`, `passes.out`, `passes.offside`, `passes.unknown`, `passes.injury_clearance`, `passes.not_completed`, `passes.completion_pct` |
| Chutes (10) | `shots.total`, `shots.goals`, `shots.shots_on_target`, `shots.saved`, `shots.saved_to_post`, `shots.saved_off_target`, `shots.blocked`, `shots.off_target`, `shots.post`, `shots.wayward` |
| xG/cobertura (4) | `shots.xg`, `shots.xg_available`, `shots.with_xg`, `shots.missing_xg` |
| Participação (1) | `participation.elapsed_on_field_ms` |

Passes e chutes suportam jogador/equipe. Participação admite apenas jogador: somar tempos dos atletas não produz duração da equipe. Todas as definições desta entrega referem-se ao StatsBomb e excluem a disputa de pênaltis. Defesa, goleiros, criação e progressão serão registrados nas próximas ampliações; sua ausência deste catálogo não significa ausência no projeto.

Aproveitamento de passes usa completos/tentados, incluindo resultados desconhecidos no denominador, com ROUND_HALF_UP em duas casas. Agregação entre partidas deve recompor numerador e denominador; nunca calcular a média simples dos percentuais. `not_completed` inclui incomplete/out/offside/injury_clearance, mas não unknown.

`shots.goals` não inclui gols contra e não é automaticamente o placar completo. `shots.xg` exige completude; `shots.xg_available` permite soma parcial explicitamente marcada como incompleta. O xG é o pré-chute fornecido pelo StatsBomb. Recortes por tipo de chute ainda não integram o contrato desta entrega.

Participação é o tempo decorrido nos intervalos recortados pelos períodos: inclui paralisações dentro deles e exclui intervalos entre períodos. A implementação é `scope_player_participation`, apoiada na política `elapsed_on_field_ms` 1.0.0. Ausência de intervalos não pode ser convertida automaticamente em zero; um jogador com participação conhecida fora do recorte pode ter zero no recorte, conforme a função existente.

## Pedido

```json
{
  "contract_version": "1.0.0",
  "provider": "statsbomb",
  "match_ids": [3869151, 3869321],
  "group_by": "team",
  "metrics": ["passes.attempted", "passes.completion_pct", "shots.total", "shots.xg"],
  "aggregation": "per_match",
  "periods": [1, 2]
}
```

`MetricQuery.model_validate_json(text)` valida JSON. Em Python, os modelos estritos usam tuplas para seleções; o JSON correspondente usa arrays. Campos não reconhecidos são recusados, assim como IDs duplicados, números inválidos, período 5, métrica desconhecida ou agrupamento incompatível.

`periods=null` significa todos os períodos de jogo disponíveis em cada partida, incluindo prorrogação quando houver. Selecionar 3/4 para uma partida sem prorrogação é recusado ao validar as fontes do resultado. Não se exclui silenciosamente uma partida para satisfazer um filtro.

Filtros iniciais: partidas explícitas, equipes, jogadores e períodos. `player_ids` com agrupamento por equipe é recusado para não publicar um subconjunto de jogadores como o total da equipe. Competição/temporada, situação do placar, fases, taxas por 90 e janelas não são campos aceitos ainda.

`aggregation=per_match` exige uma partida por linha. `selected_matches` exige que cada linha descreva a seleção completa, preservando a possibilidade de valores desconhecidos/incompletos. Ainda não há executor para efetuar essas operações.

## Resultado

- `Coverage`: fonte completa, população observada/presença confirmada/desconhecida/não aplicável, número de eventos elegíveis e cobertos para a métrica. Os contadores de cobertura são específicos da métrica: ausência de xG é relevante à soma de xG, mas pode estar completamente identificada na contagem de chutes sem xG.
- `MetricValue`: ID, versão, unidade, valor, estado, cobertura, componentes de razão, motivos de ausência/incompletude e motivos de revisão.
- `SourceReference`: partida, ID/hash dos eventos, períodos disponíveis e, quando necessário, ID/hash da escalação e cálculo de participação.
- `ResultRow`: seleção de partidas, equipe, jogador opcional e exatamente as métricas solicitadas.
- `QueryResult`: pedido, fontes e linhas, conferidos entre si.

Estados de valor:

| Estado | Regra |
| --- | --- |
| `available` | Valor presente, cobertura completa e população com evidência; zero requer essas condições |
| `unknown` | Valor nulo e motivo explícito |
| `not_applicable` | Valor nulo, motivo e população não aplicável à métrica |
| `incomplete` | Cobertura incompleta e motivo; soma parcial deve permanecer explícita |

Razões de qualidade/revisão são separadas do estado do valor: uma divergência de apelido não transforma necessariamente uma contagem válida em contagem ausente. Motivos são strings descritivas nesta versão, não um vocabulário universal de diagnósticos.

Valores negativos, não finitos, contagens fracionárias, unidades/versões incompatíveis e percentuais inconsistentes com numerador/denominador são recusados. Um total completo de xG não admite valor parcial: usar `shots.xg_available` para isso.

As fontes devem cobrir exatamente as partidas solicitadas, sem duplicação. Participação exige referência ao cálculo e aos dois payloads. Linhas fora dos filtros, métricas faltantes/duplicadas e linhas repetidas são recusadas.

## Limites da validação

Os modelos validam a coerência do documento, não a veracidade da cobertura declarada, o conteúdo real dos payloads, a existência dos IDs no banco ou o vínculo efetivo de um cálculo com os hashes informados. Essas verificações pertencem ao próximo serviço executor. Não há validação nas 64 partidas por este contrato ainda, pois ele não executa cálculos.

## Validação e próximo passo

Testes em `tests/test_analytics_contracts.py`: catálogo único, referências para implementações/testes existentes, leitura JSON, compatibilidade de filtros, zero versus desconhecido, cobertura, xG parcial, razões percentuais, proveniência e alinhamento de linhas.

Próxima entrega: executor inicial de passes/chutes/xG/participação que recebe `MetricQuery`, resolve as fontes, usa os cálculos existentes e produz `QueryResult`. Somente depois de conferir essa integração nas 64 partidas habilitaremos `execution_available` para as métricas correspondentes. Interface permanece fora desta etapa.

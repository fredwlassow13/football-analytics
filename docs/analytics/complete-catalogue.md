# Catálogo ampliado — versão 1.1.0

Atualização: catálogo 1.4.0, todas as 204 definições numéricas habilitadas. Veja [Integração complementar](complementary-executor.md). O texto abaixo preserva a entrega anterior.

Estado atual de execução: catálogo 1.3.0, 144 definições habilitadas. Veja [Executor defensivo e de goleiros](defensive-executor.md).

Atualização: passes/chutes/xG/participação estão conectados no catálogo 1.2.0. Veja [Executor analítico](query-executor.md). O texto abaixo preserva o escopo da entrega original do catálogo.

A primeira entrega registrava 24 definições. Esta ampliação cobre as famílias implementadas identificadas no inventário, incluindo defesa, duelos e goleiros, antes da integração do executor. Não cria novos cálculos, plots, endpoints ou telas e não modifica o banco.

## Como ler o catálogo

O ponto de entrada continua `football_analytics.analytics.catalogue`:

- `METRICS`: definições numéricas, com `kind=metric` ou `kind=diagnostic`.
- `RESOURCES`: análises estruturadas, diagnósticos estruturados e recursos de visualização.
- `PENDING_CAPABILITIES`: lacunas e recursos não suportados pelo adaptador atual.
- `get_metric(id)`: somente definições numéricas aceitas pelo contrato de consulta.
- `get_catalogue_entry(id)`: consulta descritiva dos três registros.
- `CATALOGUE_VERSION`: versão do conjunto; a versão individual das definições existentes permanece 1.0.0.

São **204 definições numéricas**, sendo **147 métricas e 57 diagnósticos**, além de **38 recursos estruturados/visuais e 7 lacunas explícitas**. Esses números incluem recortes e estados da mesma família: não significam 204 conceitos esportivos independentes. Por exemplo, cada flag defensiva tem contagens true/false/unknown, e os goleiros têm quatro contextos de pênalti.

Todas as entradas permanecem com `execution_available=False`. Elas descrevem implementações reais, mas ainda não estão conectadas ao executor comum. Testes existentes não certificam automaticamente cada entrada nas 64 partidas.

## Famílias numéricas

| Família | Definições, incluindo diagnósticos | Conteúdo |
| --- | ---: | --- |
| Passes | 9 | Tentados, completos, demais resultados, não completos e aproveitamento |
| Chutes | 10 | Total, gols de Shot, ao alvo e resultados individuais |
| xG/cobertura | 16 | Total completo, disponível, com/sem xG; geral, pênaltis, não pênaltis e tipo desconhecido |
| Defesa | 70 | União de fontes, nove tipos de ação e estados de atributos defensivos |
| Duelos | 10 | Sete resultados de tackle, seu total, aerial lost e total de eventos Duel |
| Jogo aéreo | 4 | Vitórias, derrotas, disputados e taxa de vitória |
| Dribles | 4 | Completos, incompletos, tentados e aproveitamento |
| Goleiros | 24 | Seis contagens em quatro contextos de pênalti |
| Criação | 9 | Assistências registradas, flags e qualidade dos pares passe–chute |
| Movimento/progressão | 30 | Cinco classificações, true/false/unknown, separadas entre Pass e Carry |
| Controle da bola | 17 | Categorias de evidência da política existente |
| Participação | 1 | Tempo decorrido em campo nos períodos selecionados |

### Defesa

Tipos: Ball Recovery, Block, Clearance, Dribbled Past, Duel, Foul Committed, Foul Won, Interception e Pressure. Cada contagem por tipo registra o seletor interno `event_type`.

As flags são obtidas da política existente `FLAG_FIELDS`: recuperação com recovery_failure/offensive; bloqueio com deflection/offensive/save_block/counterpress; cortes com aerial_won e partes do corpo; faltas com vantagem, pênalti e outros atributos; pressão/contrapressão. Cada uma preserva true, false e unknown. Unknown não é falso nem sucesso.

Interception/outcome, Duel/type/outcome, cartões/tipos de falta, partes do corpo e cobertura de campos também estão no recurso estruturado `analysis.defensive_breakdowns`, com os valores nativos, sem inventar um número fixo de categorias.

`defense.unique_sources` mede o tamanho da união dos IDs de ações e resultados aéreos no resumo existente. Cortes e evidência aérea podem compartilhar IDs: somar todos os subgrupos não reproduz necessariamente o total único.

### Duelos no chão e jogo aéreo

Tackles preservam lost, won, lost_in_play, lost_out, success, success_in_play e success_out. Não há taxa binária nova que una automaticamente won/success. Esses eventos também não representam toda forma possível de disputa no chão.

O normalizador atual distingue Tackle e Aerial Lost, sem subtipo de carrinho. Por isso `defense.sliding_tackles` aparece como **unsupported_by_current_adapter**, fora de METRICS. Não substituímos tackle por carrinho.

Aerial Lost vem de Duel; vitórias aéreas exigem evidência explícita em outros tipos de evento. `aerial.contested = won + lost`. Taxa de vitória é razão dos totais, com duas casas ROUND_HALF_UP; sem disputas, fica indefinida. Não somar `duels.aerial_lost` e `aerial.lost` como se fossem eventos diferentes.

### Goleiros

Contagens: pair_count, shot_goals_conceded, on_target_saves, off_target_saves, other_shots e review_pairs. Contextos: counts, penalty_counts, non_penalty_counts e unknown_penalty_counts.

pair_count e review_pairs são diagnósticos. Pares em revisão não viram defesas. Gol de Shot sofrido não inclui automaticamente gols contra. Tipos/resultados de outras intervenções, como saídas e ações sem chute, permanecem no recurso `analysis.goalkeeper_actions`, sem forçar todas as ações a serem defesas de finalização.

### Criação, movimento e controle

Criação preserva flags de assistência para chute/gol, vínculos explícitos, candidatos, pares utilizáveis e revisão. Os diagnósticos não são assistências adicionais e não representam xA.

Movimento usa a política `project_dx10_goal_distance25`: delta x pelo menos 10 e redução de distância ao gol de pelo menos 25%. Classificação geométrica é distinta de progressão contável; passes completos são elegíveis. Estados falsos/desconhecidos e elegibilidade/geometria são diagnósticos. As coordenadas não são metros percorridos.

Controle da bola registra as 17 categorias de evidência já implementadas, sem convertê-las automaticamente em ganhos/perdas efetivos de posse. As células estruturadas mantêm zonas, resultados, pressão e contrapressão.

## Recursos que não são escalares

O catálogo inclui resumos ofensivos/defensivos e seus rosters; ações e vínculos goleiro–chute; criação e seus vínculos; geometria; células de controle; fragmentos de posse e auditoria de tempo; observações de controle; ataques/paralisações; fases; conexões/nós/redes por janela; mudanças táticas, linha do tempo, janelas e escalações; qualidade do relógio; ocupação coletiva; gols contra/placar; elenco e elegibilidade inicial da disputa; heatmaps, mapas defensivos e renderizadores de chutes/xG/vista do gol/fora/redes; baseline do torneio.

Cada recurso indica implementação, esquema de saída quando aplicável, grão, testes e limitações. Uma matriz espacial, uma aresta e um fragmento não são aceitos como valores escalares de `MetricQuery`. O contrato específico para consultar esses recursos será desenvolvido depois.

## Lacunas explícitas

- Carrinhos: não distinguíveis pelo adaptador normalizado atual.
- Duração/percentual confiáveis de posse.
- Evolução por situação do placar.
- Evolução cumulativa validada de xG/criação.
- Mudanças de elegibilidade durante as cobranças.
- Política de exposição e taxas por 90.
- xA/PSxG: não encontrados como implementações completas; inclusão futura depende da definição desejada e das fontes.

Registrar uma lacuna não é compromisso de implementá-la antes de usar o projeto. Ela impede que seja confundida com um recurso já concluído.

## Compatibilidade e validação

O contrato de consulta continua limitado a partidas explícitas, jogador/equipe e períodos. `selection` em uma definição descreve o recorte interno fixo que identifica a métrica; não habilita novos filtros externos. Fases, janelas e situação do placar não foram adicionadas silenciosamente ao pedido.

O catálogo não substitui a verificação real de fontes e população pelo futuro executor. O mesmo cuidado de zero/desconhecido/incompleto continua valendo nas novas famílias. O contrato também recusa somas parciais publicadas como xG completo nos novos recortes de pênalti/tipo.

Testes cobrem: unicidade global de IDs, referências importáveis, existência dos testes indicados, cobertura de todos os estados defensivos, distinção tackle/carrinho, contextos de goleiros, xG parcial por recorte e separação entre métricas, análises estruturadas e pendências.

Próximo passo: implementar o executor por famílias, começando por passes/chutes/xG/participação, e validar cada integração nas 64 partidas. As demais definições poderão ser conectadas gradualmente, sem precisar reconstruir o catálogo.

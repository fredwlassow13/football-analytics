# Evolução dentro da partida e situação do placar

Política `1.0.0`, implementada em `ingestion/match_evolution.py`. Recebe os
catálogos completos de eventos, chutes, relações e criação da mesma partida,
mais os limites dos períodos. Não consulta rede nem modifica banco ou fontes.

## Placar e seleção

A ordem é `source_index`. Cada evento registra placar anterior e posterior,
período, relógio original, tempo decorrido, equipe/jogador e identificador da fonte.
O placar começa em 0–0. A situação `leading`, `drawing` ou `trailing` é relativa
à equipe da ação **antes** dela: o chute que desempata pertence a `drawing`.
Empates de timestamp são resolvidos pelo índice original.

Gols de Shot usam `build_goal_score_evidence`. Um gol contra utiliza somente um
par utilizável de `Own Goal Against`/`Own Goal For`, contando uma vez para a
equipe beneficiada, no primeiro índice do par. A segunda perspectiva não altera
o placar. Evidência não resolvida torna **todo o contexto de placar desconhecido**,
sem publicar reconstrução parcial como completa; xG e criação observados continuam
calculáveis. Os diagnósticos e a evidência completa de gols acompanham o resultado.
Eventos sem equipe recebem situação desconhecida.

`select_score_state_events` seleciona ações normalizadas da equipe solicitada,
por situação e períodos. Valida origem, identidade e cabeçalho. Os resultados
podem alimentar os agregadores existentes; para jogadores, selecionar também o
ID do jogador após este filtro. O seletor usa a situação da própria equipe da
ação, sem atribuir ações adversárias à população da equipe solicitada.

## Relógio

`elapsed_ms = start_elapsed_ms[period] + timestamp_ms`: soma as durações
observadas dos períodos anteriores, inclui acréscimos, exclui intervalos e a
disputa de pênaltis. Não é o minuto convencional da transmissão: na final, a
escala ultrapassa 120 minutos por incluir os acréscimos dos quatro períodos.
Os limites vêm dos eventos Half End. Período 5 é publicado apenas como exclusão.

Pequenas regressões de relógio na ordem original produzem
`source_clock_not_monotonic`. Não se inventam timestamps nem se reordenam ações
para reconstruir o placar. O gráfico mantém os pontos na ordem original e mostra
o aviso; regressões entre pontos ofensivos podem produzir recuos horizontais.

## Fórmulas e cobertura

| Campo | Definição |
| --- | --- |
| `shots` | Número de chutes observados, inclusive pênaltis durante o jogo |
| `shots_with_xg`, `missing_xg` | Cobertura de xG entre esses chutes |
| `xg_available` | `math.fsum` do xG pré-chute StatsBomb disponível |
| `xg` | Mesmo total somente se nenhum chute tiver xG ausente; caso contrário, nulo |
| `created_shots` | Pares passe–chute utilizáveis pela classificação de criação existente |
| `recorded_shot_assists` | Pares utilizáveis com flag shot_assist positiva |
| `recorded_goal_assists` | Pares utilizáveis com flag goal_assist positiva |

A criação é atribuída ao instante e ao placar do **passe**, não ao chute.
`created_shots` inclui pares utilizáveis sem flag positiva e não soma as duas
flags, evitando duplicação. Pênaltis e vínculos que exigem revisão não entram
nesse contador, conforme a política de criação existente. O resultado preserva
classificações, referências problemáticas e IDs dos passes em revisão. Não é
uma métrica de xA, qualidade de todas as oportunidades ou total de ataques.

`points` contém os acumulados por equipe nos instantes de chute ou passe criador.
`by_score_state` fornece oito grupos (duas equipes × quatro situações), com IDs
dos chutes/passes para auditoria. Grupos vazios têm zero na população observada,
sem inferir participação de jogadores ausentes. A soma dos grupos recompõe os
totais observados. Contagens não são taxas por minuto: não há denominador de
duração em vantagem, empate ou desvantagem nesta entrega.

## Gerar PNG e JSON

Na raiz do projeto, com os payloads locais já auditados:

```sh
.venv/bin/python -m football_analytics.visualization.match_evolution \
  --events outputs/audits/statsbomb-2022-2026-11-13/sources/events/3869685.json \
  --matches outputs/audits/statsbomb-2022-2026-11-13/sources/matches/43/106.json \
  --match-id 3869685 \
  --output-dir outputs/plots/match-evolution/3869685
```

O comando exige uma pasta nova, confere o placar final contra o cadastro da
partida e rejeita divergências. Se a evidência requer revisão, publica placar
nulo com status `review`. O bundle contém PNG, JSON, nomes das equipes, hashes
dos dois arquivos de origem e da imagem, versão da política e diagnósticos.
Os três painéis mostram xG disponível, gols e criação acumulados. Não depende
do índice de métricas agregadas, que não contém a cronologia de eventos.

## Conferência em 17/11/2026

As **64 partidas** foram conciliadas contra contadores independentes sobre os
payloads brutos locais, incluindo **três partidas com gols contra**. Foram
conferidos placar final, placar anterior a passes/chutes/conduções e IDs, somas
de xG e cobertura dos chutes por situação. Nenhuma falha. Esta conferência usa
arquivos locais, **não SQL**. A classificação de criação reutiliza a política
existente e recebe testes de atribuição temporal, revisão e deduplicação.

Na final: 4.386 eventos de jogo, 21 eventos da disputa excluídos, placar 3–3;
Argentina: 20 chutes, xG 2,758305926 e 15 pares criadores utilizáveis;
França: 10 chutes, xG 2,272617949 e quatro pares utilizáveis. xG completo para
os 30 chutes. Há regressões de relógio entre eventos gerais, sem regressão entre
os pontos ofensivos de cada equipe usados nas curvas da final.

Reproduzir a conferência em lote (usar um novo caminho de saída):

```sh
.venv/bin/python scripts/verify_match_evolution.py \
  --events-dir outputs/audits/statsbomb-2022-2026-11-13/sources/events \
  --matches outputs/audits/statsbomb-2022-2026-11-13/sources/matches/43/106.json \
  --output outputs/verifications/match-evolution-check.json
```

`--match-id` é opcional e repetível. O relatório registra sucessos, falhas e
hashes por partida e retorna código de erro quando houver falhas. A suíte inclui
cenários sintéticos de gol contra, referências ambíguas, gols no mesmo timestamp,
prorrogação, exclusão da disputa, xG ausente e seleções inválidas.

O núcleo e o exportador local estão disponíveis. Integração desse recorte no
`MetricQuery`, persistência/cache e API/frontend continuam no bloco de integração.

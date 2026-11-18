# Contexto de bola parada e jogo corrido

Política `source_event_play_pattern`, versão `1.0.0`. Implementada em
`ingestion/play_phase.py`, com classificação por evento, composição por fragmento
e seleção de ações normalizadas para os agregadores de criação, progressão e xG.

| play_pattern da fonte | Classe |
| --- | --- |
| regular_play, from_counter | open_play |
| from_corner, from_free_kick, from_throw_in, from_goal_kick, from_kick_off | set_piece |
| from_keeper, other, ausente | unknown |

`set_piece` significa **contexto de jogada iniciado por reinício**, incluindo
laterais, tiros de meta e saídas de centro nesta política. Não significa cobrança
direta, bola parada naquele instante nem bola fora de jogo. O padrão pode
acompanhar várias ações após o reinício. `from_keeper` não distingue distribuição
em jogo de reinício e permanece desconhecido. Pênaltis devem continuar separados
pelo tipo explícito da finalização nos agregadores existentes; esta classificação
não substitui essa informação nem identifica a execução de uma cobrança.

Cada evento mantém o nome e ID originais do padrão, equipe, jogador, período,
ordem, relógio e motivo da classificação. Não se propaga o primeiro padrão aos
demais eventos do fragmento. Fragmentos com classes diferentes recebem `mixed`;
padrões diferentes que mapeiam para a mesma classe não geram mistura de classes.
A seleção exige identidade e cabeçalho compatíveis e rejeita duplicatas e
recortes inválidos. A disputa de pênaltis permanece excluída.

A população contém todos os eventos contextualizados, inclusive administrativos
e ações adversárias dentro do fragmento. Seus totais não são números de ataques,
cobranças, ações ofensivas, duração ou percentual de posse. Para métricas de
criação/progressão/xG, `select_phase_events` filtra as respectivas ações
normalizadas por classe e período antes da agregação. A classe desconhecida deve
ser publicada como cobertura, sem redistribuição proporcional.

## Conferência da final em 12/11/2026

Payload SHA-256:
`a0b0036d50c10a4c8879d1ee9f24a7502ec2cfb264a61951c8d500dfc897ec35`.

Classificações e ordem dos 4.386 eventos dos períodos 1–4 comparadas
individualmente com CASE SQL sobre o mesmo payload armazenado:

| Classe | Eventos | Fragmentos exclusivamente nessa classe |
| --- | ---: | ---: |
| open_play | 1.731 | 99 |
| set_piece | 2.530 | 141 |
| unknown | 125 | 10 |

Os 250 fragmentos recompõem a população; nenhum é misto **entre classes** nesta
fonte. Isso é compatível com fragmentos com vários padrões da mesma classe.
Os 21 eventos do período 5 permanecem separados.

Reproduzir a conferência, em modo somente leitura, com banco e partida explícitos:

```sh
python scripts/verify_play_phases.py --database-url "$DATABASE_URL" --match-id 3869685
```

Esta entrega fecha a separação por cobertura do campo `play_pattern`. A
[reconstrução de ataques e janelas de paralisação](attack-sequences.md) foi
implementada posteriormente com evidências próprias, sem inferi-las desta
classificação. A [evolução por placar](match-evolution.md) também está disponível
como núcleo e exportação local. Duração/percentual de posse continuam pendentes.

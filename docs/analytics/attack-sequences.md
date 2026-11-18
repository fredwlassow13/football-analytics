# Ataques observados e paralisações

Política `observed_control_spells`, versão `1.0.0`, em
`ingestion/attack_sequences.py`. Reconstrói sequências ofensivas a partir das
ações e evidências de limites, mantendo a ligação com os fragmentos da fonte.
Não existe na entrada uma tabela pronta de ataques completos ou de bola em jogo.

## Definição operacional

Um ataque começa com passe, condução, chute, drible ou proteção da bola, com equipe
identificada. Essas ações dão evidência de atuação com bola; recuperação,
interceptação e recepção, sozinhas, não demonstram controle bem-sucedido no
contrato atual e não iniciam ataques. Ações defensivas adversárias dentro de um
ataque não causam troca de equipe. Um passe de primeira após interceptação pode
formar uma sequência curta: não se exige quantidade mínima de passes nem duração.

A equipe do ataque é a equipe que executa a ação com bola. Uma ação desse grupo
por outra equipe encerra a sequência anterior e começa outra. Divergência em
relação a `possession_team` permanece em `source_possession_team_disagreement`.
Não se atribui um passe/chute adversário ao ataque da equipe rotulada na fonte.
Uma mudança de fragmento da fonte sem troca de equipe é um limite conservador
`source_boundary`, marcado para revisão: não se presume continuidade entre rótulos.

Reinícios explícitos usam `pass_type` Corner, Free Kick, Throw-in, Goal Kick e
Kick Off, ou `shot_type` Corner, Free Kick, Penalty e Kick Off. O contexto
`play_pattern` não indica o instante da cobrança e não substitui esses campos.

O ataque termina por saída explícita, gol, impedimento, falta sem vantagem,
interrupção por lesão, reinício, troca observada de equipe, limite da fonte ou
fim do período. Um chute defendido/bloqueado/na trave não encerra automaticamente
o ataque: rebotes permanecem na sequência até outra evidência de limite.

Faltas com `advantage=true`, inclusive no contraponto explicitamente relacionado,
não interrompem a sequência. Em StatsBomb, a ausência da flag é interpretada
como falta sem vantagem nesta política específica, com certeza
`provider_absence_policy`; o campo original continua `None`. Para outro provedor,
a ausência permanece uma possível interrupção. O par falta cometida/sofrida e
marcadores consecutivos da mesma interrupção ficam na mesma observação de pausa,
com todos os IDs, sem contar cada perspectiva como uma pausa adicional.

Starting XI, mudanças táticas, substituições, cartões administrativos, entradas,
saídas, Half Start/End e Injury Stoppage não são ações ofensivas do ataque.
Eventos posteriores ao encerramento, sem nova ação que inicie ataque (por exemplo,
o registro do goleiro após um gol), também podem permanecer sem atribuição.
`unassigned_event_ids` preserva esses registros. A união de atribuídos e não
atribuídos recompõe exatamente a população dos períodos 1–4, sem duplicação.
Período 5 é separado. Lacunas na ordem da fonte e mudanças de período interrompem
a reconstrução; não se juntam ataques ou pausas através delas.

`bounded` significa que os limites foram encontrados pela política e que não há
um diagnóstico impeditivo no recorte. Não é certificação de ataque tático completo,
controle contínuo da bola ou precisão física dos instantes. `review` preserva
limites incertos, divergências de equipe e os problemas temporais dos fragmentos.

## Relógio e janelas de paralisação

Para saída, gol ou impedimento em Pass/Shot, a estimativa do término da ação é
`timestamp_ms + Decimal(str(duration_seconds)) * 1000`. Duração ausente não vira
zero. Outros marcadores de parada usam o instante registrado. A janela termina
no próximo reinício explícito ou no Half End do mesmo período. Os marcadores de
lesão registram interrupção; `in_chain` descreve a posse anterior à interrupção,
e não a continuidade da bola em jogo.

Uma ação com bola antes de reinício explícito fecha a observação como
`unobserved_restart`, com duração ausente. Término da fonte, recuos de relógio,
problemas de cobertura, intervalo negativo ou tempos de parada conflitantes
também impedem publicar duração. Não há arredondamento, ajuste automático,
limitação a zero nem correção de timestamps. O término de uma ação pode ultrapassar
o instante do próximo evento; isso fica visível como `negative_interval`.

`observed_span_ms` do ataque é apenas a diferença entre seus primeiro e último
registros, ausente quando o relógio exige revisão. Não soma durações de ações
sobrepostas nem inclui automaticamente o intervalo até o próximo ataque.

As janelas de parada são estimativas baseadas nos registros, não um relógio
completo e independente de bola fora de jogo. A soma das janelas utilizáveis não
pode ser subtraída do tempo total para declarar bola em jogo: existem reinícios
sem parada observada e pausas sem reinício explícito. Por isso,
`possession_duration_ms` e `possession_percentage` permanecem `None`.
Não se publica percentual de posse por contagem de eventos, por span ou por
redistribuição das lacunas. Um futuro indicador aproximado precisará de outra
política/versionamento, denominador e relatório de cobertura próprios.

## Validação da final — 12/11/2026

Fonte SHA-256:
`a0b0036d50c10a4c8879d1ee9f24a7502ec2cfb264a61951c8d500dfc897ec35`.

- 4.386 eventos dos períodos 1–4; 21 da disputa excluídos.
- 389 sequências: 286 `bounded` e 103 `review`. Entre os diagnósticos,
  79 sequências divergem da equipe de posse rotulada, 19 têm início incerto,
  19 têm fim incerto e duas carregam recuo de relógio; essas categorias se sobrepõem.
- 4.265 eventos atribuídos e 121 não atribuídos, sem sobreposição.
- Todas as 30 finalizações da partida atribuídas uma única vez.
- 309 evidências de limite conciliadas individualmente com SQL, inclusive instante
  calculado: 142 reinícios, 56 saídas, 88 registros de falta sem vantagem,
  oito impedimentos, nove marcadores de lesão e seis gols.
- 116 observações de paralisação: 98 janelas utilizáveis e 18 para revisão.
  As 18 não têm reinício explícito; nove também têm intervalo negativo.
- Janelas utilizáveis somam 3.324.659,202 ms nesta fonte. **Esse valor não é o
  tempo total parado da partida** e não fornece o complemento de bola em jogo.

O SQL confere as evidências e seus instantes contra o payload original. As
invariantes adicionais verificam população, ordem, períodos, equipe das ações
com bola, cobertura das finalizações e ausência de cruzamento de paradas ou
reinícios dentro dos ataques. Isso não equivale a uma anotação manual independente
de cada ataque por vídeo; os casos `review` continuam identificados para análise.

Reproduzir, em modo somente leitura e com partida/banco explícitos:

```sh
python scripts/verify_attack_sequences.py --database-url "$DATABASE_URL" --match-id 3869685
```

Testes sintéticos incluem reinícios na mesma posse, rebote, gol, pênalti,
vantagem vinculada, pares de falta, marcadores de lesão, mudança de equipe sem
mudança de rótulo, ordem/relógio, durações ausentes e negativas, fim de fonte,
separação de períodos/disputa e cobertura exata dos eventos.

Referência semântica: [especificação oficial StatsBomb Open Data v1.1](https://raw.githubusercontent.com/statsbomb/open-data/master/doc/StatsBomb%20Open%20Data%20Specification%20v1.1.pdf),
especialmente os campos de duração, faltas/vantagem e Injury Stoppage.

Validação automatizada: Ruff, formatação e mypy aprovados; 1.827 testes passaram
com PostgreSQL em `football_test`, incluindo 22 testes desta política. O aviso
TestClient/HTTPX existente permanece. Não houve novo build Docker ou ciclo
completo de migrações nesta etapa.

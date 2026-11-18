# Executor de dribles, criação, movimento e controle — catálogo 1.4.0

Esta entrega conecta as 60 definições numéricas restantes: dribles (4), criação (9), movimento/progressão (30) e controle da bola (17). O executor passa a cobrir as **204 definições numéricas** do catálogo (147 métricas e 57 diagnósticos), ou 203 para equipes porque tempo individual em campo não é duração de equipe. O total inclui estados e recortes, não 204 conceitos esportivos independentes.

Os 38 recursos estruturados/visuais e as sete lacunas continuam separados e não são habilitados como escalares. Não foram criados frontend, rota, comando interativo, plot novo ou novas tabelas. A operação continua sendo `run_query(engine, MetricQuery(...))`, em transação somente de leitura e snapshot consistente, conforme [Executor inicial](query-executor.md).

## Exemplo

```python
query = MetricQuery(
    match_ids=(3869151, 3869321),
    group_by="player",
    metrics=(
        "dribbles.completion_pct",
        "creation.recorded_shot_assists",
        "movement.pass.countable_progression.true",
        "movement.carry.countable_penalty_area_entry.true",
        "ball_control.miscontrol",
    ),
    aggregation="per_match",
    periods=(1, 2),
)
result = run_query(engine, query)
```

Os seletores de famílias continuam internos às definições. Não foram liberados filtros externos por pressão, zona, bola parada, situação do placar, janela ou taxas por 90. Esses recortes exigirão integração e validação próprias.

## Dribles e população

O resumo ofensivo agora inclui os Dribble normalizados, reutilizando os agregadores existentes de jogador/equipe. Tentados = completos + incompletos; aproveitamento é completos/tentados, arredondado ROUND_HALF_UP em duas casas. Entre partidas, a razão é recomposta pelos totais. Nenhuma tentativa significa percentual não aplicável, não 0%.

Um jogador com apenas dribles aparece como observado no resumo ofensivo; passes/chutes podem ser zero com presença. Dribble continua distinto de Carry.

## Criação

`build_creation_statistics` usa as referências explícitas assisted_shot_id/key_pass_id, o catálogo completo dos eventos e a classificação existente dos pares. O recorte é determinado pelo período do passe.

`recorded_shot_assists` e `recorded_goal_assists` contam passes com flags positivas em pares utilizáveis, não todas as flags isoladas nem uma assistência inferida por proximidade. Preservam-se candidatos, utilizáveis, revisão, passes com ambas/as duas flags e pares sem flag positiva.

Avisos:

- `creation_pairs_require_review`: há pares em revisão nessa população.
- `creation_positive_flags_without_link`: há flags positivas sem vínculo.
- `creation_reference_issues_in_match`: problema de referência no recorte; aviso conservador da partida.
- `creation_cross_scope_pairs_in_match`: pares em revisão cujo passe está fora do recorte e chute dentro, conforme a definição do módulo existente.

Se passe está dentro e chute fora, o par permanece em review_pairs da população do passe. Não se troca o denominador para o período do chute. Não há xA nesta entrega.

## Movimento e progressão

O executor usa `build_movement_statistics`, `classify_movement_progression` e a geometria existentes. As cinco classificações, separadas para Pass e Carry, mantêm true/false/unknown:

- eligible_for_count;
- geometrically_progressive;
- countable_progression;
- countable_final_third_entry;
- countable_penalty_area_entry.

A política continua `project_dx10_goal_distance25`: dx pelo menos 10 e redução de distância ao gol de pelo menos 25%. Passes completos são elegíveis; Carry tem sua própria elegibilidade. Geometria progressiva não torna um passe incompleto em progressão contável. Resultado de passe Unknown permanece elegibilidade desconhecida.

Coordenadas fora dos limites ou outra geometria não utilizável mantêm classificação desconhecida nos campos que dependem dela. Não se aplica a simplificação lógica que transforma um passe incompleto com geometria desconhecida em classificação falsa. A elegibilidade pode ser conhecida independentemente da geometria.

**Ausência de coordenadas obrigatórias no payload é diferente:** o normalizador espacial existente rejeita a fonte inválida. O executor não relaxa esse contrato para inventar um evento normalizado. Os testes cobrem as duas situações.

As unidades são as coordenadas StatsBomb 120×80, não metros percorridos, velocidade ou trajetória medida.

Avisos `movement_geometry_unavailable` e `movement_metric_unknown_classifications` são preservados e propagados à agregação. As contagens de estados são conhecidas quando todas as ações foram classificadas, inclusive no estado unknown. Um valor disponível de `...true` não prova inexistência de progressões entre as ações desconhecidas; deve ser interpretado com `...unknown` e os avisos. A cobertura mede a população classificada, não a completude física das posições.

## Controle da bola

`build_ball_control_statistics` recebe as ações de controle, geometria e metadados de pressão alinhados por evento. As 17 categorias são somadas nas células disjuntas por categoria; ter várias combinações de zona/pressão não duplica um evento.

Recovery failure ausente mantém a categoria recovery_failure_unknown; não vira recuperação bem-sucedida. Miscontrol, Dispossessed, resultados de passes/dribles, interceptações, tackles, aerial lost, pressões, driblado, bloqueios e cortes continuam sendo evidências observadas. Não se convertem automaticamente em perdas/ganhos efetivos de posse.

`ball_control_zone_unavailable` indica geometria de zona indisponível sem apagar a categoria conhecida do evento. Pressão e zonas permanecem disponíveis no recurso estruturado; não foram transformadas em novos filtros de MetricQuery.

## Zero, desconhecido e integridade

As três novas estruturas são comparadas com escalação e participação no recorte antes de produzir números. Ação de jogador não listado, equipe incompatível ou participação insuficiente diante de ação observada produz erro. Sem ações e com presença, zero; sem evidência de participação, desconhecido; presença conhecida só fora do recorte, não aplicável.

Vínculos, geometria e categorias são calculados pelos módulos existentes. Nenhuma fórmula esportiva foi reimplementada no frontend ou nos verificadores do produto. O SQL de aceitação é independente e fica apenas como artefato de conferência.

A mesma consulta pode combinar todas as 204 definições. Isso não implica que todos os recursos do projeto estejam concluídos: posse cronometrada, evolução por placar e outros itens reconhecidos como pendentes continuam pendentes.

## Aceitação nas 64 partidas

Resultados em `outputs/reviews/complementary-executor-2026-11-16/`, com JSON por partida compactado em gzip e scripts de execução/verificação preservados:

- 64 partidas processadas sem falha; 128 linhas equipe–partida e 3.244 jogador–partida.
- 60 definições por linha.
- **126.263 comparações numéricas com SQL independente**: 7.375 de dribles, 19.107 de criação, 63.690 de movimento e 36.091 de controle.
- 76.057 verificações de estado, separadas das comparações numéricas.
- **1.920 verificações** de agregados das 32 seleções, incluindo percentuais ponderados e propagação de avisos.
- Nenhuma divergência nesses recortes.
- Estados: 126.263 disponíveis, 74.940 desconhecidos e 1.117 não aplicáveis.
- 5.556 ocorrências de aviso de classificações de movimento desconhecidas nas células exportadas; não são 5.556 eventos únicos. Resultados Unknown de passes podem afetar várias classificações e aparecer tanto em jogador quanto em equipe.

Não houve aviso de criação em revisão nesse conjunto real; os testes sintéticos exercitam pares em revisão, referências entre períodos e flags sem vínculo. A aceitação real usa períodos completos; testes também cobrem filtros e os casos extremos acima. A checagem fina de presença em cada instante continua fora desta integração.

O banco estava desligado no começo da aceitação; o Docker local foi iniciado e a validação executada após o PostgreSQL ficar saudável. Nenhum dado foi importado ou alterado por esta entrega.

Próxima etapa: contrato de consulta e resultado para análises estruturadas — redes, mapas, fragmentos de posse, ataques e janelas — antes de construir interfaces.

Validação final: 2.126 testes passaram, com um aviso conhecido de depreciação. Foram acrescentados 11 testes desta integração; Ruff, formatação e mypy aprovados (153 arquivos fonte).

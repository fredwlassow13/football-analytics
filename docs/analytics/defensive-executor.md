# Executor defensivo e de goleiros — catálogo 1.3.0

Atualização: catálogo 1.4.0, todas as 204 definições numéricas habilitadas. Veja [Integração complementar](complementary-executor.md). O texto abaixo preserva a entrega anterior.

Esta entrega conecta mais 108 definições ao serviço comum: defesa (70), duelos (10), jogo aéreo (4) e goleiros (24). Somadas às 36 anteriores, são 144 definições habilitadas para jogadores e 143 para equipes. Permanecem 60 definições não integradas (dribles, criação, movimento e controle), além dos recursos estruturados e lacunas. O número de definições inclui estados, contextos e diagnósticos; não equivale a 144 conceitos esportivos independentes.

Não há nova interface, rota, plot ou alteração no banco. `run_query(engine, MetricQuery(...))` continua sendo o serviço Python de leitura, com a mesma seleção/proveniência de fontes e transação REPEATABLE READ / READ ONLY descritas em [Executor inicial](query-executor.md).

## Exemplo

```python
query = MetricQuery(
    match_ids=(3869151, 3869321),
    group_by="player",
    metrics=(
        "defense.interceptions",
        "defense.unique_sources",
        "duels.tackles.won",
        "aerial.win_pct",
        "goalkeepers.counts.on_target_saves",
    ),
    aggregation="per_match",
    periods=(1, 2),
)
result = run_query(engine, query)
```

Todas as métricas podem ser combinadas com as famílias anteriores. Filtros externos continuam limitados a partidas explícitas, equipe/jogador e períodos; não foram adicionados filtros por situação do placar, bola parada, por 90 ou janelas.

## Defesa e duelos

O adaptador usa `build_defensive_summary` e `build_defensive_roster`, apoiados nos normalizadores existentes. A presença/ausência é estabelecida para a população defensiva, independentemente de o jogador ter ações ofensivas.

- Sem ações defensivas e com presença comprovada no recorte: zero.
- Sem participação suficiente: desconhecido.
- Participação conhecida fora do recorte: não aplicável.
- Ações sem participação compatível ou de jogador não listado: erro, não zero.

A união de IDs em `defense.unique_sources` conta uma vez um corte que também fornece evidência aérea. Não se soma indiscriminadamente categorias sobrepostas. A contagem de Interception inclui seus resultados preservados; não é contagem automática de recuperações bem-sucedidas.

Flags true/false/unknown continuam separadas. Uma flag ausente não vira falsa; a contagem de flags desconhecidas pode ser conhecida quando o payload inteiro está disponível.

Tackles mantêm os sete resultados individuais da fonte. Tackle não foi renomeado como carrinho. Vitórias aéreas usam a evidência explícita dos eventos permitidos, enquanto Aerial Lost vem de Duel; são reunidas pelas estatísticas aéreas existentes.

Taxa aérea sem disputas é não aplicável. Agregados usam soma das vitórias dividida pela soma dos disputados, sem média simples dos percentuais por partida.

## Goleiros e aplicabilidade da função

Os 24 campos reutilizam `build_goalkeeper_statistics`, incluindo os vínculos explícitos goleiro–chute e sua classificação. Os quatro contextos são geral, pênalti, não pênalti e situação de pênalti desconhecida.

A contagem zero exige aplicabilidade da função. O adaptador usa as informações táticas normalizadas e `build_team_tactical_timeline`, cruzando seus segmentos com participação e períodos selecionados:

- Ação Goal Keeper observada, com participação compatível: função observada.
- Função Goalkeeper registrada em algum segmento ativo do recorte, sem ações de goleiro: zero com presença confirmada.
- Apenas posições de linha conhecidas em todos os segmentos ativos: não aplicável.
- Função insuficientemente estabelecida, incluindo posições não registradas de substitutos: desconhecido.
- Ausência de evidência de participação: desconhecido, mesmo que não haja ações.

É uma política conservadora sobre a **última posição registrada**, não uma medição por tracking nem prova de função em cada instante. Ações de goleiro podem estabelecer função observada mesmo que snapshots anteriores estejam desatualizados. A checagem de participação é no recorte, não a validação temporal fina de cada ação.

Dados táticos inválidos ou ausentes causam erro para consultas de goleiros; não afetam consultas das famílias que não dependem deles. A equipe pode ter zero quando existe goleiro com função/presença estabelecidas; sem essa evidência, fica desconhecida.

## Qualidade e interpretação

`review_pairs` é diagnóstico; pares em revisão não são contados automaticamente como defesas ou gols confirmados. Flags de revisão permanecem em `MetricValue.review_reasons` e são propagadas aos agregados:

- `goalkeeper_pairs_require_review`: pares do contexto pedem revisão.
- `goalkeeper_source_link_issues_in_match`: problemas de referência ou chutes sem vínculo no recorte da partida; aviso conservador, não atribuição de culpa a um goleiro.
- `unlinked_goalkeeper_shot_action`: ação de goleiro de tipo ligado a chute sem vínculo. Intervenções sem chute, como Keeper Sweeper, não recebem esse aviso por estarem sem par.

Os valores disponíveis são contagens das categorias classificadas, não promessa de que todas as finalizações foram conciliadas. A cobertura nesses campos se refere aos pares candidatos classificados, incluindo revisão; avisos de vínculo devem ser considerados ao interpretar desempenho. Tipos/resultados de intervenções não ligadas a chute continuam no recurso estruturado correspondente.

Gols de Shot sofridos não incluem automaticamente gols contra. Não se trata de PSxG ou gols evitados. Disputa continua excluída destas consultas.

## Aceitação nas 64 partidas

Arquivos em `outputs/reviews/defensive-executor-2026-11-16/`:

- 64 partidas processadas com sucesso, zero falhas.
- 108 novas definições: 128 linhas equipe–partida e 3.244 linhas jogador–partida.
- 177.785 valores defensivos e 6.216 valores de goleiros comparados com SQL sobre os payloads brutos.
- 180.175 verificações de estado; não são todas comparações numéricas. A aplicabilidade tática é exercitada nos testes sintéticos, enquanto o SQL de aceitação confere valores disponíveis, estados e ausência de ações defensivas em desconhecidos.
- Vínculos e categorias dos pares de goleiros reconstruídos em SQL independente para a comparação numérica, não apenas totais agregados.
- 3.456 verificações dos agregados das 32 seleções contra os resultados por partida previamente conciliados.
- Nenhuma divergência nos recortes conferidos; nenhum aviso de qualidade de goleiros nos resultados reais deste conjunto.

Estados dos resultados exportados: 184.001 disponíveis, 148.308 desconhecidos e 31.867 não aplicáveis. Não aplicável inclui jogadores de linha nas métricas de goleiro e taxas aéreas sem disputas; desconhecido não foi convertido em zero.

Os JSON por partida e agregado estão compactados em gzip. `run_runtime.py` e `verify_runtime.py` preservam os procedimentos de execução e aceitação; não substituem o serviço de produção. A verificação real cobre os períodos completos; testes sintéticos também exercitam seleção de períodos, deduplicação, ausência de função, pares em revisão, pênaltis e tipos desconhecidos.

Próxima integração: dribles, criação, progressão/movimento e controle da bola. Redes, mapas e sequências continuarão exigindo contratos estruturados próprios, além de métricas escalares.

Validação final: 2.115 testes aprovados, com um aviso conhecido de depreciação. Dez testes específicos novos cobrem esta integração; Ruff, formatação e mypy aprovados (152 arquivos fonte).

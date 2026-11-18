# Mapas defensivos por tipo, grupo e período

Política `unique_defensive_and_aerial_sources`, versão `1.0.0`. Seleção e grade em
`ingestion/defensive_map.py`; apresentação em `visualization/defensive_map.py`.
O cálculo independe de Matplotlib; seus pontos, células e IDs podem alimentar
uma futura interface. Não há novo endpoint nem persistência analítica nesta etapa.

## População e filtros

O mapa usa a união das ações do resumo defensivo e dos resultados aéreos,
deduplicada pelo ID original. Duel/Aerial Lost e Clearance com vitória aérea
não recebem um segundo ponto pela projeção aérea. Pass, Shot e Miscontrol só
entram quando têm evidência aérea explícita; não se incluem todos os passes,
chutes ou erros de controle no mapa defensivo.

| Grupo | Fontes |
| --- | --- |
| `all` | União única do resumo defensivo e das fontes aéreas |
| `recoveries` | Interception, Ball Recovery |
| `duels` | Duel, incluindo Tackle e Aerial Lost |
| `pressure` | Pressure, Dribbled Past |
| `blocks_clearances` | Block, Clearance |
| `fouls` | Foul Committed, Foul Won |
| `aerials` | Somente resultados aéreos com evidência normalizada |

Os grupos têm sobreposições: por exemplo, um corte aéreo pertence a
`blocks_clearances` e `aerials`. Não somar painéis desses grupos como eventos
independentes. O grupo `all` usa a união por ID.

Filtros por grupo e tipos originais são combinados por interseção; a seleção por
equipe é obrigatória e por jogador é opcional. Períodos disponíveis vêm do relógio
da partida; são aceitos períodos individuais e combinações válidas dos períodos
1–4. Disputa de pênaltis, recortes vazios, repetidos ou indisponíveis são rejeitados
como seleção; eventos do período 5 da entrada ficam fora dos mapas.

Os nomes dos grupos descrevem registros, sem pressupor sucesso. Recuperações
podem ter falha, interceptações podem resultar em perda, bloqueios podem ser
ofensivos e faltas sofridas podem ocorrer durante um ataque. O mapa não elimina
esses casos nem converte flags ausentes em sucesso. Resultados e subtipos
continuam disponíveis nas análises detalhadas; o filtro espacial atual é por
grupo/tipo, não por resultado.

## Coordenadas, cobertura e comparação

Um ponto inicial por evento, em coordenadas originais StatsBomb 120 × 80.
Não há espelhamento, suavização, interpolação ou preenchimento de posições.
Um evento sem posição ou fora do campo aparece em `location_issues`; permanece
na população selecionada e não ocupa célula. A legenda explicita localizados,
selecionados e registros sem posição utilizável. Ausência completa de amostra
não vira percentual zero: a distribuição percentual do cálculo é `None`.

A grade padrão é 12 × 8, configurável. Limites x=120/y=80 pertencem à última
coluna/linha. Cada célula preserva seus IDs na ordem da fonte. Os percentuais são
`100 × eventos da célula / eventos com posição utilizável no recorte`. Não usam
como denominador os eventos sem posição nem medem posse, aproveitamento,
intensidade por minuto ou permanência física de jogadores.

O renderizador aceita um a quatro painéis da mesma fonte, com a mesma grade.
Compartilha escala de cores, mostra zero em cinza e oferece contagem ou percentual.
A escala pode ser fixada entre arquivos por `scale_max` na função Python;
valores que truncariam a amostra são rejeitados. Escalas automáticas são comuns
entre os painéis de um arquivo, mas podem variar entre arquivos distintos.

## Exportação e conciliação

O comando lê somente o payload já armazenado; não busca nem altera dados no banco.
Recebe partida e conexão explicitamente, concilia cada seleção e célula com SQL
e exporta PNG, SVG e JSON. O JSON mantém hash da fonte, filtros, pontos, cobertura
e IDs de cada célula. Requer as dependências de visualização instaladas.

```sh
python scripts/export_defensive_maps.py --database-url "$DATABASE_URL" \
  --match-id 3869685 --periods 1 2 --compare-periods \
  --output outputs/maps/defense/team-periods

python scripts/export_defensive_maps.py --database-url "$DATABASE_URL" \
  --match-id 3869685 --group recoveries \
  --output outputs/maps/defense/recoveries

python scripts/export_defensive_maps.py --database-url "$DATABASE_URL" \
  --match-id 3869685 --event-types Pressure --periods 1 2 3 4 \
  --output outputs/maps/defense/pressures

python scripts/export_defensive_maps.py --database-url "$DATABASE_URL" \
  --match-id 3869685 --team-id 779 --player-id 5503 \
  --output outputs/maps/defense/messi
```

Outras opções: `--group aerials`, `--mode percentage`, `--columns`, `--rows`.
O limite de quatro painéis exige dividir a comparação dos quatro períodos e
duas equipes em dois arquivos. Não são materializadas todas as combinações.

## Checkpoint da final — 12/11/2026

SHA-256 da fonte:
`a0b0036d50c10a4c8879d1ee9f24a7502ec2cfb264a61951c8d500dfc897ec35`.

| Período, grupo `all` | Argentina | França |
| --- | ---: | ---: |
| 1 | 142 | 149 |
| 2 | 177 | 175 |
| 3 | 48 | 59 |
| 4 | 54 | 49 |
| Total | 421 | 432 |

Os 853 registros recompõem as fontes únicas do resumo integrado (820 ações e
82 resultados aéreos, com 49 IDs compartilhados). Todos têm posição utilizável
neste recorte; essa cobertura não é presumida para outras partidas.

Também foram gerados e conciliados: interceptações/recuperações (72 Argentina,
71 França), pressões (167 e 194), resultados aéreos (41 por equipe, visualizados
em percentual) e fontes defensivas/aéreas de Messi (34). Os seis exemplos em
`outputs/maps/defense/` têm PNG, SVG e JSON; os outputs são locais e ignorados
pelo Git. PNGs foram inspecionados, incluindo ajuste do espaço entre composição
da amostra e rodapé nos painéis de uma linha.

A integração do resumo defensivo aos jogadores listados e à participação temporal
foi implementada posteriormente em [resumo dos listados](defensive-roster.md), distinguindo `observed_events`, `zero_with_presence`,
`not_applicable` e `unknown`. Um mapa vazio, isoladamente, não demonstra ausência
de ações de quem não tem participação conhecida.

Validação após os mapas: Ruff, formatação e mypy aprovados; **1.848 testes
passaram**, incluindo PostgreSQL em `football_test` e 21 casos dos mapas.
Permanece apenas o aviso TestClient/HTTPX já conhecido. Build Docker e ciclo
completo de migrações não foram reexecutados nesta etapa.

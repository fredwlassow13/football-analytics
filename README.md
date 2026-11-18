# Football Analytics

Plataforma de análise de futebol em Python 3.13, FastAPI e PostgreSQL 17,
organizada como monólito modular. O desenvolvimento começa pela Copa do Mundo
2022, aprofundando uma partida antes de replicar as análises.

## Estado atual

| Área | Implementado |
| --- | --- |
| Infraestrutura | Docker Compose, SQLAlchemy, Alembic, Ruff, MyPy, Pytest e CI |
| Fonte | StatsBomb Open Data, com adapters independentes do formato interno |
| Copa 2022 | Competições/temporadas, 32 equipes e 64 partidas importadas no ambiente validado |
| Final de 2022 | 50 jogadores listados, escalações e classificação de titulares |
| Participação temporal | Relógio por período, entradas/saídas, intervalos e conferência coletiva |
| Eventos e contexto | Normalização de eventos, passes, finalizações, dribles, posições, posse e coordenadas |
| Estatísticas ofensivas | Agregações individuais/coletivas de passes, finalizações, chutes ao alvo, dribles e xG; resumo unificado por período e junção aos listados |
| Análise tática | Formações registradas, mudanças de posição, participação e catálogo automático de trechos |
| Visualização | Mapas de finalizações, vista do gol, redes de passes e heatmaps individuais de ações |
| Armazenamento analítico | Participação persistida com versão e hashes das fontes; demais análises ainda em memória/scripts |
| API | Competições, temporadas, partidas e participação de um cálculo explícito |
| Auditoria | Execuções, erros e respostas originais com SHA-256 |

Os dados são carregados por comandos explícitos: não acompanham o código no Git.
O fluxo temporal da final foi conferido com 50 resultados, 35 jogadores com
intervalos, 15 com duração ausente e 36 intervalos. Esse recorte não representa
uma validação de todos os casos possíveis de futebol.

A primeira métrica é `elapsed_on_field_ms`, fórmula `1.0.0`. Ela inclui acréscimos
e paralisações dentro dos períodos, desconta ausências temporárias identificadas
e exclui intervalos entre períodos e disputa de pênaltis. **Não equivale a tempo de
bola em jogo nem a minutos estatísticos convencionais usados em indicadores por 90.**
A definição completa está em [participação temporal](docs/analytics/elapsed-on-field.md).

Implementação em módulos, conferência por scripts, persistência e disponibilidade
na API são estados distintos. Os endpoints atuais ainda não expõem as novas
estatísticas ofensivas, redes ou heatmaps. O plano de consolidação está em
[análises e roteiro](#análises-táticas-estado-e-roteiro).

## Executar com Docker

Na raiz do repositório, com Docker em execução:

```sh
docker compose up --build -d
```

O Compose aguarda o PostgreSQL e aplica as migrações antes de iniciar a API.

- Processo: <http://localhost:8000/health>
- Disponibilidade do banco: <http://localhost:8000/health/ready>
- Documentação OpenAPI: <http://localhost:8000/docs>
- PostgreSQL no host: `localhost:5433`; entre containers: `db:5432`.

```sh
docker compose logs backend migrate
docker compose down
```

O volume do PostgreSQL preserva os dados ao parar os serviços. As credenciais
incluídas no Compose são de desenvolvimento local.

## Desenvolvimento no VS Code

Abra a pasta `football-analytics` como raiz do workspace:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -c constraints.txt -e '.[dev,visualization]'
```

Se ainda não houver `.env`, copie `.env.example` para `.env`. O exemplo usa o
PostgreSQL local na porta 5433. Nunca versione `.env` ou `.venv`.

Para executar a API fora do Docker:

```sh
docker compose up -d db
docker compose stop backend
alembic upgrade head
uvicorn football_analytics.main:app --reload
```

Selecione `.venv/bin/python` em **Python: Select Interpreter**. Se um novo terminal
não encontrar Ruff, MyPy ou Pytest, ative novamente o ambiente virtual.

## Reproduzir o recorte validado

Com `.venv` ativo, banco disponível e migrações aplicadas, execute nesta ordem:

```sh
python -m football_analytics.ingestion.catalog --competition 43 --season 106
python -m football_analytics.ingestion.world_cup_teams
python -m football_analytics.ingestion.world_cup_matches
python -m football_analytics.ingestion.final_players
python -m football_analytics.ingestion.match_lineups --match-id 3869685
python -m football_analytics.ingestion.match_participation_job --match-id 3869685
```

`43`, `106` e `3869685` são IDs externos StatsBomb. UUIDs internos são gerados pela
aplicação e variam entre instalações. O comando do cálculo informa seu UUID.
Equipes e partidas ainda usam o recorte fixo Copa 2022; `final_players` seleciona a
final explicitamente. Escalações e participação recebem `--match-id`, mas exigem
os cadastros e mapeamentos prévios. Não há execução em lote de todo o catálogo.

Para executar esses módulos no container, use
`docker compose exec backend python -m ...`. Após alterações no código ou nas
migrações, reconstrua a imagem com `docker compose up --build -d`.

As cargas repetidas preservam novas execuções e payloads em `raw` sem duplicar os
cadastros. O cálculo reutiliza o mesmo resultado se partida, métrica, versão e
hashes das duas fontes forem iguais. Fonte ou fórmula diferente identifica outro
cálculo, preservando o anterior.

Detalhes: [catálogo](docs/ingestion/catalog.md) e
[pipeline de participação](docs/ingestion/participation.md).

Esses comandos reproduzem a ingestão e a participação. As conferências SQL e
exportações táticas posteriores foram executadas por scripts de terminal; ainda
não existe um comando único versionado que reproduza todas elas. Essa integração
é uma pendência do roteiro. Os artefatos em `outputs/` são locais e ignorados pelo
Git; não são necessários para executar a API atual.

## Consultar a API

| Endpoint | Origem e comportamento |
| --- | --- |
| `GET /api/v1/providers/statsbomb/competitions` | Prévia externa; acessa StatsBomb; `country`, `limit`, `offset` |
| `GET /api/v1/competitions` | Banco; paginação `limit`/`offset` |
| `GET /api/v1/competitions/{id}/seasons` | Banco; UUID interno da competição |
| `GET /api/v1/matches` | Banco; filtro `season_id`, paginação `limit`/`offset` |
| `GET /api/v1/matches/{id}/participation?calculation_id={uuid}` | Banco; resultado do cálculo solicitado para a partida |

Use o UUID **interno** da partida e o UUID emitido pelo comando de cálculo. O
endpoint de participação retorna nomes, titularidade, evidência, duração em
milissegundos, apresentação legível e intervalos. Uma duração ausente é `null`;
não é convertida em zero. Cálculo inexistente ou de outra partida retorna 404;
parâmetros inválidos retornam 422. Falhas SQLAlchemy recebem resposta 503.
O limite 100 nas listagens significa registros por página, não cota de requisições.

## Qualidade e testes

```sh
ruff check .
ruff format --check .
mypy src
pytest -q
```

Os testes que exigem PostgreSQL são ignorados quando `TEST_DATABASE_URL` não está
definido. Para rodar a suíte completa, crie um banco separado uma única vez:

```sh
docker compose exec db createdb -U football football_test
```

Se ele já existir, pule a criação e execute:

```sh
DATABASE_URL=postgresql+psycopg://football:football_local@localhost:5433/football_test alembic upgrade head
TEST_DATABASE_URL=postgresql+psycopg://football:football_local@localhost:5433/football_test pytest -q
```

Nunca aponte os testes ao banco principal. Alguns testes de pipeline preservam
fixtures/auditoria no banco de testes; outros usam rollback. As consultas externas
são simuladas nos testes automatizados. Há um aviso de depreciação na integração
TestClient/HTTPX das versões atuais, sem falhas na suíte validada.

O GitHub Actions verifica lint, formatação, tipos, ciclo de migrações em banco
isolado, testes e build Docker. SQLAlchemy é usado com SQL explícito; ainda não há
modelos ORM registrados. As migrações são manuais; `--autogenerate` está bloqueado
com metadata vazio.

Checkpoint de fechamento em 11/11/2026: Ruff, formatação e mypy aprovados;
925 testes passaram com `TEST_DATABASE_URL` apontando para `football_test`, com
um aviso de depreciação TestClient/HTTPX. Esse checkpoint não significa que o
build Docker ou o ciclo completo de migrações tenham sido reexecutados localmente.

Checkpoint de retomada em 11/11/2026, após interceptações e recuperações:
Ruff, formatação e mypy aprovados; **972 testes passaram** com `TEST_DATABASE_URL`
apontando para `football_test`, mantendo o mesmo aviso TestClient/HTTPX.
Os 143 eventos desse novo bloco foram conciliados individualmente com SQL.
Build Docker e ciclo completo de migrações não foram reexecutados nesta etapa.

## Análises táticas: estado e roteiro

### Objetivo e escopo

Consolidar a análise por equipe e jogador da final Argentina × França de 2022
(StatsBomb `3869685`) antes de processar os eventos de todas as 64 partidas da
Copa. O catálogo de partidas já foi importado; isso não significa que a análise
tática de todos os jogos esteja implementada ou validada. Depois dessa expansão,
avaliar comparações entre competições e temporadas e, em outra etapa, modelos
preditivos com separação temporal entre treino e avaliação.

### Inventário das análises

| Análise | Implementado e conferido | Pendências |
| --- | --- | --- |
| Finalizações e gols | Adapter, resultados individuais/coletivos, recortes, parte do corpo, técnica, tipo, mapas e conciliação SQL | API e tratamento do placar com gols contra; gols de Shot não substituem o placar completo |
| Chutes ao alvo | Definição e agregações por jogador/equipe no mesmo recorte | Exposição na API |
| xG | Valores da StatsBomb, agregações individuais/coletivas, cobertura e separação de pênaltis/não pênaltis; mapa com áreas proporcionais | Evolução temporal e exposição na API; não é um modelo próprio de xG |
| Passes | Tentados, completos, não completos, desconhecidos e aproveitamento por jogador/equipe; destinatário, tipo e recortes; conciliação SQL | API e atributos de criação/progressão |
| Dribles | Tentativas, completos, incompletos e aproveitamento por jogador/equipe, com recortes e conciliação SQL | Exposição na API |
| Redes de passes | Conexões dirigidas, IDs de origem, médias das origens dos passes e camisas reais; seleção por trechos | Consulta integrada, filtros de apresentação e comparação com escala consistente |
| Formações e posições | Starting XI, Tactical Shift, comparação de registros e linha temporal combinada com presença em campo | Resumos por formação/trecho; não inferir posições ausentes entre registros |
| Heatmaps individuais | Origens de passes e seleção geral de ações com bola, defensivas, disputas e outras ações; grade, contagens, percentuais, composição e renderização | Filtros explícitos por grupo/tipo, comparação padronizada e integração na API |
| Heatmaps por equipe | Coordenadas dos eventos disponíveis | Agregação e apresentação próprias |
| Ações defensivas | Adapters, resumo por jogador/equipe, integração aos listados/participação e mapas por tipo/grupo/período, com IDs e cobertura | API; auditoria reutilizável do instante de cada evento no bloco 5 |
| Interceptações e recuperações | Contratos e adapters com resultados explícitos e flags ausentes preservados; 143 eventos da final conciliados individualmente com SQL | Agregações por jogador/equipe, recortes comuns e integração ao resumo defensivo |
| Duelos e desarmes registrados | Contrato, adapter e agregações por jogador/equipe; resultados separados e conferência SQL dos 98 eventos Duel | Integração dos recortes, participação e resumo defensivo; não reduzir resultados distintos a um único booleano |
| Disputas aéreas | Vitórias explicitamente marcadas e derrotas Aerial Lost, preservando o tipo original; contagens e aproveitamento por jogador/equipe conferidos com SQL | Integração ao resumo defensivo e filtros comuns; não somar novamente eventos já usados em passes ou finalizações |
| Goleiros | Eventos originais disponíveis e resultados de chutes defendidos normalizados | Adapter de Goal Keeper, vinculação aos chutes e estatísticas por goleiro/equipe |
| Posse | Fragmentos, ataques observados e janelas de paralisação com diagnósticos e proveniência; limites conciliados com SQL | Duração/percentuais com cobertura própria; revisão dos casos de limites incertos |

As implementações principais estão em `ingestion/pass_statistics.py`,
`shot_statistics.py`, `shot_xg_statistics.py`, `dribble_statistics.py`,
`pass_network*.py`, `window_pass_network.py`, `tactical_*.py`,
`team_tactical_timeline.py`, `player_activity*.py` e `player_pass_heatmap.py`.
Os resumos coletivos e a consolidação usam `team_*_statistics.py`,
`offensive_scope.py`, `offensive_summary.py`, `player_scope_participation.py` e
`offensive_roster.py`.
Os duelos e resultados aéreos usam `duel_statistics.py`, `team_duel_statistics.py`,
`aerial_statistics.py`, `team_aerial_statistics.py` e os adapters `duels.py` e
`aerial_duels.py`.
Interceptações e recuperações usam os adapters `interceptions.py` e
`ball_recoveries.py`; definições e checkpoint estão em
[interceptações e recuperações](docs/analytics/interceptions-and-recoveries.md).
Os adapters ficam em `integrations/statsbomb/` e os renderizadores em
`visualization/`.

### Checkpoints da final

Valores deste recorte, vinculados à fonte examinada; não são constantes universais
nem substituem uma nova conferência quando os dados mudarem.

| Indicador nos períodos 1–4 | Argentina | França |
| --- | --- | --- |
| Passes tentados | 693 | 570 |
| Passes completos | 560 | 434 |
| Passes de resultado desconhecido | 6 | 4 |
| Finalizações | 20 | 10 |
| Chutes ao alvo | 10 | 5 |
| Gols | 3 | 3 |
| xG StatsBomb, incluindo pênaltis durante a partida | 2.758305926 | 2.272617949 |
| Dribles tentados | 21 | 33 |
| Dribles completos | 9 | 19 |

- Fonte examinada: 4.407 eventos; a disputa de pênaltis contém oito finalizações
  e permanece separada das 30 finalizações da partida.
- Redes: 994 passes completos preservados em 272 conexões dirigidas; 34 jogadores
  com origens de passe. Uma conexão agregada não equivale a um evento individual.
- Tática: nove registros de formação/escalação e sete comparações de mudanças;
  catálogo com 24 trechos, incluindo um de duração zero. A exportação de conferência
  produziu 23 PNGs e 23 SVGs, sem descartar o trecho de duração zero do manifesto.
- Qualidade temporal: dois Ball Receipt* (índices 3438 e 3975) têm relógio fora do
  trecho atribuído por ordem da fonte. Foram sinalizados, sem corrigir timestamps;
  não bloquearam as redes de passes. Não presumir que são utilizáveis em métricas
  de duração ou presença sem revisão específica.
- Heatmap de Messi: 238 ações em 15 tipos; 60 passes, 74 recepções, 53 conduções,
  17 pressões e outros eventos. IDs e contagens foram conciliados com SQL; três
  eventos administrativos foram excluídos. As 238 ações foram conferidas contra
  os intervalos em campo e a ordem de entradas/saídas por script.
- Grade de apresentação de Messi: 24 × 16, sem suavização; 104 células ocupadas.
  O padrão da função de agregação continua 12 × 8: a resolução é configurável.
  No modo de contagem, zero tem cor cinza clara separada das contagens positivas.
- Consolidação ofensiva: 1.347 eventos (1.263 passes, 30 finalizações e 54 dribles),
  com categorias, cobertura de xG e proveniência conciliadas com SQL. Os sete
  recortes de períodos foram conferidos; parcelas recompõem seus totais, e as
  somas individuais coincidem com as coletivas.
- Resumo dos 50 listados na partida inteira: 34 com `observed_events`, um com
  `zero_with_presence` (Axel Disasi, 211.646 ms de presença e zero passes,
  finalizações e dribles) e 15 com `unknown`. Nos recortes menores,
  `not_applicable` indica jogador com intervalos reconstruídos em outros períodos,
  sem duração positiva no recorte solicitado. Durações dos recortes também foram
  conciliadas por jogador.
- Duelos: 98 eventos entre 28 jogadores, conferidos individualmente e nas
  agregações por jogador/equipe. Argentina: 26 Tackle e 24 Aerial Lost; França:
  31 Tackle e 17 Aerial Lost. Resultados de Tackle permanecem separados:
  Argentina com 8 won, 7 success_in_play, 5 lost_in_play e 6 lost_out; França
  com 9 won, 7 success_in_play, 11 lost_in_play e 4 lost_out.
- Resultados aéreos: 82 registros entre 27 jogadores, com IDs, resultados,
  evidências e percentuais conferidos com SQL. Argentina: 17 vitórias e 24
  derrotas (41,46%); França: 24 vitórias e 17 derrotas (58,54%). As vitórias
  estão marcadas em 32 passes, oito cortes e uma finalização. A igualdade
  de 41 vitórias e 41 derrotas é um checkpoint desta fonte, não uma exigência
  universal para outro dataset.
- Interceptações e recuperações: 143 eventos conciliados individualmente com SQL,
  incluindo identidade, ordem, relógio, equipe/jogador e detalhes. Argentina:
  13 interceptações e 59 recuperações registradas, nove com falha explícita;
  França: 15 e 56, quatro com falha explícita. Entre as 115 recuperações,
  101 não têm objeto de detalhes e uma tem somente `offensive=true`.
  Ausência de `recovery_failure` permanece `null`, sem inferir sucesso.
- Resumo defensivo integrado: 820 eventos nos nove tipos selecionados,
  82 resultados aéreos e 49 IDs compartilhados, totalizando 853 fontes
  únicas. As sobreposições correspondem a 41 Duel/Aerial Lost e oito
  Clearance com vitória aérea. Os sete recortes foram conciliados com
  SQL, incluindo jogadores, equipes, categorias, cobertura, resultados
  específicos de duelos, percentuais aéreos e IDs ordenados.
  Fontes únicas não equivalem a episódios defensivos ou recuperações
  de posse. O resumo inclui fontes aéreas de passes e finalizações.
- Goleiros: 44 eventos e 38 pares explícitos goleiro–chute, todos com
  evidência nos dois sentidos, sem ambiguidades nesta fonte. Na partida:
  36 ações, 30 pares, seis gols de Shot sofridos, nove defesas de chutes
  ao alvo e 15 demais finalizações. As seis ações sem chute correspondem
  a quatro Keeper Sweeper, uma Collected e uma Punch.
- Os sete recortes da partida e a disputa separada foram conciliados
  com SQL por goleiro/equipe, incluindo categorias, pênaltis e IDs.
  Na disputa: oito pares, seis gols, uma defesa ao alvo e uma finalização
  para fora.
- Participação: intervalos e ordem da fonte conferidos nos instantes das
  36 ações, dos 30 chutes e da atuação do goleiro em cada chute.
  Lloris e Martínez têm 141 min 24,610 s de elapsed_on_field_ms cada;
  isso não representa tempo de bola em jogo ou minutos convencionais
  para indicadores por 90. A participação temporal no período 5 não
  foi aferida.
- Fonte de escalações usada nessa conferência: SHA-256
  `2b00c4cbf027b36ae2ce99f183a0943c649bb5bdcb8324683a8603785beffed5`.
  A conferência temporal foi executada por script de terminal; sua
  transformação em validação reutilizável permanece no roteiro.
- Sequências de posse da fonte: 250 fragmentos, cobrindo 4.386 eventos
  dos períodos 1–4; 21 eventos da disputa permanecem separados.
  Composição, metadados e IDs foram conciliados com SQL.
- Auditoria temporal: 3.272 observações com duração e 1.114 eventos sem
  duração; 42 términos calculados ultrapassam o primeiro timestamp do
  fragmento seguinte. Nenhum ultrapassa o fim do próprio período.
  Os dois recuos de timestamp permanecem sinalizados, sem correção.
- Três fragmentos têm múltiplos play_pattern e mantêm sua composição.
  Diferenças temporais não representam automaticamente bola parada,
  duração de posse ou percentual de posse. Essas definições e a
  duração/percentual de posse continuam pendentes; a reconstrução operacional de ataques está em [política de ataques](docs/analytics/attack-sequences.md).
- Criação: 1.301 eventos normalizados e 19 vínculos específicos utilizáveis,
  todos com evidência nos dois sentidos. Foram confirmadas 17 flags
  shot_assist e duas goal_assist, sem sobreposição nesta fonte.
  Classificação, contagens, cobertura e IDs foram conciliados com SQL
  por jogador/equipe nos sete recortes da partida.
  Flags ausentes permanecem desconhecidas; gol com vínculo não gera
  assistência automaticamente. A disputa permanece excluída das contagens.
### Definições que devem permanecer explícitas

- **Chutes ao alvo:** `goal`, `saved` e `saved_to_post` na definição atual.
  Bloqueados, para fora, na trave sem defesa e `saved_off_target` não entram.
  Alterações nessa definição exigem revisão da métrica e dos testes.
- **Passes:** resultado desconhecido permanece identificado e entra no denominador
  atual de aproveitamento; não é convertido automaticamente em passe errado.
- **Drible e condução:** são eventos diferentes. Carry tem coordenadas de início
  e fim; isso não fornece a trajetória exata nem distância efetivamente percorrida.
- **Posição média:** a rede atual usa origens de todos os passes tentados no
  recorte, não posição contínua ou média de toda a movimentação. Suas setas ligam
  essas médias, não os pontos de início/fim de cada passe real.
- **Heatmap:** um ponto inicial por evento selecionado. Uma sequência de recepção,
  condução e passe conta três ações; não representa três posses, três toques ou
  tempo de permanência. Zero significa ausência de eventos selecionados na célula,
  não ausência física do atleta naquela região.
- **Composição:** defensivas, ações com bola, disputas e demais eventos devem ser
  filtráveis e identificáveis. Duelos não são todos desarmes; bloqueios não são
  todos defensivos. Contagens de tipos não substituem análise de resultado.
- **Disputas aéreas:** vitórias usam `aerial_won=true` em Pass, Clearance, Shot
  ou Miscontrol; derrotas usam Duel/Aerial Lost. Campo ausente ou false não
  vira derrota; null explícito na marcação exige revisão. O ID e o tipo original
  são preservados: um passe com vitória aérea é o mesmo evento em duas análises,
  não duas ações distintas. O total por jogador/equipe representa resultados
  registrados, não necessariamente episódios únicos na soma das duas equipes.
- **Interceptações e recuperações:** uma interceptação registrada pode ter resultado
  de perda; preservar as sete categorias de resultado. Ball Recovery inclui
  tentativas com falha e recuperações ofensivas. Flags ausentes permanecem `null`;
  `false` explícito permanece `false`, e `null` explícito na fonte exige revisão.
  Contar esses eventos não equivale a contar recuperações bem-sucedidas nem
  mudanças de posse únicas. Não somar eventos relacionados como episódios distintos.
- **Goleiros:** quantidade de eventos Goal Keeper não equivale a defesas. Vincular
  chutes e ações do goleiro com evidência, sem duplicar o mesmo episódio. Separar
  cobranças durante a partida da disputa de pênaltis. Não chamar xG pré-chute de
  avaliação pós-chute ou de gols evitados sem uma métrica apropriada.
- **Posse:** quantidade de eventos ou de passes não equivale a percentual de posse
  por tempo. Definir denominador, bola fora de jogo e lacunas antes de publicá-lo.
- **Ausência e zero:** preservar resultado desconhecido, campo ausente e falta de
  evidência; incluir zero apenas quando houver base para afirmar ausência da ação.
  A junção ofensiva mantém `counts_basis`: `observed_events`, `zero_with_presence`,
  `not_applicable` ou `unknown`. Zeros pressupõem entrada completa dos eventos
  ofensivos do recorte. A junção exige presença positiva no recorte para ações
  observadas; isso não substitui auditar o instante de cada evento contra os
  intervalos. O valor de xG ausente não é preenchido por zero.
- **Comparações:** manter grade e escala de cores comuns entre painéis. Percentual
  mostra distribuição espacial; não corrige duração nem mede intensidade por minuto.
  Definir separadamente taxas por tempo e convenções por 90.
- **Fontes:** preservar payloads, hashes, versões de política/fórmula e eventos de
  origem. Não corrigir coordenadas, relógios ou posições por suposição.
- **Progressão:** política própria `project_dx10_goal_distance25`, versão 1.0.0.
Exige avanço de pelo menos 10 unidades em x e redução de pelo menos 25%
da distância inicial ao centro do gol, no quadro nominal StatsBomb.
Contagens separam passes completos e conduções registradas, preservando
resultados desconhecidos. Bolas paradas e jogo corrido permanecem incluídos.
### Recortes previstos

| Recorte | Períodos |
| --- | --- |
| Primeiro tempo | 1 |
| Segundo tempo | 2 |
| Primeiro tempo da prorrogação | 3 |
| Segundo tempo da prorrogação | 4 |
| Tempo normal com acréscimos | 1, 2 |
| Prorrogação com acréscimos | 3, 4 |
| Partida inteira, sem disputa de pênaltis | 1, 2, 3, 4 |
| Disputa de pênaltis | 5, em análise separada |

As funções de seleção de heatmap já recebem períodos. A consolidação ofensiva usa
um seletor comum, recebe os períodos disponíveis do relógio normalizado, rejeita
recortes inexistentes e separa a disputa de pênaltis. Ainda faltam nomes
compartilhados pela API/frontend, opção de trecho tático no resumo unificado e
aplicação da mesma política às futuras métricas defensivas e de goleiros.

### Sequência de implementação

As caixas distinguem entregas implementadas/validadas das pendentes. Os itens
concluídos de cálculo não significam persistência ou disponibilidade na API.

1. **Consolidação ofensiva — núcleo implementado e validado na final.**
   - [x] Criar resumos por equipe para passes, finalizações/chutes ao alvo, dribles e xG.
   - [x] Reunir resumos por jogador e equipe com recortes padronizados e conciliação dos totais.
   - [x] Definir respostas para jogadores sem ações, sem intervalo no recorte e com evidência ausente.
   - [x] Incluir proveniência e definições nos contratos de cálculo; validar com SQL e testes.
2. **Defesa e disputas — núcleo, mapas e integração aos listados/participação implementados e validados na final.**
   - [x] Normalizar Duel, preservar resultados de Tackle e agregar por jogador/equipe; conferir eventos, categorias e IDs com SQL.
   - [x] Normalizar vitórias/derrotas aéreas e agregar contagens/aproveitamento por jogador/equipe; conferir com SQL.
   - [x] Normalizar resultados de interceptações e flags de recuperações de bola; conferir os 143 eventos e detalhes com SQL.
   - [x] Normalizar detalhes de bloqueios e cortes; conferir os 95 eventos com SQL.
- [x] Normalizar pressões, dribles sofridos e faltas, preservando atributos e vínculos; conferir os 484 eventos com SQL.
- [x] Criar agregações individuais/coletivas com recortes padronizados; conferir contagens, categorias, cobertura e IDs com SQL.
- [x] Integrar ações, duelos e resultados aéreos ao resumo defensivo, com proveniência e deduplicação por ID de origem.
- [x] Implementar mapas defensivos por tipo/grupo e período, com equipe/jogador, escala comum, cobertura de posições, PNG/SVG/JSON e conciliação SQL; ver [mapas defensivos](docs/analytics/defensive-maps.md).
- [x] Integrar o resumo defensivo aos jogadores listados e à participação, distinguindo zero, ausência e evidência desconhecida; conferir os 50 listados nos sete recortes com SQL. Ver [resumo defensivo dos listados](docs/analytics/defensive-roster.md).
3. **Goleiros — núcleo implementado e validado na final.**
   - [x] Normalizar tipos, resultados, atributos, coordenadas e vínculos dos 44 eventos Goal Keeper; conferir individualmente com SQL.
   - [x] Resolver os 38 pares goleiro–chute, preservando evidência nos dois sentidos e diagnósticos de qualidade.
   - [x] Classificar gols de Shot sofridos, defesas de chutes ao alvo, intervenções em chutes para fora e demais finalizações; preservar combinações que exigem revisão.
   - [x] Agregar por goleiro/equipe, separando pênaltis, não pênaltis e situação desconhecida; conferir os oito recortes com SQL.
   - [x] Conciliar por script a participação nos períodos 1–4: 36 ações de goleiro, 30 finalizadores e presença do goleiro nos 30 chutes.
   - [x] Separar a disputa de pênaltis e conferir suas referências de jogador/equipe.
   - [x] Aferir participação e elegibilidade no contexto inicial da disputa, sem estender os intervalos da partida; revisão externa vinculada aos hashes, 16 evidências conciliadas com SQL e 10 participantes observados classificados como elegíveis, sem divergências.
   - [ ] Avaliar mudanças de elegibilidade durante as cobranças.
   - [x] Implementar a conciliação de gols de Shot e evidências de gol contra, preservando vínculos ambíguos para revisão; conferir na final o placar 3–3 contra SQL, payload de partidas e cadastro, excluindo a disputa.
- [x] Validar a conciliação em partidas reais com gol contra; conferência independente nos arquivos brutos das 64 partidas incluiu três casos, sem divergências. Ver [evolução e placar](docs/analytics/match-evolution.md).
4. **Posse, criação e progressão.**
   - [x] Reconstruir sequências ofensivas observadas e janelas de paralisação, com limites explícitos, casos para revisão e conciliação SQL; ver [política de ataques](docs/analytics/attack-sequences.md).
   - [ ] Definir duração/percentual de posse com denominador e cobertura próprios; os spans e as janelas observadas não fornecem essas métricas.
   - [x] Normalizar flags de criação e vínculos específicos passe–chute.
   - [x] Resolver e classificar vínculos, preservando evidências, desconhecidos e diagnósticos.
   - [x] Agregar criação por jogador/equipe e período, evitando duplicação entre flags; conferir com SQL.
   - [x] Definir progressão de passes/conduções, entradas no terço final e na área e direção longitudinal das ações; agregar por jogador/equipe e período, com conciliação SQL na final.
   - [x] Normalizar Dispossessed e Miscontrol; validar com SQL.
   - [x] Agregar ações por zona, resultado e pressão, com recortes por jogador/equipe e período; validar com SQL.
   - [x] Integrar ações e finalizações aos fragmentos de posse; validar vínculos e diagnósticos com SQL.
   - [x] Separar contexto de bolas paradas e jogo corrido por evento/fragmento, com desconhecidos explícitos, seletor para ações e conciliação SQL; ver [política e cobertura](docs/analytics/play-phases.md).
   - [x] Criar evolução temporal e recortes por situação do placar, tratando gol contra e alterações de placar; núcleo, seletor e exportação local conferidos nas 64 partidas, com desconhecidos explícitos. Ver [evolução e placar](docs/analytics/match-evolution.md).
   - [x] Avaliar xG ao longo do jogo e métricas de criação; acumulados e grupos por placar com fórmulas, cobertura e pares passe–chute documentados em [evolução da partida](docs/analytics/match-evolution.md). Integração ao executor/API permanece no bloco 5.
5. **Integração e expansão.**
   - [ ] Transformar as conferências temporais/espaciais dos scripts em validações reutilizáveis para todos os jogadores.
   - [ ] Criar um fluxo reproduzível por partida, sem IDs fixos nos comandos gerais.
   - [ ] Definir persistência/cache dos resultados táticos, com versão e hashes; preservar as fontes originais.
   - [ ] Expor endpoints de consulta para resumos, eventos, redes e heatmaps, sem cargas implícitas por GET.
   - [ ] Manter cálculo no backend e entregar dados em JSON para frontend interativo.
   - [ ] Permitir selecionar partida, equipe/jogador, períodos, trecho, ações e resolução; consultar os eventos de cada célula/conexão.
   - [ ] Gerar PNG/SVG sob demanda; não materializar imagens para toda combinação de filtros.
   - [ ] Atualizar documentação específica, executar as verificações completas e revisar antes de publicar no GitHub.
   - [ ] Processar e validar os eventos das 64 partidas da Copa 2022, com execução em lote e registro de falhas por partida.
   - [ ] Avaliar cobertura e comparabilidade entre competições/temporadas antes de ampliar o escopo.
   - [ ] Investigar novos fornecedores somente para lacunas confirmadas; avaliar modelos preditivos em etapa posterior.

Antes de ampliar a Copa, conferir cobertura de cada campo e testar situações não
presentes na final. Amostras pequenas e diferenças de fonte/definição devem ficar
visíveis; não transformar uma diferença descritiva entre trechos em prova de
efeito causal de uma substituição ou mudança de formação.

### Arquivos e interface futura

Os PNGs/SVGs atuais são instrumentos de conferência e exportação. A lógica de
seleção/agregação é independente de Matplotlib e pode alimentar uma interface
interativa. A camada visual do navegador e os endpoints ainda precisam ser
implementados; não será necessário reescrever as regras de cálculo por isso.

Preservar dados históricos e resultados identificáveis. Consultar uma partida não
deve apagar seus eventos nem exigir nova busca externa. Medir volume e desempenho
antes de escolher índices, particionamento e políticas de cache. O formato final
da persistência tática ainda será definido, sem presumir uma imagem por jogador,
período ou combinação de filtros.

## Limitações operacionais

- Eventos são preservados em `raw` e normalizados em memória para as análises
  descritas acima; ainda não há tabela geral de eventos normalizados, persistência
  completa das métricas táticas nem estatísticas completas de cartões.
- Durações sem evidência permanecem ausentes. Expulsão de jogador já fora de campo
  e outras sequências não suportadas podem interromper o cálculo para revisão.
- A reutilização de um cálculo verifica identidade por fontes/versão e contagens;
  não compara novamente cada valor já armazenado.
- Execuções interrompidas abruptamente podem permanecer `running`; não há
  reconciliação automática, agendamento ou atualização ao vivo.
- `kickoff_at` permanece ausente enquanto o fuso da fonte não for tratado.
- Outros fornecedores, frontend e infraestrutura de produção são etapas futuras.
- Posição média de ações e posição média contínua são análises distintas; tracking
  e identificações individuais em dados 360 não são presumidos disponíveis.

As páginas de arquitetura e integração abaixo documentam principalmente o marco
de participação persistida. Sua atualização para as análises táticas consta no
roteiro; para o inventário atual, usar este README junto ao código e aos testes.

## Documentação e atribuição

- [Arquitetura](docs/architecture/system-design.md)
- [Integração StatsBomb](docs/integrations/statsbomb.md)
- [Ingestão do catálogo](docs/ingestion/catalog.md)
- [Pipeline de participação](docs/ingestion/participation.md)
- [Fórmula temporal e validação da final](docs/analytics/elapsed-on-field.md)
- [Interceptações e recuperações de bola](docs/analytics/interceptions-and-recoveries.md)
- [Revisão histórica do projeto](docs/reviews/project-review.md)

Dados fornecidos por **StatsBomb Open Data**. Repositório oficial:
<https://github.com/statsbomb/open-data>. Consulte suas condições, `LICENSE.pdf`
e requisitos de atribuição antes de publicar análises ou redistribuir dados.

Checkpoint de 12/11/2026: classificação por play_pattern conciliada com SQL nos
4.386 eventos dos períodos 1–4. Ruff, formatação e mypy aprovados;
**1.805 testes passaram**, incluindo PostgreSQL em `football_test`, com o aviso
TestClient/HTTPX já conhecido. Build Docker e ciclo completo de migrações não
foram reexecutados. Definições e limites em
[contexto de jogada](docs/analytics/play-phases.md).

Reconstrução de ataques em 12/11/2026: 389 sequências observadas (286 com limites
utilizáveis pela política e 103 para revisão); 116 observações de paralisação.
As 309 evidências de limite foram conciliadas com SQL. Duração e percentual de
posse permanecem ausentes. Ver [definições e cobertura](docs/analytics/attack-sequences.md).

Validação após a reconstrução: Ruff, formatação e mypy aprovados; **1.827 testes
passaram**, incluindo PostgreSQL em `football_test`. Permanece um aviso de
depreciação TestClient/HTTPX. Build Docker e ciclo completo de migrações não
foram reexecutados nesta etapa.

Mapas defensivos em 12/11/2026: seleção por equipe/jogador, grupo, tipo e períodos;
853 fontes únicas da final recompostas nos quatro períodos, com IDs/células
conciliados com SQL. Seis exemplos PNG/SVG/JSON gerados e revisão visual feita.
Detalhes em [mapas defensivos](docs/analytics/defensive-maps.md). O próximo item do
bloco 2 é integrar o resumo defensivo aos listados e à participação.

Validação após os mapas: Ruff, formatação e mypy aprovados; **1.848 testes
passaram**, incluindo PostgreSQL em `football_test` e 21 casos dos mapas.
Permanece apenas o aviso TestClient/HTTPX já conhecido. Build Docker e ciclo
completo de migrações não foram reexecutados nesta etapa.

Integração defensiva em 12/11/2026: os 50 listados foram conferidos nos sete
recortes contra SQL e participação persistida das mesmas fontes. Na partida
inteira: 33 `observed_events`, dois `zero_with_presence` e 15 `unknown`,
recompondo as 853 fontes únicas. Ver [definição e conciliação](docs/analytics/defensive-roster.md).

Validação após a integração: Ruff, formatação e mypy aprovados; **1.871 testes
passaram**, incluindo PostgreSQL em `football_test` e 23 casos da junção defensiva.
Permanece o aviso TestClient/HTTPX já conhecido. Build Docker e ciclo completo
de migrações não foram reexecutados nesta etapa.

Checkpoint em 13/11/2026: contexto inicial da disputa da final conferido.
Dos 50 listados, 22 foram classificados como elegíveis, 13 como inelegíveis
por substituição e 15 permaneceram desconhecidos. Os 10 participantes
observados estão entre os elegíveis, sem divergências. Ruff, formatação e
mypy aprovados; 1.943 testes passaram, com o aviso TestClient/HTTPX já conhecido.
A revisão é externa ao código e vinculada aos hashes dos payloads.

Checkpoint em 13/11/2026: placar da final conciliado em 3–3, sem divergências,
com seis gols de Shot, nenhum registro de gol contra e oito finalizações
da disputa excluídas. Ruff e mypy aprovados; 1.975 testes passaram,
incluindo PostgreSQL em football_test. Permanece o aviso TestClient/HTTPX.

Checkpoint em 17/11/2026: evolução dentro da partida, xG e criação acumulados,
com recortes por situação do placar, disponíveis no núcleo e exportador local.
As 64 partidas foram conferidas contra contadores independentes dos arquivos
brutos, incluindo três partidas com gols contra, sem divergências. Esta validação
não executa SQL. Fórmulas, comando e cobertura em
[evolução da partida](docs/analytics/match-evolution.md).
Ruff, formatação e mypy aprovados; **2.289 testes passaram e 16 foram ignorados**,
com o aviso TestClient/HTTPX já conhecido. Integração ao executor/API, duração e
percentual de posse e mudanças de elegibilidade durante a disputa continuam pendentes.

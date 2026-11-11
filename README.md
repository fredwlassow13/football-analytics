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
| Ações defensivas | Eventos incluídos no mapa geral, com tipos e IDs preservados | Adapters detalhados, subtipos, resultados, classificação e agregações por jogador/equipe |
| Duelos e desarmes registrados | Contrato, adapter e agregações por jogador/equipe; resultados separados e conferência SQL dos 98 eventos Duel | Integração dos recortes, participação e resumo defensivo; não reduzir resultados distintos a um único booleano |
| Disputas aéreas | Vitórias explicitamente marcadas e derrotas Aerial Lost, preservando o tipo original; contagens e aproveitamento por jogador/equipe conferidos com SQL | Integração ao resumo defensivo e filtros comuns; não somar novamente eventos já usados em passes ou finalizações |
| Goleiros | Eventos originais disponíveis e resultados de chutes defendidos normalizados | Adapter de Goal Keeper, vinculação aos chutes e estatísticas por goleiro/equipe |
| Posse | Sequência e equipe de posse preservadas no contexto | Reconstrução de sequências, definição de duração/percentuais e tratamento de paralisações |

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
2. **Defesa e disputas — em andamento; próxima etapa: interceptações e recuperações.**
   - [x] Normalizar Duel, preservar resultados de Tackle e agregar por jogador/equipe; conferir eventos, categorias e IDs com SQL.
   - [x] Normalizar vitórias/derrotas aéreas e agregar contagens/aproveitamento por jogador/equipe; conferir com SQL.
   - [ ] Normalizar subtipos/resultados de interceptações e recuperações de bola — ponto de retomada.
   - [ ] Normalizar detalhes e resultados de bloqueios e cortes.
   - [ ] Tratar pressões, dribles sofridos e faltas, distinguindo tentativas, resultados e grupos de ações.
   - [ ] Criar estatísticas individuais/coletivas e mapas por tipo/grupo e período.
   - [ ] Evitar somar eventos relacionados como perdas/recuperações independentes sem uma definição.
3. **Goleiros.**
   - [ ] Normalizar tipos e resultados das ações de goleiro e os vínculos disponíveis com chutes.
   - [ ] Calcular defesas, gols sofridos e demais categorias com critérios explícitos.
   - [ ] Conciliar jogador, equipe, participação e finalizações adversárias; separar a disputa de pênaltis.
4. **Posse, criação e progressão.**
   - [ ] Reconstruir sequências de posse e ataques, preservando qualidade temporal e paralisações.
   - [ ] Normalizar assistências, passes para finalização e vínculos passe–chute quando disponíveis.
   - [ ] Definir progressão de passes/conduções, entradas no terço final e na área e direção dos passes reais.
   - [ ] Analisar perdas/recuperações por zona, pressão/contrapressão e resultados das sequências.
   - [ ] Separar bolas paradas e jogo corrido, conforme a cobertura efetiva dos campos.
   - [ ] Criar evolução temporal e recortes por situação do placar, tratando gol contra e alterações de placar.
   - [ ] Avaliar xG ao longo do jogo e métricas de criação; documentar fórmulas e cobertura antes de usá-las.
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
- [Revisão histórica do projeto](docs/reviews/project-review.md)

Dados fornecidos por **StatsBomb Open Data**. Repositório oficial:
<https://github.com/statsbomb/open-data>. Consulte suas condições, `LICENSE.pdf`
e requisitos de atribuição antes de publicar análises ou redistribuir dados.

# StatsBomb Open Data — primeira integração

Fonte oficial: https://github.com/statsbomb/open-data (redireciona para hudl/open-data).
Catálogo: https://raw.githubusercontent.com/statsbomb/open-data/master/data/competitions.json

O dataset publica arquivos JSON; não exige chave. Cada entrada é um par competição/temporada.
A cobertura é seletiva e histórica; não representa calendário global ou resultados ao vivo.

## Consulta

GET /api/v1/providers/statsbomb/competitions?country=Germany&limit=20&offset=0

O filtro de país é exato, sem distinguir maiúsculas/minúsculas. A paginação é local,
após buscar o catálogo completo. A ordem é por nome da competição e nome da temporada.
Os IDs são strings externas identificadas pelo prefixo external_; não são IDs internos.
Esse endpoint de prévia não persiste nem mantém cache. Cada consulta acessa o fornecedor.
A persistência é executada separadamente pelos pipelines de ingestão.

O adapter valida o contrato externo e retorna um DTO independente do formato StatsBomb.
Timeout: conexão 5 s e demais operações 15 s. Até três tentativas para erros de transporte,
HTTP 429 e HTTP 5xx; espera exponencial de 1 e 2 segundos. Retry-After numérico até
5 segundos é respeitado; valores maiores ou datas encerram a operação para nova tentativa
posterior. Outros erros HTTP e respostas inválidas não são repetidos. A API retorna 502
quando a fonte não está disponível. Logs registram falhas de transporte/HTTP.

## Uso e atribuição

Dados fornecidos por StatsBomb Open Data. Consulte README e LICENSE.pdf no repositório
antes de publicação ou redistribuição. O README solicita atribuição à StatsBomb e uso
do logo em análises publicadas. Não se presume licença comercial irrestrita.

## Estado atual

- Catálogo: ingestão auditada; competições e temporadas normalizadas em core.
- Equipes e partidas: cargas auditadas da Copa 2022 (43/106).
- Jogadores: cadastro e mapeamento dos 50 listados na final (3869685).
- Escalações: vínculos de jogador/equipe/partida e classificação inicial validada.
- Eventos: preservados em raw; adapters extraem ações de participação, relógio e titulares.
- Analytics: intervalos e durações reconstruídos, versionados e persistidos.
- API: consultas de competições, temporadas, partidas e participação armazenada.

O campo match_status do dataset indica disponibilidade do dado, não o estado esportivo.
Horários sem fuso não são convertidos por suposição; kickoff_at permanece vazio.
Placares ausentes são None, preservando a diferença em relação ao placar zero.

Não usamos os intervalos de posição como fonte direta da duração: na inspeção da
final, três registros tinham período final anterior ao inicial. A reconstrução
usa eventos, sem corrigir silenciosamente o material original.

Dados de eventos e frames 360 são representações diferentes. Não presumimos
tracking contínuo, cobertura integral dos jogadores ou identificação individual
nos frames públicos. Formações registradas e posições médias das ações exigirão
normalização própria e não representam automaticamente toda a tática da equipe.

Próximas etapas: normalização geral de eventos da final, contexto tático e
estatísticas. Depois expandir o fluxo validado aos demais jogos e avaliar novos
fornecedores. Não existe importação automática de todo o catálogo.

Veja [pipeline de participação](../ingestion/participation.md) e
[fórmula temporal](../analytics/elapsed-on-field.md).

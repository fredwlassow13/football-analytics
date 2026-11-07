# Pipeline de participação da partida

## Pré-requisitos e ordem

Banco migrado, competição/temporada, equipes, partida, jogadores, escalações e
mapeamentos internos/externos existentes. O caminho validado é:

```sh
python -m football_analytics.ingestion.catalog --competition 43 --season 106
python -m football_analytics.ingestion.world_cup_teams
python -m football_analytics.ingestion.world_cup_matches
python -m football_analytics.ingestion.final_players
python -m football_analytics.ingestion.match_lineups --match-id 3869685
python -m football_analytics.ingestion.match_participation_job --match-id 3869685
```

`final_players` ainda tem seleção fixa da final. Os dois comandos com `--match-id`
são parametrizados, mas não cadastram automaticamente dependências ausentes.
IDs externos são independentes dos UUIDs internos. Nenhuma carga é iniciada por GET.

## Escalações e titularidade

`match_lineups` busca `lineups/{match_id}.json` e `events/{match_id}.json`, preserva
cada resposta e valida a lista inicial de titulares. Classificação desconhecida
pode ser preenchida; classificação conhecida não é apagada por entrada ausente.
Uma classificação oposta, camisa ou equipe divergente gera erro para revisão.

O comando informa registros criados e já existentes; o segundo grupo inclui
registros que receberam enriquecimento. Não é uma contagem separada de atualizações.

## Cálculo temporal

`match_participation_job` usa o mesmo par de fontes, reconstrói os intervalos e
confere a contagem coletiva. A fórmula é
[elapsed_on_field_ms 1.0.0](../analytics/elapsed-on-field.md).

1. Cria `raw.ingestion_runs` como `running`.
2. Busca e grava imediatamente cada fonte em uma transação própria, com corpo,
   JSONB, URL, hash e vínculo à execução.
3. Normaliza titulares, escalações, relógio e ações; reconstrói e valida a participação.
4. Abre uma transação para localizar/criar o cálculo e gravar os artefatos novos.
5. Atualiza a execução para `success` na mesma transação dos artefatos analíticos.
6. Em caso de falha, desfaz os artefatos pendentes e registra `failed` e erro em
   outra transação; payloads já preservados permanecem disponíveis.

O lock `CORE_INGESTION_LOCK_ID` serializa as gravações atuais. Constraints, FKs e
validações de aplicação protegem os vínculos e os resultados.

Primeira execução validada da final: 50 resultados e 36 intervalos novos. Repetição
com mesmos conteúdos e versão: mesmo UUID do cálculo, zero artefatos novos e uma
nova execução auditada com dois payloads.

O cabeçalho do cálculo mantém as referências da execução que o criou. Na
reutilização, novas fontes ficam na nova execução e o comando emite o UUID
reutilizado. Ainda não há tabela de associação direta entre cada execução que
reutilizou um cálculo e esse cálculo; a auditoria conserva os hashes correlacionáveis.

Uma fonte ou versão diferente permite outro cálculo, sem sobrescrever o anterior.
A reutilização verifica as contagens esperadas; não revalida integralmente cada
linha histórica. `records_processed` conta jogadores listados processados, não
apenas registros novos. `records_failed=1` representa falha do lote.

## Consulta

```text
GET /api/v1/matches/{match_uuid}/participation?calculation_id={calculation_uuid}
```

Ambos são UUIDs internos, e o cálculo deve pertencer à partida. A API retorna
metadados do cálculo, nomes dos jogadores/equipes, titularidade, evidência,
duração em ms, apresentação legível e intervalos numerados. A resposta consulta
somente PostgreSQL. Cálculo ausente/de outra partida: 404; parâmetro inválido: 422.

Para consultar a identificação interna pela referência externa:

```sql
SELECT match_id
FROM core.external_id_mappings
WHERE provider = 'statsbomb'
  AND entity_type = 'match'
  AND external_id = '3869685';
```

## Testes e operação

Execute as migrações contra o banco separado antes da suíte com PostgreSQL;
veja os comandos no README. Os testes cobrem relógio, intervalos, eventos,
normalização, persistência, rollback e consulta HTTP. Não fazem requisições reais
à StatsBomb; a validação com a final foi realizada separadamente.

O pipeline ainda não possui agendamento, recuperação automática de execuções
interrompidas ou processamento em lote. Uma interrupção abrupta pode deixar
`running`. Eventos permanecem nos payloads originais; não existe uma tabela
geral de eventos normalizados nem ingestão completa de estatísticas táticas.

# Escalações, participação e baseline a partir de fontes auditadas

## Carga offline

`football_analytics.ingestion.audited_roster` reutiliza os normalizadores e persistidores existentes. Não baixa arquivos: usa `downloads.json` e `sources/events`/`sources/lineups` do cache auditado. Confere status, URL por partida e SHA-256 dos dois arquivos.

Sem `--apply`, apenas valida arquivos e reconstrói participação/ocupação coletiva. Esse modo **não consulta nem concilia o cadastro do banco**. A carga efetiva resolve os vínculos de partida/equipe, cria jogadores e escalações ausentes e grava os cálculos de participação numa transação por partida. Falhas de uma partida não interrompem a verificação das demais; aparecem no relatório, com código de saída 1 se houver falhas.

```sh
.venv/bin/python -m football_analytics.ingestion.audited_roster \
  --database-url "$DATABASE_URL" \
  --audit-dir outputs/audits/statsbomb-2022-2026-11-13 \
  --all-audited \
  --output outputs/roster/preflight.json
```

Acrescentar `--apply` para persistir. `--match-ids ID ID` permite selecionar partidas em vez de `--all-audited`. A seleção completa percorre os eventos com status 200 do manifesto; a ausência ou inconsistência da escalação correspondente produz falha, sem omissão silenciosa.

Os payloads são reaproveitados por fornecedor, endpoint e hash, conferindo o corpo armazenado. O cálculo é identificado pela política existente e pelo par de hashes, independentemente de novas execuções. Na reexecução são comparados todos os resultados e intervalos, incluindo jogador, equipe, titularidade e ausência de evidência; apenas contar linhas seria insuficiente.

O cálculo continua sendo `elapsed_on_field_ms`, fórmula `1.0.0`: tempo decorrido dentro dos períodos, incluindo paralisações; não é tempo de bola rolando nem uma taxa por 90. Jogadores sem evidência mantêm duração desconhecida.

## Diferenças de metadados do jogador

O importador não altera nome/apelido/nacionalidade/data de nascimento de um jogador já identificado pelo mesmo ID no fornecedor. Diferenças são preservadas em `player_metadata_variants`, vinculadas aos hashes da partida, com valores armazenados e recebidos. `metadata_status=review` e `metadata_review_matches` indicam revisão pendente. `status=success` significa carga e conferência estrutural concluídas, não resolução das diferenças de metadados. A função legada `persist_players` continua estrita; não houve flexibilização global da ingestão.

Na Copa 2022 foram encontrados sete IDs com variações, afetando nove partidas: Yeremy/Yeremi Pino, Awer Mabil, Phil/Philip Foden, Mamadou Loum N'Diaye (apóstrofo duplicado), Matt Turner (apelido ausente), Otávio (apelido) e Anass Zaroury (country Marrocos/Bélgica). Os valores originais de cada arquivo permanecem nos payloads. A política não decide qual valor é correto; uma política de nomes/atributos canônicos com revisão é trabalho posterior.

## Baseline reutilizável

`football_analytics.ingestion.tournament_baseline` usa os cálculos ofensivos/defensivos existentes e a conciliação de placar para produzir linhas equipe–partida, agregados por equipe, totais e proveniência por partida. Não depende dos scripts antigos de outputs, não fixa 64 partidas e não fixa nomes, hashes ou caminhos de uma competição.

```sh
.venv/bin/python -m football_analytics.ingestion.tournament_baseline \
  --database-url "$DATABASE_URL" \
  --match-ids 3869151 3869321 3857271 \
  --output outputs/analysis/pilot-baseline.json
```

A CLI usa uma transação `REPEATABLE READ`, somente de leitura, e grava um JSON com os hashes e IDs dos payloads utilizados. Chamadores da função Python devem gerenciar sua transação; para múltiplas partidas recomenda-se o mesmo isolamento. A seleção de fontes segue o verificador existente (últimas fontes armazenadas), e o relatório registra a seleção efetiva. Reproduzir uma versão histórica exigirá seleção explícita dos payloads, ainda não oferecida nessa CLI.

A política exige placar conciliado e xG completo para os chutes selecionados; falha em vez de publicar soma incompleta como xG total. Uma equipe sem chutes tem xG zero. Pênaltis da disputa são excluídos. Aproveitamento agregado é completos/tentados somados, não média dos percentuais por jogo. Médias por partida incluem prorrogação e não devem ser apresentadas como métricas por 90.

O baseline produz JSON/exportação; ainda não acrescenta tabelas analíticas, endpoint ou frontend. A junção geral das métricas com participação, ausência de ações e filtros será tratada nos serviços de consulta.

## Verificação em 15/11/2026

- 64 partidas carregadas e conferidas; nenhuma falha na execução concluída.
- 829 jogadores distintos, 3.244 linhas de escalação, 64 cálculos, 3.244 resultados e 2.069 intervalos no banco local.
- Reexecução das 64: nenhum jogador, escalação ou cálculo novo.
- Baseline: 128 linhas equipe–partida e 32 seleções; 5.376 comparações com o relatório anterior sem diferenças nos campos comparados. IDs físicos de payload podem mudar com armazenamento equivalente e não são comparados; os hashes são comparados.
- Suíte completa: 2.041 testes passaram, um aviso conhecido de depreciação; Ruff/formatação e mypy aprovados (143 arquivos fonte).
- Relatórios: `outputs/batches/roster-participation-2026-11-15/` e `outputs/analysis/world-cup-2022-baseline-2026-11-15/packaged-baseline.json`.

A primeira execução (`import.json`) registra nove falhas por metadados divergentes. `completed.json` registra a conclusão com preservação dos metadados e revisão explícita; `reexecution.json` comprova o reaproveitamento. Não apagar a execução inicial da trilha de auditoria.

Próximo passo: catálogo versionado de métricas e contrato de consulta compartilhado por terminal/API/frontend local, incorporando a participação completa e os estados de cobertura.

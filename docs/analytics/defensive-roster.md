# Resumo defensivo dos jogadores listados e participação

Implementado em `ingestion/defensive_roster.py`, política `1.0.0`. A junção usa
ID externo de jogador e valida equipe, partida, fornecedor, titularidade e
igualdade dos períodos. A saída inclui uma linha para cada listado, inclusive
quem não tem eventos ou intervalos conhecidos. Mantém o resumo detalhado, a
participação recortada, os hashes das fontes e a base das contagens.

| `counts_basis` | Evidência | Contagens |
| --- | --- | --- |
| `observed_events` | Fontes defensivas/aéreas e presença positiva no recorte | Contagens observadas; resumo detalhado preservado |
| `zero_with_presence` | Presença positiva e nenhuma fonte no recorte | Zeros nas contagens; resumo observado ausente |
| `not_applicable` | Participação conhecida em outros períodos, sem intervalo positivo no recorte | `None` |
| `unknown` | Participação sem evidência temporal suficiente | `None` |

Uma duração ausente não vira zero. Estar listado ou ser reserva não basta para
atribuir presença, ausência definitiva ou zero de ações. Eventos sem presença
positiva exigem revisão e fazem a junção rejeitar a entrada, em vez de publicar
estatística contraditória. Duplicatas, jogador não listado, conflito de equipe,
titularidade, recortes, hashes ou intervalos também são rejeitados.

## Contagens e cobertura

- `unique_source_events`: união de ações e fontes aéreas por ID.
- `action_events`: registros dos nove tipos defensivos selecionados.
- `actions_by_type`: contagens desses nove tipos, incluindo zeros quando aplicáveis.
- `duel_events`: subconjunto Duel das ações, sem somá-lo novamente ao total.
- `aerial_results`, `aerial_won`, `aerial_lost`: resultados aéreos registrados.
- `shared_source_events`: IDs presentes nas ações e na projeção aérea.

Vale `unique_source_events = action_events + aerial_results - shared_source_events`.
Um Clearance com vitória aérea ou Duel/Aerial Lost continua uma única fonte.
Um jogador com somente fontes aéreas de Pass/Shot/Miscontrol também é observado,
mesmo sem registro nos nove tipos defensivos.

Flags ausentes, resultados desconhecidos, taxas e detalhes não são preenchidos
por zero: permanecem no `observed_summary` original. Zero de uma categoria é
contagem de registros, não afirmação sobre sucesso defensivo. Um resumo sem
fontes observadas permanece ausente; não se inventa um resumo detalhado com
percentuais ou flags zeradas.

**Pré-condição:** o resumo de entrada deve cobrir toda a população defensiva/aérea
do recorte. Não filtrar jogadores, grupos ou tipos antes de usar a junção para
afirmar zeros. O hash identifica a fonte, mas não prova sozinho que a chamada
recebeu todos os eventos; o chamador deve assegurar essa cobertura. A conciliação
versionada da final verifica essa condição com a seleção SQL independente.

## Participação e proveniência

A métrica é `elapsed_on_field_ms`, fórmula `1.0.0`, recortada por
`scope_player_participation`. O módulo valida soma dos intervalos, sobreposições,
limites negativos e consistência da evidência. Evidência conhecida com duração
zero no recorte é distinta de duração desconhecida.

`participation_events_sha256` deve coincidir com o hash de eventos do resumo.
O contrato registra também `lineups_sha256`, versão da política, métrica e versão
da fórmula. O script usa os dois payloads explicitamente vinculados ao cálculo
de participação armazenado, evitando misturar fontes de execuções diferentes.

Esta junção verifica presença agregada no recorte. **Não é uma auditoria do
instante de cada evento contra entradas/saídas e sua ordem na fonte.** Essa
validação reutilizável continua no bloco 5. Não são geradas taxas por 90, minutos
convencionais, tempo de bola em jogo ou elegibilidade no período 5.

## Conferência reproduzível da final — 12/11/2026

```sh
python scripts/verify_defensive_roster.py --database-url "$DATABASE_URL" \
  --match-id 3869685 --output outputs/defense/roster-verification.json
```

O comando lê somente dados locais. Escolhe o cálculo de participação mais recente
compatível com `elapsed_on_field_ms`/`1.0.0` da partida solicitada e lê as fontes
vinculadas a ele. Recalcula a participação e a junção em memória. O SQL recorta
independentemente os intervalos persistidos usando o relógio da fonte e seleciona
as ações e fontes aéreas diretamente do payload. Não grava no banco nem faz
requisições externas.

São conferidos identidade, titularidade, base, duração, IDs ordenados e todas as
contagens por jogador. Os totais dos jogadores recompõem o resumo integrado.
São 350 linhas de jogador verificadas nos sete recortes; as populações dos
recortes se sobrepõem e não devem ser somadas como eventos distintos.

| Períodos | Observados | Zero com presença | Não aplicável | Desconhecido | Fontes únicas |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 23 | 1 | 11 | 15 | 291 |
| 2 | 25 | 0 | 10 | 15 | 352 |
| 3 | 23 | 2 | 10 | 15 | 107 |
| 4 | 21 | 5 | 9 | 15 | 103 |
| 1, 2 | 27 | 0 | 8 | 15 | 643 |
| 3, 4 | 26 | 3 | 6 | 15 | 210 |
| 1, 2, 3, 4 | 33 | 2 | 0 | 15 | 853 |

Eventos SHA-256:
`a0b0036d50c10a4c8879d1ee9f24a7502ec2cfb264a61951c8d500dfc897ec35`.

Escalações SHA-256:
`2b00c4cbf027b36ae2ce99f183a0943c649bb5bdcb8324683a8603785beffed5`.

O JSON local exportado contém o relatório e os sete resumos completos com seus
IDs e diagnósticos de base. Não há novos plots, tabelas persistidas ou endpoints
nesta entrega. O próximo item do roteiro é a política de participação/elegibilidade
na disputa de pênaltis, dentro do bloco de goleiros.

Validação após a integração: Ruff, formatação e mypy aprovados; **1.871 testes
passaram**, incluindo PostgreSQL em `football_test` e 23 casos da junção defensiva.
Permanece o aviso TestClient/HTTPX já conhecido. Build Docker e ciclo completo
de migrações não foram reexecutados nesta etapa.

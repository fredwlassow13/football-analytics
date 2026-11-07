# Participação temporal — elapsed_on_field_ms

## Contrato da fórmula

| Propriedade | Definição |
| --- | --- |
| Nome | `elapsed_on_field_ms` |
| Versão | `1.0.0` |
| Unidade | Milissegundos inteiros |
| Granularidade | Jogador listado × partida × cálculo identificado |
| Fonte | Eventos e escalações StatsBomb Open Data |
| Implementação | `ingestion/participation.py`, `match_participation.py`, `team_occupancy.py` e adapters temporais |
| Ausência | `NULL` quando não existem intervalos reconstruídos |

A métrica mede a duração reconstruída da presença em campo no relógio acumulado
dos períodos. Inclui acréscimos e paralisações dentro dos períodos; exclui os
intervalos entre períodos, a disputa de pênaltis e as ausências temporárias
explicitamente identificadas pelos eventos usados na reconstrução.

Não mede tempo de bola em jogo. Não equivale automaticamente a minutos oficiais,
minutos arredondados ou ao denominador convencional de indicadores por 90.
Qualquer transformação para essas convenções exigirá outra definição e versão.

## Relógio e fórmula

1. Interpretar `timestamp` como `HH:MM:SS.mmm`, sem ponto flutuante.
2. Obter a duração de cada período pelos eventos `Half End` dos períodos 1–4.
3. Deduplicar encerramentos iguais e rejeitar encerramentos divergentes.
4. Exigir períodos `{1, 2}` ou `{1, 2, 3, 4}` completos. Excluir período 5.
5. Acumular as durações anteriores para converter um evento ao relógio da partida.

Para um evento no período p, com timestamp local t:

```text
instante_acumulado(p, t) = soma(duracoes_dos_periodos_anteriores) + t
```

Para um jogador com intervalos `[inicio_i, fim_i)`:

```text
elapsed_on_field_ms = soma(fim_i - inicio_i)
```

Os intervalos são interpretados como fechados no início e abertos no fim para
contagem coletiva. Eventos no encerramento do período são aceitos; eventos além
dele são rejeitados. Um intervalo com duração zero é permitido e ainda constitui
evidência de uma transição; ausência de intervalos é um caso diferente.

A apresentação `141 min 15,875 s` é derivada do inteiro armazenado. Ela não altera
nem arredonda o cálculo e não é outro valor independente para persistência.

## Tradução dos eventos

| Evento | Ação |
| --- | --- |
| `Starting XI` | Entrada no instante zero |
| `Substitution` | Saída do substituído e entrada do substituto |
| `Player Off` / `Player On` | Saída e retorno |
| `Red Card` / `Second Yellow` | Saída e marcação de saída definitiva |
| `Yellow Card` | Não altera participação |
| Mudança tática/posição | Não altera participação por si só |

A ordem por jogador segue `index`, preservando o índice e relógio da fonte nas
ações em memória. Os payloads originais mantêm todos os campos para auditoria.
Retorno após saída definitiva, entrada duplicada, saída sem presença ativa,
relógio que retrocede, jogador fora da lista ou equipe incorreta geram erro.
A reconstrução atual não persiste o motivo/índice de origem em cada linha de
intervalo: esse detalhe permanece recuperável nos payloads do cálculo.

Titulares são validados pelos eventos `Starting XI`, com dois conjuntos de onze
jogadores distintos e pertencentes às listas das equipes. `is_starter=False`
significa não titular, não participação durante a partida.

Sem intervalos, `has_participation_evidence=False` e duração `NULL`. Essa condição
não afirma definitivamente que o jogador não entrou: representa a ausência de
evidência reconstruída pelo algoritmo. Não substituir por zero.

## Validações individuais e coletivas

- Intervalos ordenados, não negativos, sem sobreposição e dentro da partida.
- Soma dos intervalos igual à duração individual atribuída.
- Jogadores e equipes coerentes com a partida e com a escalação armazenada.
- Entradas e saídas no mesmo instante aplicadas juntas na contagem coletiva.
- Contagem coletiva entre zero e onze; intervalos contíguos com igual contagem unidos.
- Soma das durações individuais igual à integral da quantidade de jogadores em campo.

Essas verificações demonstram consistência interna, não completude independente
de toda a fonte ou validade universal para qualquer regulamento de competição.

## Recorte validado: final da Copa 2022

Partida externa StatsBomb `3869685`, Argentina × França. Os números abaixo são um
checkpoint dos dados examinados; mudanças futuras no dataset podem alterar hashes
e exigir novo cálculo e investigação das diferenças.

| Resultado | Valor validado |
| --- | --- |
| Listados | 50: Argentina 26, França 24 |
| Titulares | 22, onze por equipe |
| Com intervalos | 35: Argentina 17, França 18 |
| Sem duração atribuída | 15 |
| Intervalos | 36 |
| Relógio acumulado | 8.484.610 ms = 141 min 24,610 s |
| Messi | 8.475.875 ms = 141 min 15,875 s, em dois intervalos |
| Ausência temporária de Messi | 8.735 ms |
| Soma Argentina | 93.321.975 jogador-ms |
| Soma França | 93.330.710 jogador-ms |

```text
Messi: [0, 1691248) e [1699983, 8484610)
Argentina: 11 -> 10 -> 11 jogadores nos limites dessa ausência
França: 11 jogadores durante o relógio reconstruído inteiro
```

A inspeção dos 60 registros de posição encontrou três intervalos com período
final anterior ao inicial: Messi, Acuña e Montiel. Não foram corrigidos por
suposição; a reconstrução usa eventos e preserva a fonte original. Esse diagnóstico
foi feito durante a validação; ainda não existe tabela de qualidade para registrar
essas anomalias automaticamente.

Cartões amarelos foram identificados também para jogador já substituído. Por
isso, um cartão isolado não é evidência de presença em campo naquele instante.

## Versão, identidade e armazenamento

O cálculo é identificado por:

```text
match_id + metric_name + formula_version + lineups_sha256 + events_sha256
```

Os hashes vêm do `response.content` preservado na ingestão, não de uma
canonicalização semântica do JSON. Até uma mudança de serialização pode produzir
outro hash e outro cálculo. IDs dos payloads, da execução criadora e timestamp
ligam o cálculo às fontes originais.

- `analytics.participation_calculations`: identidade, versão e proveniência.
- `analytics.player_match_participation`: snapshot de titularidade, evidência e duração.
- `analytics.player_participation_intervals`: início/fim numerados por resultado.

Cálculo, resultados e intervalos novos são inseridos na mesma transação. As FKs
compostas exigem que o cálculo e a escalação pertençam à mesma partida. Limites
entre linhas, sobreposições e conciliação de somas são validados pela aplicação.

Mesma identidade reutiliza o cálculo e verifica contagens de resultados e
intervalos; não compara cada valor armazenado novamente. Alterar a definição ou a
lógica com impacto no resultado exige revisar/incrementar `formula_version`, sem
mudar silenciosamente o significado de resultados já identificados.

## Limitações conhecidas

- Validado em profundidade na final de 2022 e em cenários sintéticos, não em todos
  os jogos ou em regulamentos com reentrada por substituição.
- Expulsão de reserva ou de jogador já fora de campo pode gerar saída sem presença
  ativa e interromper o cálculo. Também pode ocorrer em substituição durante uma
  ausência temporária. Esses casos exigem política específica; não são corrigidos
  silenciosamente.
- Os eventos de expulsão pertencentes ao período 5 não alteram os intervalos de atuação.
- Não são calculadas posições médias, distâncias percorridas, intensidade física ou
  estatísticas completas de cartões.

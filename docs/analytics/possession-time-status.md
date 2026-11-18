# Estado da posse e bola em disputa

Decisão de 17/11/2026: prosseguir sem revisão de vídeo e sem bloquear as demais
entregas do projeto pela validação de duração de posse/disputa.

## Disponível como experimental

- Estimativa parcial `event_gap_estimate_1.0.0`, com limite configurável e tempo
  desconhecido explícito. Não é posse oficial nem medição completa da partida.
- Visão de distribuição do relógio, com duas equipes, disputa revisada,
  paralisações delimitadas e desconhecido.
- Visão das equipes normalizada pelo tempo atribuído a ambas, com cobertura
  publicada. Não redistribui disputa, paralisação ou desconhecido entre equipes.
- Gerador `contest_candidates_1.1.0`, para localizar eventos e limites possíveis,
  sem aprovar janelas ou somar suas durações como disputa.

## Estado de disputa

`contest_assessment=not_reviewed`: apresentar “Disputa não aferida”. Nenhuma
janela da final foi aprovada. Zero janelas contabilizadas não significa zero
tempo de disputa. Manter o tempo sem evidência suficiente como desconhecido.

A revisão dos eventos confirmou três pares de perspectivas 50/50 entre os seis
candidatos com sugestões sobrepostas. Isso permite agrupar a revisão sem
aprovar duração nem fundir automaticamente todos os episódios próximos.

A janela do índice 2748 tem Lost Out seguido de lateral e não deve ser aprovada
inteira como disputa contínua. O índice 4238 contém recuperações e corte antes
da condução usada como limite; continuidade e limites precisam de revisão.
O relatório original permanece preservado, com todos os candidatos pendentes.

## Validação já realizada

- 106 candidatos: 98 Duel e 8 50/50.
- 53 sugestões de limites; nenhuma sobrepõe classificação conhecida na política
  de base. Isso não demonstra que o tempo esteja continuamente em disputa.
- Testes, Ruff e mypy da entrega 1.1.0 passaram na validação isolada; a execução
  do usuário reproduziu os totais esperados do relatório.
- Fonte da final SHA-256:
  a0b0036d50c10a4c8879d1ee9f24a7502ec2cfb264a61951c8d500dfc897ec35.
- Artefatos locais em outputs/reviews/final-contest-candidates-v1-1/.

## Pendências preservadas

Duração completa de posse e disputa não está validada. Essa pendência permanece
aberta, sem transformar estimativas parciais em indicador consolidado. Uma
retomada futura requer evidência independente de início, fim e continuidade,
alinhada ao relógio dos períodos. Não aumentar limites para completar 100%.

## Próxima integração

Integrar a evolução temporal e os recortes por situação do placar ao executor
analítico, reutilizando o núcleo já conferido nas 64 partidas. Definir filtros,
proveniência, cobertura e agregações; manter o placar anterior à ação e a
conciliação de gols contra. Os percentuais experimentais de posse/disputa ficam
fora das métricas consolidadas dessa integração.

Isso não conclui todas as pendências analíticas: mudanças de elegibilidade
durante a disputa de pênaltis também permanecem no roteiro existente.

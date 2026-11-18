# Interceptações e recuperações de bola

## Contratos e política de normalização

`MatchInterception` e `MatchBallRecovery` estendem `MatchEvent`, preservando
fornecedor, partida, ID original, índice, período, relógio e referências de equipe
e jogador. Os adapters ficam em `integrations/statsbomb/interceptions.py` e
`ball_recoveries.py`. Os resultados são retornados em ordem de origem, sem alterar
o payload armazenado. IDs ou índices repetidos interrompem a normalização.

Interceptações exigem resultado explícito. As categorias `Lost`, `Won`,
`Lost In Play`, `Lost Out`, `Success`, `Success In Play` e `Success Out` são
normalizadas em snake_case e permanecem distintas. Resultado desconhecido,
ausente ou nulo exige revisão. Não se cria um subtipo inexistente na fonte nem
um booleano universal de sucesso a partir dessas categorias.

Ball Recovery preserva `recovery_failure` e `offensive`, independentemente.
Cada flag pode ser `true`, `false` ou `null` no contrato interno. Campo ausente
gera `null`; `false` explícito é preservado. Um objeto `ball_recovery` ausente
ou vazio produz flags ausentes. Objeto ou flag explicitamente nulos na fonte,
strings e números no lugar de booleanos são rejeitados para revisão.

`recovery_failure=true` identifica falha explicitamente marcada. A política
atual não converte ausência dessa marcação em sucesso, nem infere que uma
recuperação sem `offensive` seja defensiva. Uma marcação ofensiva pode coexistir
com falha. As contagens abaixo representam eventos registrados, incluindo
tentativas, e não mudanças de posse únicas ou recuperações bem-sucedidas.

Referência do formato: [especificação oficial StatsBomb Open Data v1.1](https://github.com/statsbomb/open-data/blob/master/doc/StatsBomb%20Open%20Data%20Specification%20v1.1.pdf).
A política conservadora de manter ausências explícitas acima é uma decisão do
projeto; não deve ser confundida com uma nova definição estatística do fornecedor.

## Checkpoint da final em 11/11/2026

Fonte: StatsBomb `3869685`, 4.407 eventos, SHA-256
`a0b0036d50c10a4c8879d1ee9f24a7502ec2cfb264a61951c8d500dfc897ec35`.
Conferência somente de leitura em `raw.provider_payloads`, comparando o resultado
dos adapters com extração independente por `jsonb_array_elements` em SQL.
Os 143 eventos coincidiram individualmente em ID, índice, período, relógio,
equipe/jogador, categoria e flags. Essa conferência foi feita por script de
terminal; não é ainda um comando geral versionado de validação.

| Interceptações por resultado | Argentina | França |
| --- | ---: | ---: |
| won | 6 | 4 |
| success_in_play | 2 | 4 |
| lost_in_play | 3 | 3 |
| lost_out | 2 | 4 |
| lost / success / success_out | 0 | 0 |
| Total registrado | 13 | 15 |

| Recuperações registradas | Argentina | França |
| --- | ---: | ---: |
| Total de eventos Ball Recovery | 59 | 56 |
| recovery_failure=true | 9 | 4 |
| recovery_failure ausente | 50 | 52 |
| offensive=true | 0 | 1 |

As linhas de flags descrevem dimensões distintas; não devem ser somadas ao total.
Há 101 eventos sem objeto de detalhes, 13 com falha marcada e um com apenas
`offensive=true`. Nenhuma flag `false` explícita ocorre nesta amostra; esse caso
é coberto por testes sintéticos. Esses totais não são constantes de validação
para outras fontes ou partidas.

## Escopo e validação

Os adapters preservam inclusive o período 5; seleção temporal pertence à camada
de cálculo, para permitir separar a disputa de pênaltis. Ainda não há agregações
individuais/coletivas, recortes comuns, junção com participação, persistência
analítica ou endpoints específicos para esse bloco.

`tests/test_defensive_recovery_normalization.py` cobre resultados, flags ausentes
e falsas, combinações de flags, entradas inválidas, duplicações, ordenação,
filtragem por tipo e preservação de identidade/relógio/período. A suíte automatizada
usa fixtures sintéticas e não acessa a StatsBomb.

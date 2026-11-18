# Progressão e controle de bola por situação do placar

A extensão `ScoreStateQuery` 1.4.0 habilita as famílias `movement` (30 contagens) e `ball_control` (17 contagens), por equipe. Mantém suporte às famílias e versões anteriores. A consulta genérica continua sem aceitar o filtro de placar.

## Escopo e cálculo

O placar é reconstruído usando a fonte completa antes de aplicar período, equipe e situação. Cada ação pertence à situação anterior ao próprio evento, da perspectiva da equipe executora. Ações de período 5 são excluídas. Uma situação sem ações tem contagens disponíveis iguais a zero; gols sem resolução confiável mantêm os valores desconhecidos.

As classificações reutilizam as políticas existentes: `project_dx10_goal_distance25` para movimentos e categorias de evidência de controle de bola. Geometria indisponível permanece como classificação `unknown`, com motivos de revisão, sem virar progressão negativa. Controle conta eventos observados; não é tempo de posse nem duração de disputa. Metadados de pressão e geometria são mantidos junto ao evento original.

A evidência `complementary_scope` identifica família, equipe e IDs das ações selecionadas, incluindo equipes sem ações. A evidência herda o hash da fonte no registro da partida. Totais entre partidas usam o agregador já existente. Ainda não há agrupamento por jogador nesta extensão.

## Verificação

O script `verify_score_state_complementary.py` calcula a referência com os classificadores existentes sobre a fonte completa e atribui o placar anterior independentemente a partir da ordem bruta dos eventos. Conta diretamente as classes resultantes e compara valores, disponibilidade, cobertura e IDs selecionados. Isso verifica o filtro e a integração; não constitui uma validação independente das fórmulas geométricas existentes.

Também compara o placar final ao catálogo de jogos e, no modo PostgreSQL, verifica hashes de eventos e escalações. A execução usa `REPEATABLE READ`, `SET TRANSACTION READ ONLY` e um savepoint por partida. Usa a configuração existente de banco e não exige leitura direta de `os.environ['DATABASE_URL']`. O destino do relatório deve ser novo.

Validação no ambiente isolado: 2.484 testes aprovados, 16 ignorados; Ruff limpo; mypy limpo em 175 fontes. Conferência local dos 64 jogos: 18.048 comparações, nenhuma falha. A validação PostgreSQL deve ser executada após copiar os arquivos.

# Revisão do projeto

Escopo: arquivos de código, migrações, testes, configuração Python, Docker/Compose,
GitHub Actions, configuração VS Code e documentação. Arquivos gerados (.venv,
__pycache__, egg-info e caches) não são fontes mantidas do projeto.

## Problemas corrigidos

1. Migrações e testes tinham imports desordenados e formatação divergente. Isso
   impediria o CI de passar. Ruff aplicado ao projeto inteiro; o template das
   próximas migrações também foi alinhado.
2. Os downgrades dos mapeamentos de equipes e partidas recriavam CHECKs ainda
   dependentes da coluna que seria removida. O PostgreSQL removia esses CHECKs
   junto com a coluna, quebrando a reversão seguinte e perdendo a proteção.
   Corrigidas somente essas funções de downgrade; os upgrades e IDs mantidos.
3. Metadata SQLAlchemy vazio permitia autogenerate propor remoções de tabelas
   existentes. Adicionado bloqueio explícito ao processamento de revisões geradas.
   Verificação executada em cópia temporária das migrações.
4. Erros SQLAlchemy nas consultas de competições resultavam em falha sem resposta
   JSON padronizada. Agora recebem HTTP 503, com detalhes somente nos logs.
5. Normalização de equipes aceitava nomes em branco e a mesma equipe nos dois
   lados da partida. Agora rejeita esses casos e remove espaços externos dos nomes.
6. IDs do catálogo agora exigem inteiros positivos. Timeout de conexão do CLI de
   catálogo alinhado em 5 s com o restante da integração.
7. Documentação descrevia integrações e persistência existentes como trabalho
   futuro. README e documentação atualizados, inclusive execução dos testes com banco.

## Validação

- Ruff lint e formatação: aprovados em todos os arquivos Python.
- MyPy: aprovado nos 21 arquivos de src.
- Pytest com PostgreSQL separado: 38 testes passaram, nenhum ignorado.
- Dependências: pip check aprovado.
- Migrações em banco vazio: upgrade head, downgrade base e novo upgrade head passaram.
- Geração SQL offline: aprovada.
- Autogenerate com metadata vazio: bloqueado conforme esperado.
- Compose: configuração válida; imagem e serviços verificados durante a revisão.
- CI atualizado para testar o ciclo de migrações antes de inserir fixtures.
  O workflow remoto no GitHub não foi executado nesta revisão.

## Limites atuais

- Permanece um aviso de depreciação da integração TestClient/HTTPX; os testes passam.
  Atualizar essa integração exige revisar as versões e o arquivo de constraints.
- Partidas têm tabela e normalizador, mas ainda não têm pipeline de persistência.
- Ainda não há modelos ORM registrados, vínculo de participação equipe/temporada
  ou reconciliação automática de execuções interrompidas que permanecem running.
- Carga de seleções ainda seleciona 43/106 explicitamente e falha o lote de forma
  transacional; recuperação por partida será implementada com o pipeline de partidas.
- records_failed=1 nos pipelines atuais significa falha do lote, não contagem de
  entidades rejeitadas. Payloads e execuções preservam a rastreabilidade das cargas.
- Credenciais do Compose são exclusivas de desenvolvimento local.

Os testes com banco usaram football_review_test e football_migration_review_test,
separados do banco football. Não foi executado downgrade no banco principal.

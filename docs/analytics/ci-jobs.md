# Pipeline CI com suítes paralelas

Lint, Build e as seis suítes de Tests começam de forma independente. Package depende do sucesso de todos eles. Deploy depende de Package; Smoke test depende de Deploy; Notify depende dos jobs anteriores. Os três últimos jobs são placeholders: não realizam ações externas.

## Jobs ativos

- Lint: Ruff, formatação, mypy e validação do manifesto de suítes.
- Build: constrói a imagem Docker do Dockerfile existente, identifica pelo SHA do commit e exporta a imagem como artefato intermediário por um dia.
- Tests: matriz de analytics, ingestion, integrations, scripts, visualization e api; fail-fast desativado para permitir relatórios de todas as suítes. Cada job tem PostgreSQL 17 próprio, verificação de saúde e variáveis DATABASE_URL/TEST_DATABASE_URL apontando somente para o banco daquele job. Antes dos testes, aplica upgrade, downgrade e upgrade das migrações. O serviço e seus dados são descartados pelo GitHub ao finalizar o job.
- Package: reaproveita a imagem produzida pelo Build, comprime, gera SHA256SUMS e armazena o pacote por sete dias. Não publica imagem em registry nem cria release.

Os relatórios JUnit são armazenados por suíte durante 14 dias, inclusive após falhas de testes. Concorrência cancela execuções anteriores do mesmo workflow/ref. As permissões do token são apenas contents: read.

## Manifesto de testes

`configs/test-suites.json` atribui cada arquivo test_*.py a exatamente uma suíte. Alguns arquivos testam tanto execução quanto scripts; são executados integralmente na suíte scripts. O runner rejeita arquivos novos sem atribuição, arquivos removidos, duplicações e suítes vazias. Ao criar um arquivo de teste, acrescente-o à suíte correspondente. Testes que importam fixtures/helpers de outros arquivos continuam podendo fazê-lo; importar um helper não executa os testes daquele outro arquivo.

`python scripts/ci_test_suites.py` valida e lista as quantidades por suíte. `python scripts/ci_test_suites.py --suite analytics` executa uma suíte. A execução exige banco migrado quando os testes da suíte usam PostgreSQL. Qualquer skip faz o runner retornar erro no CI.

## Etapas futuras

Deploy, Smoke test e Notify ficam ignorados por padrão. Suas condições usam respectivamente as variáveis de repositório CI_DEPLOY_ENABLED, CI_SMOKE_TEST_ENABLED e CI_NOTIFY_ENABLED; somente o texto 'true' habilita cada placeholder. Mantenha-as ausentes ou desativadas enquanto as etapas não forem implementadas. Os nomes dos jobs declaram 'not configured' e os passos contêm apenas TODOs. Notify ainda não envia mensagens.

## Validação desta alteração

- YAML analisado e actionlint aprovado.
- Seis suítes executadas simultaneamente em bancos separados: analytics 210; ingestion 1.044; integrations 978; scripts 103; visualization 160; api 5. Total: 2.500 testes, nenhuma falha, erro ou skip.
- Upgrade/downgrade/upgrade das migrações passou em cada banco.
- Imagem Docker construída localmente com sucesso.
- Ruff e mypy aprovados no runner.
- Três exemplos da documentação tiveram apenas a formatação corrigida para atender ao job Lint já existente.

A execução hospedada no GitHub Actions deverá ser confirmada após aplicar e enviar os arquivos. O aviso existente de depreciação de TestClient/httpx não foi suprimido.

# Smart Grid by ZURI — Progresso

Última atualização: 2026-11-25.

## Estado resumido

- Branch: `feature/dataforseo-standard-normal`.
- Evolução estimada do projeto: **80%**.
- Homologação integrada estimada: **70%**.
- Testes automatizados: **204 aprovados, 0 reprovados**; um aviso de
  depreciação do `httpx` usado pelo `TestClient`.
- Nenhum commit, push, merge, deploy, chamada DataForSEO real ou alteração de
  produção foi realizado.

Os percentuais e os critérios estão detalhados em `ROADMAP.md`. São estimativas
de marcos; não medem cobertura de código nem garantem prontidão para produção.
Na homologação, os critérios concluídos somam 70 de 100 pontos ponderados;
os 30 pontos restantes são carga integral de 500, restart com fila pendente e
investigação dos `502`.

## Funcionalidades implementadas

- Smart Grid Live preservado, inclusive parâmetros de grade, histórico,
  comparações, indicadores, PDF e ranking de concorrentes.
- DataForSEO Live com idioma configurável, tratamento diferenciado dos estados
  de ponto e cobertura amostral.
- API externa autenticada/idempotente; geração de nove coordenadas 3×3 a 1 km.
- Fila PostgreSQL, workflows n8n Standard Normal, priority 1, polling por IDs
  persistidos e bloqueio de reenvio automático em submissão incerta.
- Landing Page, status e relatório individual protegidos; outbox e adaptador CRM
  preparados, com T2S apenas simulado.
- Compose de homologação isolado com os workflows reais e simuladores HTTP.

## Evidência da carga integrada

- Lotes planejados: 50, 100 e 500; concorrências configuradas em 10, 25 e 50.
- Os lotes de 50 e 100 foram aceitos. O runner **falhou durante o lote de 500**
  ao receber `502`. Portanto, o lote de 500 não é considerado aprovado.
- O PostgreSQL registrou 650 solicitações únicas e 650 chaves idempotentes
  distintas. Estado final observado: **623 `done`, 20 `manual_review` e 7
  `submission_uncertain`**.
- Os 20 casos de revisão manual não devem iniciar submissões pagas. Os 7 casos
  incertos exigem reconciliação; não reenviar automaticamente.
- Chamadas externas foram somente aos mocks locais. A captura disponível
  registrou `task_post` simulado, polling, Places e CRM; custo real zero.
- Logs agregados do proxy incluíram respostas `202` e `502`, além de retries e
  quatro solicitações posteriores. Não são uma contagem confiável por lote.
  Confirma-se que a resposta `502` pode coexistir com a persistência; a causa
  exata de cada resposta não foi isolada.
- Uma amostra durante a carga observou aproximadamente 89% de CPU e 1,42 GiB
  de memória no n8n. Não é perfil contínuo nem dimensionamento de produção.
- O banco de homologação chegou a 3.850 eventos da outbox `delivered` no
  simulador; isso não comprova integração real com T2S.
- Smoke integrado anterior aprovou status/relatório protegidos, tenant
  isolation, rejeição de token inválido, nove pontos e o estado DataForSEO
  `40102 No Search Results`.
- O teste dedicado de restart com tarefas pendentes não concluiu. A recuperação
  após reinicialização permanece não homologada.

## Segurança e preservação

- A stack Compose está parada; volumes nomeados foram preservados.
- Os resultados de homologação residem nos volumes PostgreSQL/n8n/Smart Grid.
  Não exportar banco, contatos sintéticos ou capabilities para o GitHub.
- Não foram encontrados arquivos de evidência `geogrid-*` em `/tmp`.
- A varredura de credenciais não encontrou chaves reais nos arquivos revisados.
  Há somente placeholders e identidades sintéticas de teste/documentação.
- `geogrid.db` está ignorado pelo Git e não foi alterado. Backups `.bak`
  permanecem no worktree e não fazem parte do checkpoint proposto.

## Inventário do worktree

### Arquivos rastreados modificados

- `README.md`
- `app/cli.py`
- `app/db.py`
- `app/fetchers/base.py`
- `app/fetchers/dataforseo.py`
- `app/main.py`
- `app/metrics.py`
- `app/scan.py`
- `app/static/app.js`
- `app/static/index.html`
- `app/static/style.css`
- `tests/test_scan_analysis.py`
- `tests/test_ui_help_panel.py`

### Arquivos novos, fora backups e script auxiliar

- `app/external_api.py`, `app/landing.py`
- `app/static/diagnostic.html`, `app/static/diagnostic.js`
- `app/static/landing.css`, `app/static/landing.html`,
  `app/static/landing.js`
- `docs/crm-t2s-adapter.md`, `docs/n8n-integration.md`, `docs/n8n-setup.md`
- `tests/integration/005-homologation-budget.sql`,
  `tests/integration/Dockerfile.mock`,
  `tests/integration/Dockerfile.test-runner`,
  `tests/integration/bootstrap-n8n.sh`,
  `tests/integration/compose.yaml`,
  `tests/integration/mock_services.py`,
  `tests/integration/nginx.conf`,
  `tests/integration/run_homologation.mjs`
- `tests/test_dataforseo.py`, `tests/test_external_api.py`,
  `tests/test_landing.py`, `tests/test_n8n_queue_artifacts.py`,
  `tests/test_scan_coverage.py`
- `workflows/n8n/01-recepcao-leads.json`,
  `workflows/n8n/02-processador-standard.json`,
  `workflows/n8n/03-recuperacao-resultados.json`,
  `workflows/n8n/04-status-diagnostico.json`,
  `workflows/n8n/05-relatorio-protegido.json`,
  `workflows/n8n/06-crm-outbox-adapter.json`
- `workflows/n8n/sql/000-create-database.sql`,
  `workflows/n8n/sql/001-schema.sql`,
  `workflows/n8n/sql/002-functions.sql`,
  `workflows/n8n/sql/003-landing-crm-capability.sql`,
  `workflows/n8n/sql/004-capability-lifecycle-and-crm-dispatch.sql`
- Documentação desta sessão: `ROADMAP.md`, `PROGRESS.md`, `TASKS.md`,
  `CHANGELOG.md`, `SESSION_HANDOFF.md`, `.github/copilot-instructions.md`.

Arquivos excluídos da proposta de checkpoint: três backups `*.bak`, o script
auxiliar `instalar_ida_adaptativo.py` (não necessário à execução e revisado
separadamente), o banco local ignorado e os volumes Docker.

# Instruções do projeto — Smart Grid by ZURI

## Contexto

- Projeto Python/FastAPI com interface web, SQLite local e DataForSEO Live.
- `feature/dataforseo-standard-normal` contém a integração externa, Landing
  Page e fila assíncrona PostgreSQL/n8n Standard Normal.
- A interface Live e a fila n8n são modos separados. Não remover parâmetros,
  indicadores, histórico, PDF ou comparações da interface Live.
- n8n processa Standard Normal (`priority: 1`, idioma `pt`, grid 3×3 de 1 km);
  Smart Grid gera coordenadas e persiste/calcúla resultados. PostgreSQL é a
  fonte de verdade da fila; SQLite existente deve permanecer compatível.
- O CRM está desacoplado por outbox. Não inventar endpoints ou contratos T2S.

## Estado e retomada

Consulte `SESSION_HANDOFF.md`, `PROGRESS.md`, `TASKS.md` e `ROADMAP.md` antes de
retomar. A regressão mais recente teve 206 testes aprovados (um aviso conhecido
de depreciação do `httpx` no `TestClient`). A homologação integrada em ambiente
isolado passou nos lotes HTTP de 50/100/500 e no teste de restart com tarefas
pendentes. A configuração verificada usou n8n 1.123.21, concorrência 50 e
`ZURI_N8N_TIMEOUT_SECONDS=60`; não presumir a mesma capacidade no Easypanel.

A causa original dos `502` foi o timeout fixo de 8 s no webhook da Landing Page.
O backend agora configura o timeout e devolve `503` retryable em falhas de
transporte, sempre repetindo com a mesma chave idempotente. A rodada final
registrou 651 requests únicos: 595 `done`, 34 `manual_review` e
22 `submission_uncertain`; os casos incertos não podem ser reenviados
automaticamente. A causa específica da revisão manual e a reconciliação dos
incertos seguem como riscos operacionais.

Antes de qualquer publicação, revisar esses casos, sizing/monitoramento no
Easypanel e os contratos oficiais do CRM T2S. Smart Leads Intelligence é
futuro, não iniciado nem autorizado. Não iniciar funcionalidades, commit,
push, merge ou deploy sem autorização explícita.

## Segurança e operações

- Nunca realizar chamadas pagas DataForSEO em testes; usar mocks HTTP.
- Nunca acessar ou modificar bancos, credenciais, backups ou serviços de
  produção sem autorização explícita.
- Não registrar tokens, capabilities, contatos ou payloads pessoais em logs.
- Não enviar, atualizar ou expor secrets no frontend.
- Não apagar bancos, históricos, backups ou volumes. Em homologação, preferir
  novos projetos/volumes Compose; não usar `down -v`.
- Não fazer commit, push, merge em `main` ou deploy sem autorização explícita.
- Dados `submission_uncertain` não podem ser reenviados automaticamente.
- Isolar tenants e preservar autenticação/idempotência.

## Desenvolvimento e validação

- Fazer alterações cirúrgicas e compatíveis; migrações somente aditivas.
- Preservar as fórmulas existentes dos indicadores, salvo aprovação explícita.
- Usar o estilo e os padrões já existentes. Adicionar testes para novos
  comportamentos e executar a suíte completa antes de concluir.
- Comandos atuais:
  - `.venv/bin/python -m pytest -q`
  - `docker compose -p geogrid-homolog-next -f tests/integration/compose.yaml config -q`
- O runner integrado pressupõe PostgreSQL de homologação vazio. Usar projeto
  Compose novo para uma nova rodada; preservar os volumes de evidência antigos.

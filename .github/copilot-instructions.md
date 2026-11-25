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
retomar. A regressão mais recente teve 204 testes aprovados. A homologação de
500 pedidos não passou: respostas HTTP 502 coexistiram com 650 solicitações
persistidas (623 concluídas, 20 revisão manual, 7 submissões incertas).
Recuperação após restart com tarefa pendente ainda precisa de prova.

Prioridade seguinte: investigar os 502, testar resposta idempotente e concluir
homologação progressiva/restart. Smart Leads Intelligence é futuro, não iniciado
nem autorizado neste momento. Não iniciar novas funcionalidades sem pedido.

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

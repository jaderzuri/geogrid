# Smart Grid by ZURI — Roadmap

## Objetivo

Entregar diagnósticos reproduzíveis de posicionamento no Google Maps por grade
geográfica, preservando a interface Smart Grid Live e oferecendo uma fila
assíncrona para solicitações externas. A arquitetura separa as pesquisas Live
da interface dos diagnósticos Standard Normal iniciados pela Landing Page.

## Arquitetura atual

- **Interface Smart Grid:** FastAPI, SQLite local e DataForSEO Live configurável;
  mantém grids, espaçamentos, unidades, palavras-chave, centro geográfico,
  histórico, comparações, indicadores e exportação existentes.
- **Integração externa:** API FastAPI autenticada por bearer token; gera a grade
  3×3 e os nove pontos, recebe resultados idempotentes e disponibiliza scans e
  indicadores sem iniciar consultas pagas.
- **Landing Page:** formulário servido pelo Smart Grid; encaminhamento de
  backend ao webhook n8n, resposta/protocolo de diagnóstico, status e relatório
  protegido por capability.
- **Fila:** PostgreSQL dedicado é a fonte de verdade de requests, orçamento,
  leases, tarefas DataForSEO, resultados e outbox. n8n Self Hosted 1.123.21
  executa seis workflows com Standard Normal, `priority: 1`, `language_code:
  pt` e recuperação por IDs persistidos.
- **CRM:** outbox durável e adaptador desacoplado; homologação usou somente um
  simulador. A integração real T2S depende do contrato oficial da conta.
- **Homologação:** Compose isolado, rede interna, PostgreSQL dedicado, n8n,
  Smart Grid e simuladores HTTP de DataForSEO, Places e CRM. Não é produção.

## Evolução estimada

- **Projeto: 80%.** Estimativa ponderada por marcos, não por linhas de código ou
  quantidade de testes:
  - Smart Grid Live, indicadores e compatibilidade: 20/20 pontos.
  - API externa autenticada e idempotente: 15/15.
  - Fila e workflows Standard: 17/20 (implementados; há homologação pendente).
  - Landing, status, relatório e outbox CRM: 12/15 (T2S real não conectado).
  - Homologação integrada: 11/20 (smoke e lotes menores; 500 e restart não
    passaram integralmente).
  - Prontidão operacional/documentação: 5/10.
  - Total: **80/100 pontos estimados**.
- **Homologação integrada: 70%.** Percentual ponderado por oito critérios:
  ambiente/workflows reais (20 pontos), smoke funcional Standard e relatório
  (15), verificações de segurança (10), regressão automatizada (10) e lotes de
  50/100 (15) foram concluídos: 70 pontos. Lote integral de 500 (15), restart
  com tarefas pendentes (10) e investigação dos `502` (5) estão pendentes:
  30 pontos. O caminho HTTP sob carga teve falhas; persistência não é contada
  como confirmação HTTP bem-sucedida.

## Marcos

### Concluídos para homologação

1. Melhorias do DataForSEO Live: idioma `pt` configurável, classificação dos
   estados/códigos, cobertura, falhas técnicas e estados visuais distintos.
2. API externa: autenticação, isolamento por cliente, validação, idempotência,
   persistência dos pontos e cálculo pelos mecanismos existentes.
3. Fila PostgreSQL e workflows n8n Standard Normal; sem retry automático de
   submissão paga ambígua.
4. Landing Page, status privado, relatório e outbox CRM desacoplada.
5. Homologação local isolada com os seis workflows reais, PostgreSQL 16 e
   serviços externos simulados.

### Próxima atividade autorizada

Investigar os `HTTP 502` na recepção sob concorrência, garantindo resposta
coerente quando o webhook já persistiu a solicitação. Em seguida, repetir em
ambiente isolado os lotes de 50, 100 e 500 e executar um teste de restart que
interrompa uma tarefa efetivamente pendente. Não iniciar trabalho até a próxima
sessão e confirmação do usuário.

### Planejamento futuro — Smart Leads Intelligence

**Não iniciado e não autorizado para esta sessão.** Futuro módulo separado,
após aprovação explícita e definição de requisitos: enriquecer leads e
priorizar oportunidades usando dados já disponíveis, com controles de
privacidade, consentimento e custo. Não inferir nem implementar fontes,
indicadores ou automações sem especificação e aprovação.

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

- **Projeto: 89%.** Estimativa ponderada por marcos, não por linhas de código ou
  quantidade de testes:
  - Smart Grid Live, indicadores e compatibilidade: 20/20 pontos.
  - API externa autenticada e idempotente: 15/15.
  - Fila e workflows Standard: 17/20 (homologados localmente; T2S e operação
    real no Easypanel ainda não integrados).
  - Landing, status, relatório e outbox CRM: 12/15 (T2S real não conectado).
  - Homologação integrada: 20/20 (recepção 50/100/500, restart com fila
    pendente, segurança e regressão executados em ambiente isolado).
  - Prontidão operacional/documentação: 5/10.
  - Total: **89/100 pontos estimados**. O total aumenta 9 pontos porque o
    marco de homologação passou de 11/20 para 20/20; não representa percentual
    de código nem prontidão de produção.
- **Homologação integrada: 100% dos critérios de teste definidos.** Pesos:
  ambiente/workflows reais (20), smoke Standard/relatório (15), segurança
  (10), regressão automatizada (10), lotes 50/100 (15), lote 500 (15),
  restart com tarefas pendentes (10) e diagnóstico/correção dos `502` (5).
  Os oito critérios foram exercitados. A configuração final recebeu HTTP `202`
  em todas as submissões, sem `502`/`503`, com 651 solicitações únicas e sem
  duplicidade de linhas. Este percentual é limitado ao ambiente local mockado;
  não certifica produção.

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

### Resultado da homologação

- Causa dos `502` confirmada: timeout fixo de 8 s no caminho da Landing Page,
  menor que o tempo de confirmação de persistência do webhook.
- Timeout configurável (padrão 30 s, faixa 1–120 s); erros de transporte são
  `503` retryable e orientam repetir com o mesmo identificador idempotente.
- A configuração final de teste usou n8n concorrência 50 e timeout 60 s:
  lotes 50/100/500 com concorrência 10/25/50 tiveram 654 respostas `202`
  (incluindo duplicatas idempotentes e o caso especial), sem `502`/`503`.
- PostgreSQL final: 651 requests únicos; 595 `done`, 34 `manual_review` e
  22 `submission_uncertain`. Os últimos estados não foram reenviados
  automaticamente; 22 continuam exigindo reconciliação e 34 não originaram
  chamadas DataForSEO.
- Restart isolado com IDs pendentes: polling e processamento retomaram após
  reiniciar n8n/Smart Grid; IDs já persistidos não foram reenviados.
- Suíte: 206 testes aprovados. Resultado detalhado e métricas em
  `docs/n8n-setup.md` e `PROGRESS.md`.

### Próximas atividades autorizadas

- **P01:** autorização única, reservas e custos foram validados com PostgreSQL
  e n8n isolados; checkpoint `6fc53a142cbe0cb0edceed83a097e96853d8f2da`
  publicado na branch de feature. A migration 006 final foi testada.
- **P02:** classificação e observabilidade implementadas localmente; testes
  do classificador, PostgreSQL e falhas induzidas nos nós do n8n isolado
  passaram; checkpoint local `4ccc300` sem push.
- **P03:** concluído em ambiente isolado. Backoff/jitter, limite de tentativas,
  `Retry-After`, agenda persistente e journal durável recuperam respostas
  recebidas sem repetir `task_post`; resposta perdida antes do journal continua
  em reconciliação manual. Checkpoint local autorizado, sem push.
- **P04 Smart Business Finder:** próximo pacote autorizado; implementar busca
  backend, classificação de candidatos e confirmação explícita. A interface
  visual continua reservada ao P06.
- Se a resposta HTTP não puder ser persistida nem recuperada, os IDs exigem
  reconciliação manual; manter a submissão incerta, sem reenvio automático.
  A garantia universal de chamada única entre banco e serviço externo não foi
  comprovada em todas as falhas.
- Antes de publicar, revisar os 34 `manual_review`, reconciliar os 22 casos
  anteriores `submission_uncertain`, validar sizing no Easypanel e obter os
  contratos oficiais T2S. Smart Leads Intelligence segue não autorizado.

### Planejamento futuro — Smart Leads Intelligence

**Não iniciado e não autorizado para esta sessão.** Futuro módulo separado,
após aprovação explícita e definição de requisitos: enriquecer leads e
priorizar oportunidades usando dados já disponíveis, com controles de
privacidade, consentimento e custo. Não inferir nem implementar fontes,
indicadores ou automações sem especificação e aprovação.

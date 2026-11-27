# Smart Grid by ZURI — Roadmap

## Objetivo

Entregar diagnósticos reproduzíveis de posicionamento no Google Maps por grade
geográfica, preservando a interface Smart Grid Live e oferecendo uma fila
assíncrona para solicitações externas. A arquitetura separa as pesquisas Live
da interface dos diagnósticos Standard Normal iniciados pela Landing Page.

## Status FASE A — Concluída ✅

**Data de Conclusão**: 2025-01-16

### Implementações:
1. **P0.1 — Captura de Dados de Contato** ✅
   - Captura: `phone`, `url`, `position` quando disponíveis
   - Sem invenção de dados; campos opcionais com valores None
   - Persistência automática em `points.results` (JSON flexível)
   - Compatibilidade retroativa 100%
   - Testes: 254 passando

2. **P0.2 — Interpretação de Resultados** ✅
   - Módulo `app/interpretation.py` implementado
   - Três geradores: coverage, positioning, competition
   - Integrado a `/api/scans/{scan_id}/analysis`
   - Testes: 268 passando (+ 15 novos)

3. **P0.3 — Correção de PDF** ✅
   - Folha de estilos `print.css` para A4 paisagem
   - Integrado a `index.html` e `diagnostic.html`
   - Utilitário `app/pdf_utils.py` com Playwright
   - PDFs validados: 3×3, 7×7, 13×13
   - Testes: 280 passando (+ 12 novos)

**Resultado**: 280 testes passando. Sem chamadas pagas. Compatibilidade preservada.

## Próximas Fases (Futuro)

### FASE B — MVP Comercial (Prioridade P1)

1. **Investigação de Recuperação Histórica**
   - Verificar se respostas originais estão preservadas
   - Analisar possibilidade de enriquecimento sem nova cobrança
   - Estimar custos de recuperação por CID/Place ID

2. **Utilitário de Recuperação de Contatos**
   - Modo dry-run por padrão
   - Deduplicação por identidade empresarial
   - Controle de origem e elegibilidade
   - Estimativa de custos antes da execução
   - Checkpoint e retomada após interrupções
   - Relatório de resultados

3. **Interface de Oportunidades Comerciais**
   - Visualizar empresas identificadas
   - Filtros: categoria, palavra-chave, região, posicionamento, telefone
   - Visualizar origem e data das informações
   - Selecionar e exportar em CSV
   - Identificar empresas que precisam enriquecimento

4. **Classificação de Telefones**
   - Normalização E.164 brasileiro
   - Classificação: celular, fixo, inválido, internacional, indeterminado
   - Testes para DDD, duplicados, formatos inválidos

### FASE C — Integrações (Prioridade P1)

1. **Integração CRM T2S**
   - Adaptador com cadastro idempotente
   - Origem SMART-GRID-ZURI
   - Controle de duplicidade e reprocessamento seguro
   - Validação com contrato oficial antes de ativar

2. **Fila Comercial SMS/RCS**
   - PostgreSQL com limite 200 mensagens/dia
   - Controle de duplicidade e descadastramento
   - Evidência de elegibilidade para contato
   - Disparos desabilitados por padrão

3. **Workflows n8n**
   - Workflows válidos e inicialmente inativos
   - Testes com mocks
   - Integração segura com endpoints

### FASE D — Futuro (Prioridade P2+)

1. **ZURI Smart Report**
   - Interface de personalização de relatórios
   - Capa personalizada e logo do cliente
   - Resumo executivo automatizado
   - Interpretação estratégica por IA
   - QR Code e CTA WhatsApp
   - Entrega por e-mail ou WhatsApp

2. **ZURI Keyword Intelligence**
   - Volume mensal de buscas no Google
   - Histórico de 12 meses e sazonalidade
   - CPC médio e concorrência publicitária
   - Palavras-chave relacionadas e alternativas
   - Distinção: Google Search vs Google Maps

## Sprint de lançamento comercial — candidata local

- Implementados registros diretos de leads e captura de concorrentes a partir
  dos resultados já persistidos de scans Express concluídos. A deduplicação é
  por identidade do candidato e acumula observações; não há busca/enriquecimento
  adicional. Exportação de prospectos DataForSEO exige
  `ZURI_PROSPECTING_DATA_USE_APPROVED`; dados sintéticos nunca são elegíveis.
- Incluído painel de operação autenticado com filtros e CSV. Leads só podem ser
  exportados com consentimento explícito de marketing; exportação de
  concorrentes permanece separadamente gated.
- Implementado o caminho Express real como integração condicionada ao webhook
  existente: grade 3×3, uma palavra-chave, 1 km e Standard Normal/priority 1.
  Continua fechado até todas as aprovações/configurações e não houve chamada
  paga ou teste em serviço real. Candidatos comerciais são processados somente
  depois que o scan existente é concluído.
- Professional e Monitor no modo real aceitam apenas solicitações idempotentes
  de atendimento assistido. Checkout, confirmação financeira real, liberação e
  diagnóstico Professional 7×7 não estão implementados para produção.
- Foco de validação: preservar scanner Live, licenciamento, idempotência e
  gates; testes usam mocks. Regressão consolidada concluída localmente:
  **253 aprovados**, com o aviso conhecido Starlette/httpx. Os arquivos do
  checkpoint foram revisados; artefatos locais e backups foram excluídos.
  Checkpoint local: `a95e079`, sem push.

- Foi implementada uma jornada comercial **somente simulada** na Landing Page
  nativa: busca sintética com candidatos ambíguos, confirmação explícita,
  cadastro de lead, diagnóstico Express 3×3, relatório protegido, oferta de
  upgrade, pedido Professional, confirmação manual auditada e liberação 7×7.
  Também há rotas autenticadas servidor-a-servidor para integração futura com
  WordPress. O modo simulado exige configuração explícita e não usa provedores
  externos nem credenciais comerciais.
- A API oficial da DataForSEO informa que analytics/relatórios transformados
  podem ser exibidos a clientes pagantes, mas sua FAQ declara que não confirma
  conformidade de um produto específico e que os termos não concedem direitos
  do provedor upstream. A compatibilidade do rastreamento de ranking local com
  a seção 7.1 e os termos aplicáveis do Google continua sem confirmação; não
  presumir autorização.
- Os termos MapTiler Cloud limitam o plano gratuito a uso não comercial e
  pesquisa/desenvolvimento. Não há evidência nesta sessão de assinatura
  comercial, limites ou restrições de chave para o ambiente ZURI.
- O Google Places permanece restrito à descoberta: sua política existente
  impede usar coordenadas para gerar a grade. Nenhuma alternativa comercial
  licenciada de coordenadas está ativa. Não adicionar OSM/Nominatim nesta
  sprint.
- Por isso, o endpoint da Landing Page que encaminha requests à fila paga
  falha fechado até haver quatro aprovações/configurações explícitas:
  `ZURI_COMMERCIAL_DIAGNOSTICS_ENABLED`,
  `ZURI_DATAFORSEO_COMMERCIAL_USE_APPROVED`,
  `ZURI_GOOGLE_UPSTREAM_USE_APPROVED` e
  `GEOGRID_MAPTILER_COMMERCIAL_PLAN_CONFIRMED`. A ativação dessas flags não
  substitui revisão documental/contratual.
- Catálogo experimental Express (3×3/1 palavra-chave, R$ 0), Professional
  (7×7, R$ 29,90) e Monitor (R$ 97/mês assistido) está centralizado e seus
  valores, disponibilidade e grids são configuráveis. No simulador, Express
  gera relatório sintético e Professional exige confirmação de pagamento e
  liberação por operador autorizado; Monitor só registra interesse assistido.
  Checkout real e cobrança continuam bloqueados. O workflow n8n Standard
  existente aceita somente 3×3; o Express real só pode usá-lo com gates
  adicionais aprovados. No caminho real Professional permanece assistido.
- Estimativa tarifária pública DataForSEO Standard para Google Maps SERP:
  US$ 0,0006 por página, logo US$ 0,0054 para 9 pontos e US$ 0,0294 para
  49 pontos, por palavra-chave. Estimativa não inclui resolução de empresa,
  cobranças adicionais, tributos, câmbio ou custos de operação; não representa
  custo confirmado da conta. Fonte: [preços da Google Maps SERP API](https://dataforseo.com/apis/serp-api/google-maps-api).
- A jornada simulada não é candidata à ativação comercial. Permanecem
  bloqueadas as pesquisas pagas, o checkout e qualquer liberação real até
  resolver direitos upstream, confirmar o plano MapTiler e habilitar as
  configurações autorizadas. O Smart Grid Live segue preservado.

## Arquitetura atual

- **Interface Smart Grid:** FastAPI, SQLite local e DataForSEO Live configurável;
  mantém grids, espaçamentos, unidades, palavras-chave, centro geográfico,
  histórico, comparações, indicadores e exportação existentes.
- **Integração externa:** API FastAPI autenticada por bearer token; gera a grade
  3×3 e os nove pontos, recebe resultados idempotentes e disponibiliza scans e
  indicadores sem iniciar consultas pagas.
- **Landing Page:** interface Live legada preservada em `/landing-legacy`;
  Landing nativa mantém modo sintético e Express real gated. O Express usa o
  webhook existente; a fila n8n e o diagnóstico Live da interface continuam
  separados.
- **Integração WordPress:** contrato documentado para busca, confirmação,
  leads, diagnósticos, status, relatórios, pedidos e consulta de pagamento;
  bearer token é validado no backend e nunca entregue ao navegador.
- **Persistência comercial:** tabelas SQLite aditivas para leads, diagnósticos,
  pedidos, eventos, prospects e observações; relatórios usam capability e
  captura/CSV obedecem aos gates de consentimento/proveniência.
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

### Pacotes autorizados e bloqueios

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
- **P04 Smart Business Finder:** backend implementado localmente com busca
  configurável, classificação, confirmação explícita, limites persistentes e
  gates de proveniência. Testes focados simulados: 25 aprovados; regressão
  completa: 236 aprovados. Checkpoint local `f657b8d`, sem push. Nenhuma busca
  real foi executada. A interface visual continua reservada ao P06.
- **P04.1 — compatibilidade e licenciamento:** bloqueio obrigatório antes de
  habilitar o fluxo em produção ou publicar a Landing Page 2.0. Validar com
  revisão legal/operacional o provedor, direitos de uso, retenção, atribuição,
  quotas e integração com o scanner. O Google Places não pode fornecer
  coordenadas para a análise espacial da grade; Place IDs são a exceção de
  armazenamento documentada. A integração OSM permanece desabilitada até a
  aceitação explícita da ODbL e prontidão de atribuição, e exige uma instância
  própria para o scanner. Não ativar fornecedor comercial sem contratação e
  revisão de termos.
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

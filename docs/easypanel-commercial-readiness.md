# Prontidão comercial no Easypanel

Este checklist descreve uma publicação futura, não uma autorização para ativar
serviços, executar consultas pagas ou alterar produção. O teste integrado desta
rodada usou exclusivamente simuladores em uma rede Docker interna.

## Evidência da homologação

- Jornada simulada: busca de empresa, dois candidatos homônimos, confirmação
  explícita, lead, pedido Express, fila, processamento, relatório protegido,
  painel/CSV e solicitação Professional assistida.
- Cobertura do Express: 9/9 pontos (100%). Empresa não localizada: caminho de
  atendimento assistido aceito, sem diagnóstico iniciado.
- Repetir a solicitação idempotente não criou outro pedido nem outra submissão:
  uma linha de pedido, uma chave idempotente, nove tarefas únicas e nove
  tarefas concluídas no PostgreSQL isolado.
- Contadores dos simuladores: 1 `task_post`, 9 recuperações, 3 operações Places,
  7 eventos entregues somente ao mock CRM. Custo registrado: US$ 0,00.
- Nenhum e-mail, SMS ou WhatsApp foi enviado. Os eventos CRM foram recebidos
  apenas pelo simulador local; a integração T2S não foi ativada.
- O teste de prospecção gerou um CSV local com um registro fictício emulado como
  resposta do provedor para exercitar o fluxo. Não compartilhar esse arquivo.
  Separadamente, `test_express_captures_deduplicated_synthetic_prospects_without_export`
  verifica que dados marcados explicitamente como `synthetic` não são elegíveis
  para exportação. Leads sem consentimento de marketing não apareceram no CSV
  de leads.
- A rede usada pela stack era `internal=true`; credenciais e chamadas reais de
  DataForSEO, Google Places e CRM não foram usadas. Não foi feita carga de 50,
  100 ou 500 solicitações.

## Serviços e exposição de rede

1. **Smart Grid/FastAPI existente**, com volume persistente para o SQLite
   histórico; não apontar para um banco substituto nem remover o volume atual.
2. **PostgreSQL dedicado `geogrid_queue`**, com volume e backups próprios.
   Não usar o banco interno do n8n como fila nem publicar a porta PostgreSQL
   na Internet.
3. **n8n**, com volume persistente do seu banco interno e de
   `/home/node/.n8n`; importar os workflows 01–06 inativos. O workflow 06 de
   CRM deve permanecer inativo até haver contrato e configuração aprovados.
4. **Proxy HTTPS** para a landing/API pública, com limite de corpo, rate limit
   e allowlist de origem. n8n, PostgreSQL, callbacks de serviço e endpoints de
   administração devem permanecer em rede privada ou protegidos por
   autenticação forte.
5. Os serviços e aliases de mocks da homologação são exclusivos de teste; não
   configurar `*.invalid`, tokens `homolog-only-*`, certificados ou flags de
   homologação no Easypanel.

WordPress/Hostinger não é pré-requisito para a landing atual. Se a integração
servidor-a-servidor WordPress for adotada futuramente, usar
`GEOGRID_WORDPRESS_API_TOKEN` somente no backend WordPress e Smart Grid; nunca
no JavaScript do navegador.

## Variáveis e credenciais

Configurar segredos pelo secret manager do Easypanel/n8n. Não colocar tokens,
senhas ou payloads de leads nos workflows exportados, no navegador ou em logs.

| Grupo | Configuração |
|---|---|
| Backend e fila | `GEOGRID_DB` deve continuar apontando ao SQLite persistente existente; `GEOGRID_EXTERNAL_API_TOKEN` deve ser compartilhado somente como credencial Bearer entre n8n e Smart Grid. |
| Webhooks internos | `ZURI_N8N_LEADS_WEBHOOK_URL`, `ZURI_N8N_STATUS_WEBHOOK_URL`, `ZURI_N8N_REPORT_WEBHOOK_URL`, `ZURI_N8N_INTERNAL_HTTP_HOSTS` e `ZURI_N8N_TIMEOUT_SECONDS`. Usar DNS interno; nunca URLs públicas para chamadas entre containers sem necessidade. |
| Landing e privacidade | `ZURI_ALLOWED_ORIGINS`, `ZURI_CAMPAIGN_ID`, `ZURI_ALLOWED_CAMPAIGNS`, `ZURI_PRIVACY_POLICY_URL` e `ZURI_PRIVACY_POLICY_VERSION`. A política deve ser publicada e os domínios de origem exatos. |
| Segurança operacional | `GEOGRID_COMMERCE_OPERATOR_TOKEN` (segredo aleatório de pelo menos 32 caracteres) e `GEOGRID_COMMERCE_OPERATOR_IDS` para operadores autorizados. Usar HTTPS para que cookies de administração `Secure` funcionem; aplicar privilégio mínimo. |
| Produto e limites no Smart Grid | Preços e disponibilidade configuráveis por `ZURI_PRICE_*_BRL`, `ZURI_PRODUCT_*_ENABLED` e `ZURI_PRODUCT_*_GRID_SIZE`. Para um piloto limitado a 10 Express/dia, configurar `ZURI_COMMERCIAL_DAILY_DIAGNOSTICS_LIMIT=10`, `ZURI_COMMERCIAL_DAILY_POINTS_LIMIT=90` e limites de pendências coerentes com a capacidade. |
| n8n | `N8N_BLOCK_ENV_ACCESS_IN_NODE=true`, `N8N_ENFORCE_SETTINGS_FILE_PERMISSIONS=true`, limite de payload alinhado ao proxy, credenciais PostgreSQL/Smart Grid/fornecedores armazenadas no cofre do n8n. Não copiar a concorrência 50 do ensaio de carga anterior para o Easypanel sem sizing próprio. |
| Opcional/futuro | `GEOGRID_WORDPRESS_API_TOKEN` somente se WordPress for integrado; configuração do adaptador CRM somente após aprovação do contrato T2S. Checkout DOM permanece desabilitado no lançamento. |

O Google Places usado para busca/resolução é uma credencial separada e pode ter
cobrança própria. Sua chave, se futuramente autorizada, deve ser restrita aos
serviços e origens backend necessários. Não habilitar o provedor simulador em
produção.

### Gates de ativação

Manter ausentes ou `false` até receber as aprovações correspondentes:

- `ZURI_COMMERCIAL_DIAGNOSTICS_ENABLED`
- `ZURI_DATAFORSEO_COMMERCIAL_USE_APPROVED`
- `ZURI_GOOGLE_UPSTREAM_USE_APPROVED`
- `GEOGRID_MAPTILER_COMMERCIAL_PLAN_CONFIRMED`
- `ZURI_N8N_EXPRESS_PIPELINE_ENABLED`
- `ZURI_GOOGLE_COORDINATE_STORAGE_APPROVED`

`ZURI_PROSPECTING_DATA_USE_APPROVED` é independente: sem aprovação, não reter
nem exportar prospectos derivados. Nenhum gate de licença pode ser habilitado
apenas para fazer o formulário funcionar. O catálogo PostgreSQL
`geogrid_budget` deve começar desabilitado e sem limites/custo nulos; definir
valores só após aprovação financeira. A configuração `005-homologation-budget`
é exclusiva de testes e não deve ser aplicada em produção.

## PostgreSQL, n8n e persistência

- Criar banco e usuário exclusivos para `geogrid_queue`, com privilégios
  mínimos, TLS conforme a rede do Easypanel e porta não pública.
- Aplicar em ordem: `000-create-database.sql` (se necessário), depois
  `001-schema.sql`, `002-functions.sql`,
  `003-landing-crm-capability.sql`,
  `004-capability-lifecycle-and-crm-dispatch.sql`,
  `006-paid-submission-authorization-and-budget.sql`,
  `007-processor-error-observability.sql` e
  `008-safe-retries-and-paid-response-journal.sql`.
- `005-homologation-budget.sql` está em `tests/integration` e não é migração de
  produção. Fazer backup validado antes de alterações de schema; interromper
  workers durante migração e não apagar estado existente.
- Configurar orçamento e cadência no PostgreSQL, além dos limites diários do
  backend. Para um teste real isolado, no máximo uma solicitação/9 tarefas
  pendentes por vez e uma nova solicitação por minuto são valores conservadores
  a considerar, sujeitos a sizing e aprovação. Preservar requests incertos
  para reconciliação manual; nunca repetir automaticamente `task_post` incerto.
- Ativar intake e workers somente na ordem documentada em
  [`n8n-setup.md`](./n8n-setup.md); manter workflow CRM 06 inativo enquanto não
  existir contrato oficial.

## Custo e piloto controlado

Estimativa de referência para Standard Normal: 9 pontos × 1 palavra-chave ×
US$ 0,0006 por página = **US$ 0,0054 de tarifa SERP por Express**. Para 10
diagnósticos/dia, a base aritmética é **US$ 0,054/dia**. Isso **não é teto de
cobrança nem custo total**: não inclui Google Places, câmbio, tributos ou outros
serviços, e a API não oferece autorização de gasto prévia por solicitação; o
custo real retornado pode exceder a estimativa reservada. Reconfirmar os preços
vigentes e definir um limite duro no provedor, se disponível, antes de qualquer
consulta paga. O orçamento do PostgreSQL deve usar estimativa conservadora e
monitoramento, não ser tratado como limite financeiro absoluto.

Teste real futuro (não autorizado nesta etapa): uma empresa, uma palavra-chave,
grade 3×3/1 km, `priority: 1`; estimar e registrar todas as tarifas antes,
confirmar saldo/limites, pedir autorização explícita, observar as nove tarefas
e parar novas admissões após esse pedido. Se qualquer submissão ficar incerta,
não reenviar; fazer reconciliação manual pelos IDs persistidos.

## Publicação e rollback

### Preparação/publicação

1. Confirmar autorização explícita para publicar e para cada fornecedor pago;
   resolver os gates e contratos de dados pendentes.
2. Registrar imagem/versão imutável, manter o SQLite e seus backups; criar
   backups verificáveis do PostgreSQL e estado n8n.
3. Provisionar serviços/volumes privados, aplicar migrations na ordem acima,
   configurar secrets, origens, TLS, limites e orçamento aprovado.
4. Importar workflows inativos, conferir credenciais sem expor valores e
   validar endpoints com simuladores. Confirmar relatórios protegidos,
   deduplicação, consentimento e logs sanitizados.
5. Só então ativar os fluxos aprovados e executar o piloto unitário após
   autorização específica. Monitorar tarefas pendentes, custo gasto/reservado,
   erros, `submission_uncertain`, espaço em disco e callbacks.

### Rollback

1. Desabilitar novas admissões Express primeiro. Pausar novos workers sem
   apagar filas; preservar os workers de recuperação/callback necessários
   para reconciliar trabalho já submetido.
2. Manter requests, IDs DataForSEO, estados, orçamento e logs. Não reenfileirar
   submissões incertas, limpar tabelas, remover volumes nem reverter migrations
   aditivas.
3. Restaurar a versão anterior da aplicação apenas após verificar
   compatibilidade do schema. Restaurar PostgreSQL/n8n somente de backups
   consistentes e preservando os registros que possam corresponder a cobranças.
4. Validar o scanner Live existente; ele continua independente da fila n8n.
   Reabrir admissões somente após causa, reconciliação e autorização.

## Bloqueios atuais

- Homologação comercial isolada aprovada; publicação/ativação não realizada.
- Aprovações aplicáveis a DataForSEO, Google upstream/armazenamento de
  coordenadas e plano MapTiler continuam gates fechados até validação comercial.
- Nenhum orçamento de produção, limite duro do provedor, credenciais ou sizing
  do Easypanel foi autorizado/verificado nesta rodada.
- Contrato oficial T2S e configuração de sua integração permanecem pendentes;
  WordPress externo não bloqueia a landing nativa.
- Testes extensivos de carga não foram repetidos. A capacidade configurada em
  ambiente isolado não deve ser presumida para o Easypanel compartilhado.

## P5.10 — pesquisa manual DataForSEO Live

1. Após aprovação da publicação, mantenha `GEOGRID_MANUAL_LIVE_ENABLED=false`.
   Preserve o volume SQLite existente e tenha backup verificável dos dois
   bancos. No PostgreSQL da fila, já migrado até `008`, aplique
   `migrations/postgresql/009-live-scan-budget.sql` com
   `psql -v ON_ERROR_STOP=1 -f migrations/postgresql/009-live-scan-budget.sql`.
   A migração adiciona duas tabelas e uma coluna opcional ao ledger.
2. Em `GEOGRID_FINANCE_DATABASE_URL`, configure a conexão privada do backend ao
   mesmo PostgreSQL usado pelo Standard. A role de runtime precisa de
   `SELECT`, `INSERT` e `UPDATE` em `geogrid_budget`,
   `geogrid_live_scans` e `geogrid_live_scan_queries`. Use uma role separada
   para a migração. Mantenha `GEOGRID_COMMERCE_OPERATOR_TOKEN` e
   `GEOGRID_COMMERCE_OPERATOR_IDS` no backend. Acesse por HTTPS com cookie
   seguro e inclua a origem do painel em `ZURI_ALLOWED_ORIGINS`, se definida.
3. Com aprovação financeira, configure `estimated_live_task_cost_usd` com
   uma estimativa conservadora por consulta, conforme a tarifa vigente para
   o endpoint Live usado. Revise `enabled`, `daily_cap_usd`,
   `default_event_cap_usd` na linha `system/default` e os limites
   `cap_amount_usd` das linhas diária UTC e `event/manual-live`, quando
   existirem. A menor configuração aplicável prevalece. O saldo diário é
   compartilhado com o Standard. A aplicação aceita um lote Live por vez,
   com até duas consultas em voo.
4. Confirme as quatro aprovações de `landing_diagnostics_gate` listadas acima.
   Configure `DATAFORSEO_LOGIN` e `DATAFORSEO_PASSWORD` como segredos.
   Após autorização para ativar, configure `GEOGRID_FETCHER=dataforseo` e
   `GEOGRID_MANUAL_LIVE_ENABLED=true`. Entre em `/api/commercial/admin` e
   volte à pesquisa manual em `/`. O painel usa a sessão existente e a
   rota `POST /api/commercial/admin/live-scans`, com um UUID em
   `Idempotency-Key`. Reenvie a mesma chave com os mesmos parâmetros para
   consultar o pedido em caso de resposta perdida. Com
   `GEOGRID_FETCHER=playwright`, o painel usa o fluxo gratuito.

### Interrupção e rollback

- Desative `GEOGRID_MANUAL_LIVE_ENABLED` e aguarde o lote terminar antes de
  substituir a aplicação. Para interromper novas autorizações imediatamente,
  use o controle financeiro `enabled=false` na linha `system/default`
  sob o lock compartilhado `pg_advisory_xact_lock(1847219, 1)`. Esse
  controle também pausa novas autorizações Standard. Pontos já autorizados
  podem concluir. Para restaurar a pesquisa gratuita, use
  `GEOGRID_FETCHER=playwright`.
- Preserve a migração `009`, o ledger e os volumes ao voltar a uma imagem
  anterior. Se a imagem anterior permitir Live sem reserva na rota legada,
  mantenha `GEOGRID_FETCHER=playwright` nessa imagem.
- Preserve reservas `uncertain` e `cost_overrun` até verificar o total
  efetivamente cobrado. Com evidência e nenhum ponto ainda em voo, use
  `app.live_finance.reconcile_live_scan` com o UUID, o total confirmado em
  USD e a referência da evidência. Após queda abrupta, um ponto pode ficar
  `authorized`. Somente com o processo original encerrado e após verificar
  o provedor, use `mark_live_query_uncertain` para cada ponto órfão e então
  reconcilie o lote. Para uma reserva órfã sem ponto autorizado ou incerto,
  use `finish_live_scan` após confirmar que o processo original terminou.
  Nunca reenvie automaticamente um ponto cujo envio tenha sido incerto.

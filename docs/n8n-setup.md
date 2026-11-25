# Instalação da fila Smart Grid no n8n

Este documento instala os workflows locais em `workflows/n8n/` e prepara a
fila PostgreSQL dedicada. Nada aqui altera o SQLite do GeoGrid ou o banco
interno do n8n. Não importe os workflows em produção antes de concluir a
configuração e os testes de homologação.

## Arquitetura e garantias

- Landing Page → webhook público do workflow 01 → validação → gravação
  idempotente no PostgreSQL → resposta HTTP `202`.
- A página pública fica em `/landing`; o backend faz o proxy ao webhook n8n
  configurado no ambiente, sem disponibilizar credenciais ao navegador.
- O PostgreSQL `geogrid_queue` é a fonte de verdade da fila, orçamento,
  reserva, IDs DataForSEO e resultados. Fica em serviço e volume próprios no
  Easypanel, na mesma rede interna.
- O workflow 02 usa uma função PostgreSQL transacional com lock consultivo,
  `FOR UPDATE SKIP LOCKED`, reserva de orçamento e limite de diagnósticos.
  Apenas uma solicitação nova pode ser reivindicada por minuto por padrão.
- As nove tarefas e coordenadas do GeoGrid são persistidas antes de chamar
  `task_post`. `priority: 1`, `language_code: "pt"` e `depth: 20` são fixos.
- Timeout, queda ou resposta ambígua depois de armar `task_post` resulta em
  `submission_uncertain`. Não há retry automático da chamada paga. Esses casos
  exigem reconciliação manual.
- O workflow 03 recupera resultados somente pelos IDs DataForSEO persistidos.
  Buscar resultados por ID é uma operação de recuperação, não uma nova
  submissão paga. Callbacks repetidos ao Smart Grid são idempotentes.
- O Smart Grid continua com SQLite e DataForSEO Live para pesquisas da
  interface web. O n8n não escreve no arquivo SQLite nem calcula indicadores.
- O workflow 04 expõe estado/progresso somente com capability individual; o
  workflow 05 valida a mesma capability antes de obter o relatório via API
  interna Smart Grid. O navegador pode imprimir o relatório em PDF.
- O Express comercial pode encaminhar a confirmação já vinculada a um lead ao
  webhook 01, reutilizando o mesmo `request_id`; o percurso permanece fechado
  até os gates de licenciamento, pipeline e armazenamento de coordenadas
  estarem aprovados. O callback de conclusão associa o scan ao diagnóstico e
  só captura concorrentes dos pontos já recebidos; retenção/exportação depende
  ainda de `ZURI_PROSPECTING_DATA_USE_APPROVED`. Não há enriquecimento adicional.
- Professional e Monitor, quando o Express real está habilitado, registram
  apenas pedido de atendimento assistido. Não há checkout real, recorrência ou
  processamento Professional 7×7 na fila n8n.
- A migração 003 registra consentimentos, hashes de capabilities e uma outbox
  durável CRM. Nenhum dado é enviado à T2S até serem fornecidos contratos
  oficiais da conta.

### Por que não usar Data Tables como fila

Na versão n8n `1.123.21`, o Data Table node oferece operações de linha como
`get`, `insert`, `update` e `upsert`. O `upsert` dessa versão executa update e,
se nenhuma linha for atualizada, insert dentro de uma transação, mas as colunas
personalizadas não têm restrição única. Não fornece a primitiva de reserva
condicional necessária para garantir uma única submissão paga concorrente.
Por isso, as quatro tabelas abaixo são tabelas PostgreSQL dedicadas; Data
Tables podem ser usadas futuramente apenas como espelho não autoritativo.

O mecanismo SQL foi projetado para PostgreSQL. Os testes locais deste repositório
simulam concorrência com SQLite e validam os artefatos estaticamente; eles não
substituem uma validação real da versão/configuração PostgreSQL do Easypanel.
Faça o teste de homologação descrito adiante antes de ativar os workflows.

## Compatibilidade e nodes

Os workflows foram preparados para n8n Self Hosted `1.123.21`, usando apenas
nodes integrados:

| Workflow | Nodes integrados |
|---|---|
| 01 - Recepção | Webhook 2.1, Code 2, Postgres 2.6, Respond to Webhook 1.1 |
| 02 - Processador | Schedule Trigger 1.2, Postgres 2.6, Code 2, IF 2.2, HTTP Request 4.2 |
| 03 - Recuperação | Schedule Trigger 1.2, Postgres 2.6, Code 2, IF 2.2, HTTP Request 4.2 |
| 04 - Status | Webhook 2.1, Code 2, Postgres 2.6, IF 2.2, Respond to Webhook 1.1 |
| 05 - Relatório protegido | Webhook 2.1, Code 2, Postgres 2.6, IF 2.2, HTTP Request 4.2, Respond to Webhook 1.1 |

O repositório upstream da tag n8n `1.123.21` contém os nodes Data Table e
Postgres. A configuração importa com workflows inativos e referências de
credenciais placeholder. Se uma instalação diferente não reconhecer um
`typeVersion`, atualize o n8n em homologação ou ajuste a versão do node pelo
editor antes de ativar; não instale community nodes para esta integração.

## 1. Criar PostgreSQL e volume no Easypanel

1. Crie um serviço PostgreSQL dedicado, com database `geogrid_queue`, usuário
   exclusivo para a fila e senha gerada no gerenciador de segredos do Easypanel.
2. Associe um volume persistente ao diretório de dados oficial da imagem
   PostgreSQL. Configure backup/restore do volume e teste a restauração.
3. Conecte somente os serviços necessários à rede privada do Easypanel. Não
   publique a porta PostgreSQL na Internet. O host será o nome DNS interno do
   serviço, não um endereço presumido deste exemplo.
4. Armazene as credenciais no Easypanel/n8n Credentials. Não as inclua em
   workflows exportados, variáveis do navegador, arquivos de configuração ou
   logs.
5. Execute `workflows/n8n/sql/000-create-database.sql` uma única vez como
   administrador PostgreSQL, caso ainda não tenha criado a base. Conectado a
   `geogrid_queue`, execute em ordem:
   - `workflows/n8n/sql/001-schema.sql`
   - `workflows/n8n/sql/002-functions.sql`
   - `workflows/n8n/sql/003-landing-crm-capability.sql`
   - `workflows/n8n/sql/004-capability-lifecycle-and-crm-dispatch.sql`
   - `workflows/n8n/sql/006-paid-submission-authorization-and-budget.sql`
   - `workflows/n8n/sql/007-processor-error-observability.sql`

O arquivo 005 de `tests/integration` configura orçamento somente para
homologação e não é uma migração de produção. Antes de atualizar uma fila
existente, pare os processadores, valide backup e reconcilie reservas legadas;
não aplique migrações em produção sem autorização.

O serviço GeoGrid existente não precisa de alteração ou acesso a esse banco.
Mantenha `GEOGRID_DB` apontando para o SQLite atual do GeoGrid.

## 2. Estrutura persistente

| Tabela | Responsabilidade e campos principais |
|---|---|
| `geogrid_requests` | Uma linha por solicitação; UUID, chave idempotente e fingerprint únicos, campanha, dados de contato e consentimento, estado, lock/lease, IDs externos, centro geográfico, custo reservado/real, erros e timestamps. |
| `geogrid_tasks` | Nove linhas por diagnóstico; célula/índice únicos, coordenadas geradas pelo GeoGrid, `tag` única, ID DataForSEO único, estado POST/poll, código/mensagem originais, custo, resultados, lease de recuperação e confirmação do callback. |
| `geogrid_budget` | Limites e acumuladores do sistema, do dia UTC e de cada evento/campanha; custo gasto e reservado, máximo de tarefas pendentes, cadência e estimativa de custo por ponto. |
| `geogrid_execution_logs` | Eventos operacionais sanitizados, sem token, senha ou payload de contato; request ID, workflow, evento, severidade e detalhes JSON. |
| `geogrid_status_tokens` | Hash SHA-256 de capabilities aleatórias por solicitação; token em texto claro não é persistido. |
| `geogrid_crm_outbox` | Eventos idempotentes de lead e mudanças de status para um adaptador CRM futuro. |

Restrições relevantes:

- `geogrid_requests.request_id` e `idempotency_key` são únicos.
- `geogrid_crm_outbox.event_key` é único; eventos e estado persistem até um
  adaptador oficialmente configurado. A outbox não envia dados por si só.
- Status e relatório exigem o hash do token vinculado ao request UUID; o UUID
  sozinho não autoriza acesso.
- `geogrid_tasks` tem unicidade por `(request_id, point_index)`,
  `(request_id, row_index, col_index)`, `tag` e `task_id`.
- A relação célula/índice exige `point_index = row_index * 3 + col_index`.
- A função de reserva serializa rate limit/orçamento, bloqueia a linha candidata
  e usa `SKIP LOCKED`; não faça `SELECT` e `UPDATE` separados no workflow.
- Solicitações que não foram submetidas podem voltar à fila depois que o lease
  expira. Se o estado já foi armado para `task_post`, lease expirado vira
  `submission_uncertain`, nunca volta automaticamente a `queued`.
- Tarefas de recuperação podem ser reclamadas concorrentemente sem duplicar
  recuperação, usando lock e lease; leases expirados são recuperáveis porque
  `task_get` usa o ID persistido e não cria uma nova tarefa paga.

Os logs não devem receber dados pessoais, cabeçalhos `Authorization`, tokens,
senhas ou respostas completas de provedores. Os dados de contato são mantidos
em `geogrid_requests` para a integração futura autorizada; aplique a política
de retenção/expurgo aprovada pela organização antes de usar dados reais.
Os eventos `processor_error` registram nó, etapa, categoria, código seguro,
mensagem fixa, tentativa, execução, possibilidade de recuperação e fase da
autorização; data e request ID ficam nas colunas da tabela. Submissão incerta
exige intervenção e nunca autoriza novo POST. Se o PostgreSQL estiver
indisponível, ele não consegue persistir o próprio erro: a execução deve falhar
visivelmente e o monitoramento externo deve alertar, sem gravar payloads.
A classificação não introduz validação de identidade/localização do P04 nem
altera a política de retentativas; defina retenção e acesso restrito aos logs.

## 3. Configurar limites antes de qualquer ativação

O schema cria uma linha `system/default` desabilitada para processamento
financeiro: limites de orçamento e custo estimado começam nulos. Configure os
valores em homologação, usando os preços vigentes de DataForSEO:

```sql
UPDATE geogrid_budget
SET daily_cap_usd = 25.00,
    default_event_cap_usd = 10.00,
    estimated_task_cost_usd = 0.01,
    max_pending_tasks = 90,
    new_diagnostics_per_minute = 1,
    enabled = TRUE,
    updated_at = now()
WHERE scope_type = 'system' AND scope_key = 'default';
```

Os valores acima são apenas exemplo de configuração, não uma recomendação de
preço nem uma estimativa confirmada de cobrança. Substitua-os por orçamento e
estimativa aprovados. A função reserva nove vezes o custo unitário configurado
contra limites diário e do evento; não reivindica uma nova solicitação quando
falta configuração ou orçamento. A função contabiliza o custo real retornado
pelo DataForSEO. Como a API não oferece uma autorização prévia de gasto por
solicitação, um custo real superior à estimativa pode exceder o teto por esse
delta. Defina uma estimativa conservadora e monitore o custo devolvido pela API.

Um limite específico de campanha deve ser criado antes do primeiro pedido dessa
campanha:

```sql
INSERT INTO geogrid_budget (scope_type, scope_key, cap_amount_usd)
VALUES ('event', 'id-do-evento', 5.00)
ON CONFLICT (scope_type, scope_key) DO UPDATE
SET cap_amount_usd = EXCLUDED.cap_amount_usd, updated_at = now();
```

Atualizações de limites não apagam nem reiniciam valores já gastos/reservados.
O orçamento diário usa data UTC. A capacidade de pendências conta tarefas
submetidas, em consulta, com submissão em andamento ou incerta. Como cada
diagnóstico adiciona nove tarefas, o processador só reivindica uma solicitação
quando há espaço para as nove sem exceder `max_pending_tasks`.

## 4. Credenciais n8n

Crie credenciais no n8n e selecione-as nos nodes indicados. Os IDs dentro dos
JSON são placeholders para serem substituídos no editor; nunca os preencha com
secrets antes de importar.

1. **GeoGrid Queue PostgreSQL**
   - Host: DNS interno do PostgreSQL no Easypanel.
   - Database: `geogrid_queue`; porta padrão PostgreSQL.
   - Usuário e senha dedicados, TLS conforme a rede/serviço.
   - Pool/conexões limitados para proteger os recursos compartilhados.
2. **Smart Grid External API Bearer**
   - Credencial HTTP Request do tipo Bearer Auth, usando o valor de
     `GEOGRID_EXTERNAL_API_TOKEN` configurado no backend GeoGrid.
   - Configure o mesmo segredo no n8n; nunca o exponha no navegador.
   - O header não secreto `X-GeoGrid-Client-ID` está como `t2s-landing` nos
     nodes. Altere para um identificador estável acordado com o GeoGrid.
3. **DataForSEO Basic Auth**
   - Credencial HTTP Basic Auth com as credenciais DataForSEO fornecidas pelo
     administrador. Não são armazenadas nos JSON.
   - O usuário/senha não devem aparecer em output de execução ou logs.
4. **Google Places API Key**
   - Credencial HTTP Header Auth com `X-Goog-Api-Key` e uma chave restrita a
     Places API e aos serviços necessários.
   - O processador usa Place Details para confirmar o Place ID e obter o
     centro. O Google Places pode ter cobrança própria, separada do orçamento
     DataForSEO. A chamada só ocorre quando os workflows forem ativados; nenhuma
     chamada foi feita durante esta implementação.
5. **Status e relatório**
   - Não há credencial no navegador; capability aleatória de 256 bits é
     persistida apenas como SHA-256.
   - Configure os webhooks dos workflows 04/05 no backend Smart Grid.

O processador exige uma URL Google Maps com um Place ID estável
(`query_place_id`, `place_id` ou segmento `!1sChIJ...`). Links curtos sem ID
estável são movidos a `manual_review` e **não** submetem buscas pagas; não há
resolução insegura de URL arbitrária nem busca aproximada que possa selecionar
uma empresa errada. Links curtos podem ser ampliados/validados manualmente
antes de reprocessar.

## 5. Variáveis de ambiente e proteção do n8n

Avalie as variáveis no serviço n8n do Easypanel; não aplique mudanças de
produção nesta etapa:

| Variável | Configuração/uso |
|---|---|
| `N8N_PAYLOAD_SIZE_MAX=1` | Limite de 1 MiB no webhook (n8n mede em MiB); alinhe o reverse proxy para rejeitar corpos maiores. |
| `NODE_FUNCTION_ALLOW_BUILTIN=crypto,url` | Permite somente os módulos Node `crypto` e `url` usados para hash, fingerprint e validação de URLs nos Code nodes. |
| `N8N_BLOCK_ENV_ACCESS_IN_NODE=true` | Recomendado para impedir acesso a variáveis de ambiente a partir de nodes/expressões; estes workflows não dependem de `$env`. |
| `N8N_CONCURRENCY_PRODUCTION_LIMIT` | Opcional, limite global das execuções de produção. Ele afeta também webhooks e outros workflows, não somente este processador; dimensione após medir a latência do formulário. A reserva SQL continua sendo a proteção de exclusão mútua da fila. |
| `ZURI_N8N_LEADS_WEBHOOK_URL` | URL do webhook de recepção 01; somente backend Smart Grid. |
| `ZURI_N8N_STATUS_WEBHOOK_URL` | URL fixa do webhook do workflow 04; o backend envia `request_id` e capability no corpo JSON. |
| `ZURI_N8N_REPORT_WEBHOOK_URL` | URL fixa do webhook do workflow 05; o backend envia `request_id` e capability no corpo JSON. |
| `ZURI_N8N_INTERNAL_HTTP_HOSTS` | Allowlist explícita de DNS privado para HTTP entre containers; opcional. |
| `ZURI_N8N_TIMEOUT_SECONDS` | Timeout do backend para webhooks n8n; padrão 30 s, faixa permitida 1–120 s. Um timeout gera HTTP 503 e deve ser tentado novamente com o mesmo `request_id` e os mesmos dados, pois a primeira tentativa pode já ter sido persistida. |
| `ZURI_ALLOWED_ORIGINS` | Origens exatas permitidas, separadas por vírgula (ex.: `https://diagnostico.exemplo.com`); sem configuração, usa a origem vista pelo FastAPI. |
| `ZURI_CAMPAIGN_ID` | Campanha padrão que o backend aceita. |
| `ZURI_ALLOWED_CAMPAIGNS` | IDs adicionais permitidos, separados por vírgula; QR pode usar `?campaign=<id>`. |
| `ZURI_PRIVACY_POLICY_URL` | URL oficial da política; sem configuração, o formulário fica desabilitado. |
| `ZURI_PRIVACY_POLICY_VERSION` | Versão da política registrada com consentimento. |
| `ZURI_LEADS_RATE_LIMIT` | Limite por endereço no processo FastAPI; padrão 600 por janela. |
| `ZURI_STATUS_RATE_LIMIT` | Limite de status; padrão 120 por janela. |
| `ZURI_REPORT_RATE_LIMIT` | Limite de relatório; padrão 20 por janela. |
| `ZURI_LEADS_RATE_WINDOW_SECONDS` | Janela do limitador; padrão 900 segundos. |
| `ZURI_COMMERCIAL_DIAGNOSTICS_ENABLED` | Gate explícito da fila paga da Landing; sem `true`, o formulário não encaminha requests ao n8n. |
| `ZURI_DATAFORSEO_COMMERCIAL_USE_APPROVED` | Aprovação documental do uso comercial dos dados DataForSEO, incluindo os limites da seção 7.1. |
| `ZURI_GOOGLE_UPSTREAM_USE_APPROVED` | Aprovação separada dos termos/direitos aplicáveis do Google, que não são concedidos pela DataForSEO. |
| `GEOGRID_MAPTILER_COMMERCIAL_PLAN_CONFIRMED` | Confirmação de plano comercial e limites MapTiler antes do uso em produto comercial. |
| `ZURI_N8N_EXPRESS_PIPELINE_ENABLED` | Gate adicional para conectar a jornada comercial Express ao webhook n8n existente. |
| `ZURI_GOOGLE_COORDINATE_STORAGE_APPROVED` | Aprovação separada exigida porque o workflow atual resolve coordenadas pelo Google Places. |
| `ZURI_PROSPECTING_DATA_USE_APPROVED` | Aprovação separada para reter/exportar candidatos concorrentes derivados dos resultados existentes; não habilita chamadas ou enriquecimento. |
| `ZURI_PRICE_EXPRESS_BRL` | Preço experimental Express em reais; padrão `0.00`. |
| `ZURI_PRICE_PROFESSIONAL_BRL` | Preço experimental Professional em reais; padrão `29.90`. |
| `ZURI_PRICE_MONITOR_BRL` | Preço experimental Monitor em reais; padrão `97.00`. |

Os workflows desligam armazenamento de dados de execução em sucesso/erro nas
configurações exportadas. Confirme a política de execução, logs do proxy e
redação de cabeçalhos na sua versão instalada; não habilite debug com payloads
de leads. A webhook é pública para receber o formulário: aplique TLS e
rate-limit/antibot no proxy. O backend valida o header `Origin` contra
`ZURI_ALLOWED_ORIGINS` ou, sem allowlist, contra a origem vista pelo FastAPI;
configure a allowlist quando TLS termina no proxy e o backend vê HTTP. Não
coloque tokens de serviço no navegador. A resposta `202` só ocorre depois que a linha
foi persistida. Erros de validação devolvem `422`; indisponibilidade da fila
devolve `503` para permitir retry com a mesma combinação campanha/e-mail.

O catálogo público pode informar os preços experimentais. No simulador,
Professional gera apenas dados sintéticos e Monitor registra interesse
assistido. No caminho real, Express usa somente o workflow Standard Normal 3×3
existente; Professional e Monitor ficam limitados a atendimento assistido,
sem checkout ou processamento automático. Não definir os gates como `true`
apenas para liberar a página: o uso upstream de SERP Google permanece sem
parecer/confirmação, o plano MapTiler deve ser confirmado e a resolução de
coordenadas exige a aprovação compatível. O processador atual aceita somente
grids 3×3.

A captura administrativa de leads exige consentimento de marketing para
exportação CSV. Prospectos sintéticos são marcados como simulação e não são
exportáveis. Prospectos DataForSEO só podem ser retidos/exportados após a
aprovação de fonte e armazenamento; a interface admin utiliza sessão de
operador HttpOnly/SameSite Strict, Secure por padrão. O token de operador e o
token servidor-a-servidor WordPress devem permanecer no backend.

## 6. Importar e configurar os workflows

Importe, mantendo todos inativos:

1. `workflows/n8n/01-recepcao-leads.json`
2. `workflows/n8n/02-processador-standard.json`
3. `workflows/n8n/03-recuperacao-resultados.json`
4. `workflows/n8n/04-status-diagnostico.json`
5. `workflows/n8n/05-relatorio-protegido.json`
6. `workflows/n8n/06-crm-outbox-adapter.json`

Depois da importação:

- Selecione as credenciais placeholders em cada node Postgres/HTTP Request.
- Revise `http://smart-grid:8000` e troque pelo DNS/porta interna reais do
  serviço GeoGrid, se diferentes. Faça o mesmo para o nome da API Places,
  conforme configuração da rede.
- Confira que o `X-GeoGrid-Client-ID` tem o valor estável configurado no
  backend; não aceite esse identificador de uma entrada controlada pelo usuário.
- Mantenha os workflows inativos durante a configuração e teste.
- Verifique no histórico de execução que dados de entrada e headers sensíveis
  não são salvos.
- Ative 01 primeiro; depois 02/03 para processamento, 04/05 para status e
  relatório, e 06 somente quando o adaptador CRM configurado estiver aprovado.
  Faça isso apenas após checagem de orçamento e validação de homologação.

O workflow 01 é a URL de produção exibida pelo Webhook node, por exemplo
`https://<dominio-n8n>/webhook/zuri-smart-grid/leads`. Não use URL de teste em
produção. Configure a Landing Page `/landing`; ela mantém o mesmo UUID durante retry de
rede e encaminha ao webhook apenas pelo backend. Configure também as URLs dos
webhooks status/relatório no backend: os workflows 04/05 usam os caminhos fixos
`/webhook/zuri-smart-grid/status` e `/webhook/zuri-smart-grid/report`. O backend
envia `request_id` e capability somente no corpo POST. Essa forma foi validada
no n8n 1.123.21; paths com `:request_id` foram registrados literalmente e não
atenderam URLs com UUID no segmento.

## 7. Contrato do formulário e respostas

### Landing Page e entrada `POST /webhook/zuri-smart-grid/leads`

A pessoa acessa `/landing`. A página carrega campanha/política via
`GET /api/landing/config` e submete em same-origin `POST /api/landing/leads`.
O backend encaminha a solicitação para n8n com
`ZURI_N8N_LEADS_WEBHOOK_URL`; nenhum token de serviço ou URL de integração
privada é entregue ao navegador.

```json
{
  "contact_name": "Ana Exemplo",
  "company_name": "Clínica Exemplo",
  "email": "ana@example.com",
  "whatsapp": "+5511999999999",
  "city": "São Paulo",
  "state": "SP",
  "google_maps_url": "https://www.google.com/maps/place/Cl%C3%ADnica+Exemplo/data=!4m2!3m1!1sChIJexamplePlaceId",
  "keyword": "clínica odontológica",
  "campaign_id": "evento-2026-001",
  "request_id": "7ebf35a1-88c4-4d8c-ae73-8a12d15442cb",
  "status_token": "<64 caracteres hexadecimais aleatórios>",
  "privacy_acknowledged": true,
  "marketing_consent": false,
  "privacy_policy_version": "2026-11",
  "website": ""
}
```

O link Google Maps é opcional; se informado, precisa ser HTTPS e domínio Google
Maps permitido. `privacy_acknowledged` é obrigatório; a escolha
`marketing_consent` é separada e pode ser `false`. Request UUID v4 e capability
aleatória de 256 bits são criados no navegador; o token puro nunca é persistido.
O backend injeta a versão configurada da política. A idempotência continua
baseada em campanha + e-mail normalizado; conteúdo diferente na mesma chave
resulta em conflito, sem sobrescrita.

Resposta HTTP `202` do proxy e do webhook depois da persistência:

```json
{
  "accepted": true,
  "request_id": "7ebf35a1-88c4-4d8c-ae73-8a12d15442cb",
  "status": "queued",
  "duplicate": false,
  "status_token": "<somente devolvido ao navegador desta submissão>"
}
```

Um retry com o mesmo UUID, capability e conteúdo retorna o mesmo `request_id`
e `duplicate: true`. Se campanha/e-mail já corresponderem a outro pedido, o
backend devolve conflito genérico `409` sem revelar o protocolo existente nem
associar uma capability nova. O status token é devolvido somente ao navegador
que iniciou a solicitação, para formar o link protegido; tokens de serviço,
senhas e credenciais nunca são devolvidos. Se a capability for perdida, não há
recuperação por WhatsApp/e-mail nesta etapa. Não a inclua em analytics, logs,
tickets ou links públicos.

### Status e relatório protegido

O recibo oferece um link `/diagnostico#request_id=<uuid>&token=<capability>`.
O fragmento não é enviado ao servidor nem ao Referer. A página encaminha o token
no corpo POST por meio do backend; webhooks 04/05 calculam SHA-256 e comparam
com `geogrid_status_tokens`. O endpoint de status retorna apenas autorização,
estado, progresso, fim e disponibilidade do relatório. O relatório só é
obtido pelo serviço Smart Grid após capability válida e resolução dos nove
pontos; a página oferece impressão/salvar como PDF.

### CRM T2S

A trigger PostgreSQL cria eventos `lead_received` e `diagnostic_status_changed`
em `geogrid_crm_outbox`. Ela mantém origem, campanha, dados de contato,
consentimentos, IDs/status do diagnóstico, etiquetas semânticas e chaves
simbólicas de pipeline/etapa. A outbox não chama a T2S: nenhum endpoint, ID de
funil/tag nem esquema de autenticação foi presumido. Use
[`crm-t2s-adapter.md`](./crm-t2s-adapter.md) e a documentação oficial da conta
antes de configurar upsert, atualização de status ou envio de relatório.

## 8. Payloads DataForSEO e Smart Grid

### Submissão Standard Normal

O workflow 02 cria nove itens com coordenadas fornecidas pelo endpoint de
criação de scan do Smart Grid. O corpo enviado ao endpoint DataForSEO é uma
lista, por exemplo:

```json
[
  {
    "keyword": "clínica odontológica",
    "location_coordinate": "-23.5415,-46.6437,14z",
    "language_code": "pt",
    "priority": 1,
    "depth": 20,
    "tag": "7ebf35a1-88c4-4d8c-ae73-8a12d15442cb:0:0"
  }
]
```

O exemplo mostra um elemento; o workflow envia nove elementos em uma chamada
para `POST /v3/serp/google/maps/task_post`. Cada ID retornado e custo são
persistidos no PostgreSQL antes de iniciar recuperação. Tags relacionam os IDs
às células `row`/`col`. Falha/timeout na resposta após começar o POST não é
reenviada automaticamente; a solicitação entra em `submission_uncertain`.
Se não houver ID nem código DataForSEO de rejeição explícita, o ponto também
fica incerto e não é reportado ao Smart Grid como falha terminal. Se IDs forem
recebidos mas o custo total estiver ausente, os resultados continuam sendo
recuperados pelos IDs; a reserva financeira permanece retida até reconciliação.

### Recuperação Standard Advanced

Para cada ID persistido, o workflow 03 consulta:

```http
GET https://api.dataforseo.com/v3/serp/google/maps/task_get/advanced/{task_id}
```

Classificação normalizada:

- `20100`, `40601`, `40602`: tarefa ainda pendente; agenda consulta posterior.
- `40102`: ausência legítima de resultados (`no_results`), não posição `20+`.
- `20000` com itens `maps_search`: normaliza até 20 resultados e importa como
  `ok`; o Smart Grid calcula posição e indicadores.
- `20000` sem itens de mapa: ausência de resultado de mapa (`no_results`).
- Outros códigos: `error`, mantendo código/mensagem DataForSEO e categoria
  quando reconhecida. Erros não viram posição `20+`.

O callback ao GeoGrid usa os contratos descritos em
[n8n-integration.md](./n8n-integration.md). Exemplo de uma célula:

```json
{
  "points": [
    {
      "row": 0,
      "col": 0,
      "lat": -23.5415,
      "lng": -46.6437,
      "status": "ok",
      "dataforseo_task_id": "11131718-1535-0161-0000-bc49145f65fc",
      "results": [
        {
          "name": "Clínica Exemplo",
          "place_id": "ChIJexamplePlaceId",
          "lat": -23.5415,
          "lng": -46.6437,
          "rating": 4.8,
          "reviews": 75,
          "category": "Clínica odontológica",
          "address": "São Paulo, SP"
        }
      ],
      "error": null,
      "error_category": null,
      "api_status_code": 20000,
      "api_status_message": "Ok."
    }
  ]
}
```

Falhas por ponto sem ID são importadas separadamente e reconciliadas de forma
idempotente; resultados fora de ordem são vinculados por coordenada/tag, não
pela ordem de chegada. A API `/complete` é chamada somente quando os nove
pontos forem resolvidos.

## 9. Recuperação e reconciliação manual

1. Consulte `geogrid_requests` e `geogrid_tasks` pelo UUID; consulte também
   `geogrid_execution_logs` para o evento e timestamps. Não imprima os campos
   de contato em logs ou tickets.
2. Se o request estiver `submission_uncertain`, **não** volte seu status a
   `queued` nem invoque `task_post`. Revise a execução n8n e concilie cada
   `tag`/ponto com a lista Tasks Ready/DataForSEO ou com o suporte DataForSEO.
3. Se o ID DataForSEO for confirmado, grave manualmente o ID no ponto correto e
   marque a tarefa como `submitted`; em seguida deixe workflow 03 recuperar o
   resultado por ID. Se não for possível confirmar se a submissão foi cobrada,
   mantenha `submission_uncertain` e escale para revisão financeira.
4. Se confirmar que nenhum task_post foi aceito/cobrado, documente a evidência,
   corrija a causa e só então autorize operador a liberar orçamento e re-enfileirar
   a solicitação. Não há procedimento automatizado para esse override.
5. Falhas pré-submissão seguras podem retornar a `queued` pela função SQL. A
   criação de business/scan é idempotente no Smart Grid; não há custo de busca
   DataForSEO nessa fase.
6. Após reiniciar n8n/PostgreSQL, valide volume e conectividade, execute a
   recuperação dos leases expirados (`SELECT geogrid_recover_expired_work();`)
   e deixe 03 continuar pelos IDs já persistidos. Tarefas pagas incertas seguem
   para reconciliação manual; resultados e callbacks repetidos são idempotentes.

Nunca apague dados para “destravar” a fila. Preserve o registro financeiro e
os logs até a reconciliação ser concluída.

## 10. Verificação local e homologação

Validação local sem rede:

```sh
node -e 'for (const f of ["workflows/n8n/01-recepcao-leads.json","workflows/n8n/02-processador-standard.json","workflows/n8n/03-recuperacao-resultados.json"]) JSON.parse(require("fs").readFileSync(f,"utf8"))'
pytest -q tests/test_n8n_queue_artifacts.py
```

Os testes não chamam DataForSEO, Google Places, Smart Grid remoto nem PostgreSQL
de produção. Antes de ativar em Easypanel, use ambiente isolado de homologação:

1. PostgreSQL descartável em volume de homologação e orçamento/budget caps
   pequenos.
2. Webhook com e-mails e empresas sintéticos; confirme deduplicação em
   submissões concorrentes.
3. Intercepte HTTP de DataForSEO em mock ou sandbox explicitamente sem cobrança;
   nunca use `task_post` real em teste.
4. Injete queda antes do POST e após o envio, timeout, resposta parcial, restart
   e token incorreto. Confirme que submissão ambígua nunca é repetida.
5. Confirme importação de resultado repetida, fora de ordem e finalização dos
   nove pontos.
6. Só depois de aprovação explícita configure os secrets de produção e ative
   os seis workflows.

Os resultados abaixo vieram de containers e volumes exclusivos de homologação.
Não alteraram o SQLite local/de produção, não publicaram no Easypanel e não
fizeram chamadas reais a DataForSEO, Google Places ou T2S.

### Diagnóstico e correção dos HTTP 502

A Landing Page usava timeout fixo de 8 segundos para o webhook. O workflow de
recepção só responde após persistir no PostgreSQL; sob carga, o cliente podia
expirar e devolver `502` embora a linha já tivesse sido gravada. Isso explica
por que “persistido” não equivalia a “HTTP 202 confirmado”. O timeout agora é
configurável por `ZURI_N8N_TIMEOUT_SECONDS` (padrão 30 s, faixa 1–120 s), com
timeout de conexão/pool limitado a 5 s. Falhas de transporte e timeout são
devolvidos como `503` sanitizado e retryable, nunca como sucesso; qualquer
retry precisa manter o mesmo `request_id` e payload.

Com concorrência n8n 20 e timeout de 30 s ainda ocorreram respostas retryable
sob 500 solicitações. A configuração isolada final usou n8n
`N8N_CONCURRENCY_PRODUCTION_LIMIT=50` e timeout do backend de 60 s. Nessa
configuração, o runner recebeu `202` em todas as submissões, sem `502` ou
`503`. Isso é uma configuração comprovada para este container, não uma
garantia de capacidade no Easypanel.

### Carga integrada final

- Serviços exercitados sem substituir o n8n: Landing Page, proxy Nginx,
  FastAPI, seis workflows n8n `1.123.21`, PostgreSQL `16-alpine`, simuladores
  HTTP de DataForSEO/Places/CRM e integração externa do Smart Grid.
- Lotes progressivos: 50 com concorrência 10, 100 com concorrência 25 e 500
  com concorrência 50. Os 650 pedidos dos lotes e um caso especial
  `No Search Results` receberam HTTP `202`; houve três retries de duplicação
  explícitos, também aceitos como duplicados. Total observado: 654 respostas
  `202`, zero `502`/`503`, 651 solicitações únicas e zero linhas duplicadas.
- Latência de recepção (média / p95 / máximo): 50 — 1,86 / 2,52 / 2,59 s;
  100 — 3,70 / 4,41 / 4,59 s; 500 — 9,30 / 12,86 / 13,84 s. O lote de 500
  levou 100,22 s para receber todas as respostas. Tempos de processamento:
  45,57 s, 90,97 s e 435,69 s, respectivamente.
- Estados de processamento: lote 50 — 50 `done`; lote 100 — 98 `done`,
  1 `manual_review`, 1 `submission_uncertain`; lote 500 — 446 `done`,
  33 `manual_review`, 21 `submission_uncertain`. No conjunto (incluindo o
  caso especial), 595 ficaram `done`, 34 em revisão manual e 22 em
  reconciliação. Revisão manual não submeteu tarefas DataForSEO. Submissões
  incertas não podem ser reenviadas automaticamente.
- O mock registrou 595 chamadas `task_post`, 5.355 consultas `task_get`, 617
  respostas válidas do Places e 3.760 eventos CRM. Os 5.355 IDs DataForSEO
  salvos eram únicos e associados a tarefas persistidas; 0 IDs duplicados,
  0 custo simulado e 0 custo real. O CRM foi exclusivamente simulado.
- Status/relatório protegido, isolamento entre tenants, rejeição de bearer
  inválido, nove pontos por grade e `40102 No Search Results` em todos os nove
  pontos passaram.
- Limitação observada: 34 pedidos foram direcionados a `manual_review` com
  mensagem genérica de verificação de empresa/localização; a causa específica
  dessa ramificação não foi isolada. Além disso, 22 pedidos ficaram
  `submission_uncertain`, estado que requer reconciliação manual. Ambos devem
  ser revisados antes de ativar tráfego real.

### Recuperação após reinicialização

Em uma rodada separada, n8n e Smart Grid foram reiniciados com solicitações
pendentes e IDs simulados já persistidos. Ambos voltaram a ficar saudáveis; o
polling aumentou de 2.874 para 3.183 consultas e os pedidos `done` de 299 para
334 após a retomada. Os IDs permaneceram únicos; o aumento de `task_post`
correspondeu à continuação de novos pedidos da fila, não ao reenvio dos IDs
persistidos. Tarefas ainda em estado de submissão durante a interrupção foram
marcadas para reconciliação e não foram reenviadas automaticamente. Volumes
foram preservados.

### Capacidade, validações e limitações

- Na amostragem de dois segundos durante a carga final, o container n8n chegou
  a 173,95% de CPU e 1.767 MiB de memória. São picos desta máquina (7,76 GiB
  visíveis ao Docker), não um sizing certificado para produção.
- A suíte completa terminou com `206 passed`, 0 falhas e 1 aviso de
  depreciação do `httpx` usado pelo `TestClient`.
- A rede Compose era `internal: true`, todas as chamadas externas foram para
  simuladores e nenhum secret real foi configurado. A integração T2S real,
  reconciliação de submissões ambíguas em provedor real e dimensionamento no
  Easypanel continuam pendentes.
- Uma rodada histórica anterior registrou 650 pedidos: 623 `done`,
  20 `manual_review` e 7 `submission_uncertain`, mas falhou na recepção HTTP
  por `502`. Esses números são evidência histórica e não devem ser somados aos
  resultados da carga final.

Nenhuma chamada DataForSEO, Google Places ou CRM real, credencial real,
publicação, commit ou push foi realizada.

Não foi habilitada nem utilizada credencial real de DataForSEO, Google Places
ou Smart Grid. Chamadas `task_post` foram feitas somente contra o simulador
HTTP local e nunca contra a DataForSEO.

Comandos reutilizáveis em uma máquina homologadora com Docker:

```sh
# PostgreSQL descartável; não use a porta nem o volume em produção
docker run -d --name geogrid-pg-homolog \
  -p 127.0.0.1:55432:5432 \
  -e POSTGRES_PASSWORD='<senha-descartavel>' \
  -e POSTGRES_DB=postgres postgres:16-alpine

docker exec geogrid-pg-homolog \
  psql -U postgres -d postgres -c 'CREATE DATABASE geogrid_queue;'

docker exec -i geogrid-pg-homolog \
  psql -v ON_ERROR_STOP=1 -U postgres -d geogrid_queue \
  < workflows/n8n/sql/001-schema.sql

docker exec -i geogrid-pg-homolog \
  psql -v ON_ERROR_STOP=1 -U postgres -d geogrid_queue \
  < workflows/n8n/sql/002-functions.sql

docker exec -i geogrid-pg-homolog \
  psql -v ON_ERROR_STOP=1 -U postgres -d geogrid_queue \
  < workflows/n8n/sql/003-landing-crm-capability.sql

docker exec -i geogrid-pg-homolog \
  psql -v ON_ERROR_STOP=1 -U postgres -d geogrid_queue \
  < workflows/n8n/sql/004-capability-lifecycle-and-crm-dispatch.sql

docker exec -i geogrid-pg-homolog \
  psql -v ON_ERROR_STOP=1 -U postgres -d geogrid_queue \
  < workflows/n8n/sql/006-paid-submission-authorization-and-budget.sql

docker exec -i geogrid-pg-homolog \
  psql -v ON_ERROR_STOP=1 -U postgres -d geogrid_queue \
  < workflows/n8n/sql/007-processor-error-observability.sql

# n8n isolado 1.123.21
docker run -d --name geogrid-n8n-homolog \
  -p 127.0.0.1:5678:5678 \
  -e N8N_BLOCK_ENV_ACCESS_IN_NODE=true \
  -e N8N_PAYLOAD_SIZE_MAX=1 \
  -e NODE_FUNCTION_ALLOW_BUILTIN=crypto \
  docker.n8n.io/n8nio/n8n:1.123.21

# Importar workflows mantendo-os inativos
for f in workflows/n8n/*.json; do
  docker cp "$f" "geogrid-n8n-homolog:/tmp/$(basename "$f")"
done
docker exec geogrid-n8n-homolog \
  sh -c 'for f in /tmp/*.json; do n8n import:workflow --input="$f"; done'
```

### Recursos recomendados no Easypanel

Valores iniciais de produção devem ser revisados por medição; não substituem
os limites dos recursos do host compartilhado.

| Componente | Mínimo inicial | Recomendado | Persistência/observabilidade |
|---|---:|---:|---|
| PostgreSQL da fila | 1 vCPU, 1 GB RAM | 2 vCPU, 2 GB RAM | Volume 10 GB+; backup lógico diário e backup do volume |
| n8n | 2 vCPU, 2 GB RAM | 4 vCPU, 4 GB RAM para a configuração testada com 50 execuções concorrentes | Volume do banco interno e `/home/node/.n8n`; logs sanitizados |
| Smart Grid | conforme carga atual | reservar margem para 500 leads simultâneos | SQLite atual em volume persistente |

O PostgreSQL precisa de `max_connections` suficiente para o node n8n e
execuções concorrentes, mas sem exceder memória. Configure inicialmente 50
conexões e revise o pool do node. O limite n8n de 50 foi necessário para a
carga de 500 nesta homologação, mas elevou o pico do container a cerca de
1,74 vCPU e 1,73 GiB; aplique somente após medir CPU e memória do host, pois
afeta todos os workflows do servidor.
Habilite `N8N_ENFORCE_SETTINGS_FILE_PERMISSIONS=true` no homologador.

Descarte logs temporários e mantenha volume dedicado para o PostgreSQL.
Monitore: taxa de claims por minuto, tarefas pendentes, reservas e gastos,
submissões incertas, erros de callback, espaço em disco e tamanho dos logs.

### Checklist de implantação

1. Preservar e registrar o SQLite atual e todos os backups.
2. Criar um banco PostgreSQL novo em `geogrid_queue`; não apontar para banco
   interno do n8n nem para o SQLite.
3. Aplicar as migrations 001–004, 006 e 007 em ordem em homologação; executar
   em produção somente após aprovação, backup validado e parada dos workers.
4. Criar usuário PostgreSQL exclusivo com apenas os privilégios necessários.
5. Configurar credenciais reais no secret manager/n8n; não importar secrets nos
   JSON nem configurar dados no navegador.
6. Configurar limites conservadores, custo estimado por ponto e orçamento diário
   antes de ativar o workflow 02.
7. Configurar timeout, TLS, política de retenção, backup e alerta de disco.
8. Importar os seis workflows e mantê-los inativos.
9. Validar o webhook com payload sintético e respostas `202`/`422`/`503`.
10. Executar homologação com mocks HTTP de DataForSEO e Google Places.
11. Confirmar que as chamadas pagas estão bloqueadas no ambiente de homologação.
12. Ativar 01 primeiro, depois 02/03, 04/05 e por último 06 após configurar
    e homologar o adaptador CRM.
13. Monitorar os primeiros eventos e manter rollback disponível.

### Checklist de rollback

1. Desativar imediatamente os workflows 02/03; depois 04/05 e por fim 01.
2. Não chamar `task_post` novamente nem voltar pedidos incertos para `queued`.
3. Exportar `geogrid_requests`, `geogrid_tasks`, `geogrid_budget` e
   `geogrid_execution_logs` antes de qualquer intervenção.
4. Preservar IDs, custos e timestamps; não apagar histórico ou executar
   `TRUNCATE`/`DROP` na fila.
5. Restaurar apenas o PostgreSQL e o n8n a partir de backups conhecidos, sem
   alterar o SQLite histórico do Smart Grid.
6. Investigar callbacks não entregues e retomá-los pelos IDs persistidos.
7. Se houver cobrança ambígua, manter `submission_uncertain` e escalar para
   reconciliação manual/DataForSEO.
8. Retornar ao modo Live da interface web sem retirar o suporte a Live;
   esse modo não depende dos workflows n8n.

## Referências técnicas

- [n8n Data Table row operations](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.datatable/rows/)
- [n8n Postgres node](https://docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.postgres/)
- [n8n concurrency control](https://docs.n8n.io/deploy/host-n8n/configure-n8n/scaling/control-concurrency/)
- [n8n execution environment variables](https://docs.n8n.io/deploy/host-n8n/configure-n8n/basic-configuration/use-environment-variables/executions/)
- [DataForSEO Google Maps task_post](https://docs.dataforseo.com/v3/serp/google/maps/task_post/)
- [DataForSEO Google Maps task_get advanced](https://docs.dataforseo.com/v3/serp/google/maps/task_get/advanced/)

# Contrato da jornada comercial para WordPress

Este contrato descreve a API servidor-a-servidor do Smart Grid para a futura
Landing Page WordPress/Elementor. Há dois modos distintos: jornada sintética
para homologação e Express Standard Normal encaminhado à fila n8n quando todos
os gates de operação/licenciamento estiverem aprovados e habilitados. O modo
sintético usa apenas dados fictícios e não faz chamadas externas. A existência
do caminho real no código não autoriza sua ativação: não há checkout real,
integração automática de pagamento nem processamento Professional real.
Professional e Monitor podem receber solicitações de atendimento assistido no
modo real, sem cobrança ou liberação automática.

## Autenticação e transporte

- Base local de homologação: `https://<host-do-backend>/api/wordpress/v1`.
- O WordPress deve chamar o backend a partir do servidor, nunca do navegador.
- Enviar `Authorization: Bearer <GEOGRID_WORDPRESS_API_TOKEN>` em todas as
  chamadas. O token deve ter pelo menos 32 caracteres e não pode ser incluído
  no HTML, JavaScript, Elementor, URL, analytics ou logs.
- Usar HTTPS, JSON UTF-8 e `Content-Type: application/json`.
- IDs de solicitação devem ser UUIDv4. Repetir o mesmo ID com o mesmo conteúdo
  é idempotente; reutilizá-lo com outro conteúdo resulta em `409`.
- O token de status é uma capability aleatória de 64 caracteres hexadecimais.
  Ele é devolvido ao titular, fica armazenado somente como hash e deve ser
  enviado no corpo de status/relatório, nunca na URL.
- Não confiar em retorno de checkout como confirmação de pagamento.

## Pesquisa e confirmação da empresa

### `POST /businesses/search`

```json
{
  "request_id": "UUIDv4",
  "business_name": "Café Lume",
  "city": "São Paulo",
  "state": "SP",
  "address": "Rua das Acácias, 101",
  "google_maps_url": null,
  "place_id": null
}
```

É permitido pesquisar com nome/cidade/estado, endereço complementar ou Place
ID extraído de um link válido do Google Maps. O simulador aceita apenas seus
IDs sintéticos. Uma consulta sem candidatos não comprova ausência de Perfil da
Empresa no Google.

Resposta inclui `classification`, `message`, `candidates`, `confirmation_required`,
`diagnostic_allowed` e `alternative_actions`. Cada candidato pode incluir nome,
endereço, cidade/UF, categoria, referência/Place ID, coordenadas e link de mapa,
além de um `candidate_token` de curta duração. Os valores de coordenadas e mapa
do simulador são fictícios.

Classificações incluem `BUSINESS_LOCATED`, `BUSINESS_AMBIGUOUS`,
`BUSINESS_NOT_FOUND`, `BUSINESS_INSUFFICIENT_DATA`,
`BUSINESS_LOCATION_MISMATCH`, `BUSINESS_IDENTITY_MISMATCH`,
`INVALID_PLACE_ID` e erros técnicos do provedor. Erros técnicos não são
classificados como empresa inexistente.

### `POST /businesses/confirm`

```json
{
  "request_id": "UUIDv4",
  "candidate_token": "<token recebido para o candidato escolhido>",
  "confirmed": true
}
```

Exige ação explícita do visitante. Responde `business_confirmed: true` e um
`confirmation_token`; a confirmação de identidade não libera diagnóstico nem
altera permissões de armazenamento.

## Leads e diagnósticos

### `POST /leads`

```json
{
  "request_id": "UUIDv4",
  "status_token": "<64 caracteres hexadecimais>",
  "business_confirmed": true,
  "confirmation_token": "<token confirmado>",
  "search_request_id": "UUIDv4",
  "business_search": {
    "request_id": "UUIDv4",
    "business_name": "Café Lume",
    "city": "São Paulo",
    "state": "SP",
    "address": "Rua das Acácias, 101",
    "google_maps_url": null
  },
  "business_name": "Café Lume",
  "city": "São Paulo",
  "state": "SP",
  "address": "Rua das Acácias, 101",
  "google_maps_url": null,
  "contact_name": "Nome do responsável",
  "email": "pessoa@example.test",
  "whatsapp": "+5511999999999",
  "campaign_id": "campanha-configurada",
  "privacy_acknowledged": true,
  "marketing_consent": false,
  "website": ""
}
```

Para atendimento assistido sem empresa confirmada, usar `business_confirmed:
false` e omitir `confirmation_token`, `search_request_id` e `business_search`.
Esse lead não pode iniciar um diagnóstico. Para lead confirmado, enviar em
`business_search` a pesquisa original devolvida/enviada a
`/businesses/search`; seu `request_id` deve ser igual a `search_request_id`.
Isso preserva a prova de confirmação mesmo quando o nome canônico do candidato
escolhido difere do texto de pesquisa. Consentimento de marketing é opcional; a
política e sua versão devem estar configuradas no backend.

Resposta `201`: `accepted`, `lead_id`, `request_id`, `status_token`,
`business_confirmed`, `lead_type` e `duplicate`. O token de status deve ser
entregue ao titular em canal seguro; não registrar o corpo em logs.

### `POST /diagnostics`

Disponível nesta versão apenas para Express. O servidor aplica grade e
espaçamento configurados no produto (por padrão 3×3, 1 km e uma palavra-chave).

```json
{
  "request_id": "UUIDv4",
  "lead_id": "UUID",
  "status_token": "<capability do lead>",
  "product_id": "express",
  "confirmation_token": "<token confirmado>",
  "search": {
    "request_id": "UUIDv4 da pesquisa",
    "business_name": "Café Lume",
    "city": "São Paulo",
    "state": "SP",
    "address": "Rua das Acácias, 101"
  },
  "keyword": "cafeteria em São Paulo",
  "confirmed": true
}
```

Em homologação, a resposta `202` contém `data_mode: "synthetic"` e
`provider_calls: 0`. Com o pipeline real autorizado, Express usa grid 3×3,
uma palavra-chave, espaçamento de 1 km, `priority: 1` e fila n8n Standard
Normal; a resposta usa `data_mode: "standard_normal"` e `queued_via: "n8n"`.
Em ambos os modos há um objeto `diagnostic` com ID, produto, grade, status,
progresso e total. Repetições com o mesmo `request_id` reutilizam a mesma
solicitação; timeouts devem ser resolvidos com o mesmo ID, nunca com uma nova
submissão automática.

Professional não pode ser iniciado por esse endpoint. No simulador, um pedido
pode ser confirmado e liberado manualmente por operador para gerar dados
sintéticos 7×7. No modo real, Professional e Monitor são apenas solicitações
de atendimento assistido; não existe checkout real, diagnóstico Professional,
confirmação financeira nem liberação automática nesse fluxo.

### `POST /leads/{lead_id}/status`

```json
{"lead_id": "UUID", "status_token": "<capability do lead>"}
```

Retorna apenas tipo do lead, status e progresso dos diagnósticos e estados dos
pedidos. Não retorna telefone, e-mail ou token de integração.

### `POST /diagnostics/{diagnostic_id}/report`

Usa o mesmo corpo de capability acima. O acesso é limitado ao lead proprietário.
O Express retorna um resumo e oferta Professional; o Professional retorna os
indicadores existentes e resultados por ponto. A cobertura válida é sempre
apresentada; relatórios com cobertura incompleta são marcados como incompletos.
Relatório ainda não pronto responde `409 REPORT_NOT_READY`.

## Pedidos e pagamentos

### `POST /orders`

```json
{
  "request_id": "UUIDv4",
  "lead_id": "UUID",
  "status_token": "<capability do lead>",
  "source_diagnostic_id": "UUID do Express concluído",
  "product_id": "professional"
}
```

O preço é capturado do catálogo do backend no momento do pedido. A resposta
contém `order_id`, `amount_brl`, `status`, `checkout_url`,
`checkout_is_payment_proof: false`, `fulfillment` e `duplicate`. Em modo
sintético, Professional aguarda confirmação manual e pode ter somente checkout
de teste em host `*.example.test`; ele nunca comprova pagamento. No modo real
aprovado, pedidos Professional/Monitor ficam em `status: "assisted"`,
`fulfillment: "assisted"` e `checkout_url: null`. Monitor não cria recorrência.

### `GET /payments/{order_id}`

Consulta autenticada do estado do pedido, valor, confirmação registrada e
diagnóstico associado. Não retorna o token do operador nem a referência
financeira completa.

## Operação autorizada

O backend mantém rotas separadas, não destinadas ao navegador WordPress:

- `POST /api/commercial/operator/orders/{order_id}/confirm-payment`
- `POST /api/commercial/operator/orders/{order_id}/release`
- `GET /api/commercial/operator/orders/{order_id}`

Exigem `Authorization: Bearer <GEOGRID_COMMERCE_OPERATOR_TOKEN>` e
`X-Operator-ID` presente em `GEOGRID_COMMERCE_OPERATOR_IDS`. No simulador, a
confirmação manual registra operador, referência e evento de auditoria.
Somente pedido Professional em estado `payment_confirmed` pode ser liberado.
Essas operações não habilitam pagamento ou processamento reais. Pagamento não
é inferido do checkout. Monitor não pode ser liberado como diagnóstico recorrente.

## Administração de leads e concorrentes

A interface operacional fica em `/api/commercial/admin`; autenticação usa
`GEOGRID_COMMERCE_OPERATOR_TOKEN` e uma sessão em cookie HttpOnly, SameSite
Strict, Secure por padrão e com duração de 30 minutos. Configure HTTPS e não
use `ZURI_COMMERCE_ADMIN_ALLOW_INSECURE_HTTP` fora de testes isolados. A API
admin permite filtrar leads/prospectos e exportar CSV. Leads só são exportáveis
com consentimento de marketing explícito. Prospectos sintéticos nunca são
exportáveis; retenção/exportação de candidatos reais exige
`ZURI_PROSPECTING_DATA_USE_APPROVED=true` e compatibilidade contratual da fonte.
Os registros de concorrentes vêm somente dos pontos já obtidos no diagnóstico;
não há enriquecimento nem chamadas pagas adicionais.

## Erros HTTP

| Código | Uso |
| --- | --- |
| `401` | Bearer ausente/inválido ou capability inválida |
| `403` | Origem não permitida ou operador fora da lista autorizada |
| `404` | Lead, pedido ou relatório não encontrado para aquela capability |
| `409` | Confirmação ausente/incompatível, conflito de idempotência, transição inválida ou relatório pendente |
| `422` | Payload, UUID, consentimento ou dados de entrada inválidos |
| `429` | Rate limit ou capacidade diária/de tarefas pendentes atingida |
| `503` | Produto/gates desabilitados, configuração incompleta ou política ainda não aprovada |

Erros de domínio retornam `detail.category` quando aplicável. O cliente deve
exibir mensagem segura e não repetir submissões com novo ID em caso de timeout
sem primeiro consultar o status/pedido correspondente.

## Configuração de homologação

Configurar no backend, nunca no browser:

- `ZURI_COMMERCIAL_SIMULATOR_ENABLED=true`
- `GEOGRID_BUSINESS_FINDER_PROVIDER=simulator`
- `GEOGRID_BUSINESS_FINDER_SIGNING_SECRET` (segredo aleatório de pelo menos 32 bytes)
- `ZURI_CAMPAIGN_ID` / `ZURI_ALLOWED_CAMPAIGNS`
- `ZURI_PRIVACY_POLICY_URL` / `ZURI_PRIVACY_POLICY_VERSION`
- `GEOGRID_WORDPRESS_API_TOKEN` e, para operações, `GEOGRID_COMMERCE_OPERATOR_TOKEN` e `GEOGRID_COMMERCE_OPERATOR_IDS`
- preços `ZURI_PRICE_EXPRESS_BRL`, `ZURI_PRICE_PROFESSIONAL_BRL`,
  `ZURI_PRICE_MONITOR_BRL`; disponibilidade `ZURI_PRODUCT_*_ENABLED`;
  características de grade `ZURI_PRODUCT_*_GRID_SIZE`
- limites `ZURI_COMMERCIAL_DAILY_DIAGNOSTICS_LIMIT`,
  `ZURI_COMMERCIAL_DAILY_POINTS_LIMIT`,
  `ZURI_COMMERCIAL_PENDING_DIAGNOSTICS_LIMIT` e
  `ZURI_COMMERCIAL_PENDING_POINTS_LIMIT`
- Para Express real, os gates `ZURI_COMMERCIAL_DIAGNOSTICS_ENABLED`,
  `ZURI_DATAFORSEO_COMMERCIAL_USE_APPROVED`,
  `ZURI_GOOGLE_UPSTREAM_USE_APPROVED`,
  `GEOGRID_MAPTILER_COMMERCIAL_PLAN_CONFIRMED`,
  `ZURI_N8N_EXPRESS_PIPELINE_ENABLED` e
  `ZURI_GOOGLE_COORDINATE_STORAGE_APPROVED`, mais o webhook n8n configurado.
  Ativar variáveis sem os direitos e aprovações correspondentes não autoriza
  uso comercial.
- `ZURI_PROSPECTING_DATA_USE_APPROVED` é uma aprovação separada para reter e
  exportar candidatos derivados; não é necessária para completar o diagnóstico.
- link de checkout de teste opcional `ZURI_DOM_CHECKOUT_URL_PROFESSIONAL`,
  exclusivamente em domínio `*.example.test`.

Para desabilitar a experiência simulada, remover/desativar
`ZURI_COMMERCIAL_SIMULATOR_ENABLED`. Isso não habilita por si só o caminho
Express real; todos os gates correspondentes continuam obrigatórios e devem
refletir aprovações documentadas. A publicação e ativação reais não foram
autorizadas.

## Publicação e rollback

Esta entrega não foi publicada. Antes de futura publicação, usar backup e volume
isolado, validar contratos/segredos no backend e confirmar gates de licenças,
termos de fornecedores, orçamento e MapTiler. Não transferir segredos para o
WordPress front-end.

Rollback de aplicação: desabilitar o simulador, reverter a versão da aplicação
sem apagar o banco ou os volumes. O schema comercial é aditivo; tabelas e
históricos devem ser preservados. A antiga Landing Page n8n permanece disponível
em `/landing-legacy`. Não usar `down -v`, limpeza de volumes ou restauração
destrutiva.

# Changelog

Alterações relevantes do Smart Grid by ZURI.

## Não lançado — captura comercial e Express condicionado

- Captura de concorrentes dos resultados já persistidos nos scans Express,
  deduplicação por identidade, histórico de observações e qualificação. Não há
  enriquecimento ou chamadas pagas adicionais.
- Adicionado painel administrativo autenticado para filtrar leads/prospectos
  e exportar CSV; leads exigem consentimento explícito e prospectos reais
  dependem de aprovação de uso separada. Dados sintéticos não são exportáveis.
- Ligado o Express comercial ao webhook n8n existente, reutilizando ID
  idempotente e confirmação da empresa. O caminho real segue fechado pelos
  gates de licenciamento, coordenadas e configuração operacional.
- Professional/Monitor no modo real geram somente solicitações assistidas, sem
  checkout, pagamento ou processamento Professional automático. A pesquisa
  original acompanha o lead para preservar a validação do token.
- Testes focados: 42 aprovados; regressão completa: 253 aprovados, um aviso
  conhecido Starlette/httpx. `node --check`, `git diff --check` e checagem de
  compatibilidade do call site passaram.

## Não lançado — jornada comercial nativa (simulação)

- Adicionada uma jornada de referência para busca sintética, confirmação
  explícita da empresa, captura do lead, diagnóstico Express 3×3, relatório
  protegido, oferta de upgrade, pedido Professional e liberação manual
  auditada com grade 7×7. Monitor continua em contratação assistida.
- Criada persistência aditiva para leads, diagnósticos, pedidos e eventos de
  operador, com limites de utilização/pontos e idempotência. O checkout nunca
  é tratado como confirmação financeira.
- Adicionada API WordPress autenticada para busca, confirmação, leads,
  diagnósticos, status, relatórios, pedidos e consulta de pagamento; contrato
  em `docs/wordpress-commercial-api.md`.
- Mantidos desativados checkout real, provedores pagos e gates comerciais.
  Landing anterior preservada em `/landing-legacy`; nenhum sistema externo foi
  chamado.
- Corrigida a persistência de pontos pendentes para não duplicar a grade e
  impedida a reexecução automática de scans parciais/finalizados.
- Validação: 62 testes focados antes do teste final do worker, 5 testes
  comerciais após sua inclusão e regressão completa com 249 aprovados; um
  aviso de depreciação Starlette/httpx. JavaScript verificado com
  `node --check`; `git diff --check` sem erros.

## Não lançado — Sprint MVP comercial (proteções parciais)

- Consultados os termos oficiais DataForSEO e MapTiler; a compatibilidade
  upstream do rastreamento Google Maps e o plano comercial MapTiler não foram
  confirmados. O produto comercial permanece bloqueado, sem inferir direitos.
- Adicionado gate fail-closed antes da fila paga da Landing, condicionado a
  quatro flags explícitas de autorização/contratação; o endpoint público
  informa os bloqueios e não encaminha à n8n quando não autorizado.
- Centralizados os preços experimentais e limites declarados de Express,
  Professional e Monitor; são editáveis por variáveis de ambiente. Checkout e
  liberação manual permanecem indisponíveis; nenhum produto fica marcado como
  disponível.
- Testes focados Landing: 33 aprovados; regressão: 244 aprovados. Nenhuma
  chamada paga ou integração real.

## Não lançado — P04 Smart Business Finder (implementado localmente)

- Checkpoint local `f657b8d`, sem push.
- Adicionados endpoints backend de busca, confirmação explícita e início de
  diagnóstico, com classificação de candidatos e validação de identidade e
  localização. Nenhuma empresa é selecionada automaticamente.
- Adicionados limites configuráveis e persistentes, ledger por request ID,
  tokens assinados de curta duração, confirmação única/idempotente e
  proveniência de campos. Consultas e dados brutos dos candidatos não são
  persistidos.
- O scanner falha fechado para fontes sem autorização de armazenamento
  permanente de coordenadas. Google Places não alimenta a grade; OSM depende
  de aceite ODbL, atribuição pronta e instância própria. A API pública
  Nominatim fica bloqueada.
- Testes focados simulados: 25 aprovados; regressão Python: 236 aprovados.
  A busca real permanece desabilitada por padrão e não houve chamada paga.
- P04.1 — revisão de compatibilidade/licenças/retenção/atribuição é prévia
  obrigatória a qualquer habilitação em produção ou publicação do P06.

## Não lançado — P03 concluído localmente

- Landing Page repete somente respostas HTTP 429/5xx e erros de transporte, com
  limite configurável, backoff exponencial com jitter e `Retry-After`, mantendo
  o mesmo identificador e payload idempotentes.
- n8n persiste a resposta paga antes da associação e recupera IDs/custos a
  partir do journal, sem repetir `task_post`. Falhas transitórias seguras
  recebem agenda persistente; submissões incertas sem resposta journalizada
  continuam exigindo reconciliação manual.
- PostgreSQL e n8n isolados com simuladores passaram; regressão Python:
  211 testes aprovados e um aviso de depreciação conhecido.

## Não lançado — P02

- Classificação sanitizada por etapa no processador Standard, incluindo
  categorias técnicas, identidade, orçamento e submissão incerta.
- Registro de fase financeira e de resultados rejeitados por ponto, preservando
  a reserva quando o resultado da submissão ou custo for incerto.
- Testes do classificador, PostgreSQL e regressão P01 com migration 007 passaram.
  n8n isolado cobriu falha de claim e 17 falhas induzidas nos nós do processador
  e callbacks, além dos cenários de identidade, autenticação e sucesso.
- Regressão Python: 206 aprovados e um aviso conhecido.

## Não lançado — checkpoint P01

- Autorização única de `task_post` por claim válido e manutenção de reservas
  incertas, sem reenvio automático.
- Reservas legadas sem data comprovada ficam retidas para reconciliação;
  reservas novas mantêm o escopo diário original, inclusive após virada UTC.
- IDs com custo desconhecido são preservados; custo zero é distinto de
  desconhecido e a reconciliação financeira é idempotente.
- Migration final 006 validada em PostgreSQL isolado; regressão de 206 testes
  aprovada. Reinício n8n com simuladores: 30 concluídos, 30 POSTs simulados e
  270 IDs distintos.
- Limitação: se a resposta HTTP não for persistida nem recuperada, é necessária
  reconciliação manual, sem repetir o POST. Não há garantia universal provada
  de chamada única em todas as falhas distribuídas.
- Checkpoint autorizado e publicado como `6fc53a1`; P02 permanece local.

## Não lançado — homologação integrada

- Preservada a interface Smart Grid Live, com idioma DataForSEO configurável,
  estados de resultados/falhas e cobertura.
- Adicionadas API externa, Landing Page, consulta de status e relatório
  protegido.
- Adicionadas fila PostgreSQL e seis workflows n8n para recepção, processamento
  Standard Normal, recuperação, status, relatório e outbox CRM.
- Adicionados testes automatizados e Compose isolado com DataForSEO, Places e
  CRM simulados.
- Corrigido o timeout fixo de 8 s no proxy da Landing Page. `ZURI_N8N_TIMEOUT_SECONDS`
  agora é configurável (padrão 30 s, faixa de 1–120 s); timeout e falha de
  transporte retornam `503` retryable e indicam retry com a mesma chave
  idempotente.
- A homologação final em n8n `1.123.21`/PostgreSQL 16 usou concorrência 50 e
  timeout 60 s; lotes 50/100/500 receberam HTTP `202` sem `502`/`503`.
- PostgreSQL: 651 requests únicos, 595 `done`, 34 `manual_review` e
  22 `submission_uncertain`; 5.355 IDs DataForSEO simulados únicos,
  duplicidades zero e custo zero. Tarefas incertas seguem sem reenvio
  automático.
- Teste de restart com tarefas pendentes confirmou retomada do polling e
  preservação dos IDs existentes.
- Regressão atual: 206 testes aprovados, 0 falhas e 1 aviso de depreciação do
  `httpx` no `TestClient` (o checkpoint anterior registrava 204 testes).
- A rodada histórica anterior continua registrada: 650 solicitações,
  623 `done`, 20 para revisão manual, 7 submissões incertas e `502`.
- T2S real não conectado; causas específicas dos 34 casos de revisão manual e
  reconciliação dos 22 casos incertos ainda exigem tratamento operacional.
- Documentação de progresso e retomada registrada em `SESSION_HANDOFF.md`.

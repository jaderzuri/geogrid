# Smart Grid by ZURI — Próximas tarefas

## Sprint MVP comercial — jornada simulada implementada

- [x] Capturar leads diretos e consolidar concorrentes com identidade estável
  dos scans já concluídos; sem chamadas de enriquecimento.
- [x] Bloquear armazenamento/exportação comercial de prospectos DataForSEO
  sem gate separado; manter dados sintéticos inelegíveis para CSV.
- [x] Adicionar painel administrativo autenticado, filtros e exportação CSV
  respeitando consentimento de marketing e autorização da fonte.
- [x] Conectar Express ao webhook n8n existente, reaproveitando request ID e
  confirmação, sob gates de Express e armazenamento de coordenadas.
- [x] Manter Professional/Monitor como atendimento assistido no caminho real,
  sem checkout, confirmação de pagamento ou diagnóstico automático.
- [x] Preservar a pesquisa original no payload do lead para validar a
  confirmação quando o nome canônico do candidato divergir.
- [x] Validar focado: `tests/test_commercial_api.py` e
  `tests/test_landing.py` (42 aprovados); `node --check` e `git diff --check`.
- [x] Executar regressão Python consolidada: **253 aprovados**, um aviso
  conhecido Starlette/httpx.
- [x] Revisar a lista final do checkpoint; excluir os backups locais e
  `instalar_ida_adaptativo.py`.

- [x] Verificar termos oficiais DataForSEO e MapTiler e registrar as
  limitações; nenhuma licença de provedor upstream foi inferida.
- [x] Bloquear a entrada da Landing na fila paga sem aprovações explícitas para
  o fluxo comercial, uso DataForSEO, termos upstream Google e plano MapTiler.
- [x] Centralizar os preços experimentais no catálogo do backend e manter
  disponibilidade e configuração dos produtos no backend.
- [x] Implementar busca sintética de empresas, apresentação de homônimos,
  confirmação explícita, jornada alternativa assistida e bloqueio de
  diagnóstico sem empresa confirmada.
- [x] Implementar lead, diagnóstico Express 3×3, relatório simplificado
  protegido, oferta de upgrade, pedido Professional e grade sintética 7×7
  liberada somente por operador após confirmação manual auditada.
- [x] Implementar controles de capacidade/pontos, idempotência e persistência
  aditiva de pedidos e eventos de operador.
- [x] Documentar contratos autenticados para integração WordPress em
  `docs/wordpress-commercial-api.md`.
- [x] Preservar a interface Live anterior em `/landing-legacy`; nenhuma chamada
  paga ou credencial de fornecedor usada no simulador.
- [ ] Obter parecer/confirmação documental para uso de SERP Google Maps em
  relatórios comerciais e confirmar plano comercial, quotas e atribuição
  MapTiler. Não ativar apenas definindo variáveis.
- [ ] Validar os contratos WordPress com integração homologada e autenticação
  por canal servidor-a-servidor antes de expor a API fora do ambiente simulado.
- [ ] Adaptar e homologar separadamente o workflow PostgreSQL/n8n para
  Professional 7×7; o simulador SQLite não altera o workflow Standard atual.
- [ ] Configurar e revisar os gates de provedor/upstream/MapTiler e os links DOM
  antes de qualquer ativação comercial. Checkout não comprova pagamento.
- [ ] Homologar o caminho n8n em ambiente isolado com simuladores após esta
  integração; nenhuma chamada paga/real foi realizada.
- [ ] Resolver os bloqueios contratuais de upstream e MapTiler e obter
  autorização explícita antes da ativação, publicação ou teste real.

## Checkpoint P01 a P03

- [x] Validar autorização única e reservas em PostgreSQL isolado, incluindo
  concorrência, claims expirados, virada UTC e reconciliação legada.
- [x] Validar IDs com custo desconhecido, custo zero, resposta tardia e
  reconciliação idempotente sem novo POST.
- [x] Repetir os testes PostgreSQL com a versão final da migration 006 e a
  regressão de 206 testes.
- [x] Reiniciar o n8n com pendências e simuladores: 30 diagnósticos concluídos,
  30 POSTs simulados e 270 IDs distintos.
- [x] Publicar checkpoint P01 autorizado na branch de feature: `6fc53a1`.
- [x] Implementar classificação sanitizada P02 e testar falhas no classificador
  e no PostgreSQL isolado; repetir regressão P01 com migration 007.
- [x] Testar no n8n isolado falhas de identidade, autenticação simulada e
  caminho válido, sem chamadas pagas.
- [x] Induzir falhas no claim e nos 17 cenários dos nós de processamento e
  callbacks no n8n isolado, com simuladores e reservas verificadas.
- [x] Tratar erros HTTP de protocolo/proxy da Landing Page com 503 sanitizado.
- [x] Implementar P03: backoff exponencial com jitter, limite configurável,
  Retry-After e agenda persistente das retentativas pré-submissão.
- [x] Persistir respostas pagas antes da associação e recuperar IDs/custos de
  forma idempotente, sem repetir task_post; preservar reserva com custo incerto.
- [x] Validar P03 em PostgreSQL isolado e n8n 1.123.21 com simuladores,
  incluindo falhas de associação/persistência e Retry-After.
- [ ] Caso a resposta HTTP não tenha sido preservada antes da falha, reconciliar
  manualmente os IDs e custos incertos, sem reenviar `task_post`.

## P04 — Smart Business Finder

- [x] Implementar busca backend sem seleção automática e sem chamadas pagas
  durante testes.
- [x] Classificar localização, homônimos, filiais, divergência, dados
  insuficientes, limite e falhas técnicas.
- [x] Exigir confirmação explícita antes da autorização de diagnóstico; deixar
  a interface visual para P06.
- [x] Persistir rate/budget limits, request IDs, uso único de confirmação e
  proveniência sem armazenar consultas/candidatos brutos.
- [x] Bloquear o scanner para Google Places e exigir aceite ODbL/atribuição para
  permitir armazenamento de dados OSM.
- [x] Testar providers simulados, autenticação/transporte, respostas inválidas,
  ambiguidades, rate limit, confirmação, expiração e idempotência.
- [ ] P04.1: revisão formal de licenças, retenção, atribuição e integração
  geográfica antes de qualquer ativação/publicação.
- [x] Executar regressão Python completa após as mudanças P04 e registrar o
  resultado final.

## Homologação integrada — executada em 2026-11-25

- [x] Confirmar causa dos `HTTP 502`: timeout fixo de 8 s, menor que a
  confirmação de persistência no webhook n8n.
- [x] Configurar timeout do backend, retornar `503` retryable em timeout/falha
  de rede e orientar retry com o mesmo `request_id`.
- [x] Confirmar retry idempotente e ausência de linhas duplicadas.
- [x] Executar lotes progressivos de 50/100/500 com concorrência 10/25/50;
  registrar HTTP, latência, persistência, processamento, CRM, custos,
  duplicidades e CPU/memória.
- [x] Reiniciar n8n e Smart Grid com fila e IDs simulados pendentes; confirmar
  que polling retomou e IDs existentes não foram reenviados.
- [x] Confirmar unicidade dos 5.355 IDs simulados; custo simulado/real zero.
- [x] Repetir a suíte completa: `206 passed`, 0 falhas, 1 aviso de depreciação.
- [x] Atualizar relatório, roadmap, progresso e handoff.

Resultado final: 651 requests únicos (650 dos lotes e 1 `No Search Results`),
595 `done`, 34 `manual_review` e 22 `submission_uncertain`. Todos os envios
HTTP foram `202` na configuração final; não houve `502`/`503`. Os casos
incertos requerem reconciliação manual, sem reenvio automático. A causa
específica dos 34 casos de revisão manual não foi isolada.

## Integração e prontidão — dependem de aprovação/insumos

- [ ] Obter documentação oficial da conta T2S, autenticação, endpoints,
  identificadores de funil/etapa/etiquetas, campos personalizados, idempotência
  e limites. Não inventar contratos.
- [ ] Homologar retenção, backup/restauração, monitoramento e sizing no ambiente
  real antes de qualquer proposta de publicação.
- [ ] Fazer revisão de segurança final e apresentar checklist; publicação
  depende de autorização explícita.

## Futuro, não autorizado nesta sessão

- [ ] Definir escopo e métricas do Smart Leads Intelligence em proposta
  independente.
- [ ] Implementar Smart Leads Intelligence apenas após requisitos, dados,
  privacidade, orçamento e autorização do usuário.
- [ ] Entrega T2S/WhatsApp/e-mail real somente após contrato oficial e aprovação.

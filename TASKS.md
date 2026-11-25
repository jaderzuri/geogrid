# Smart Grid by ZURI — Próximas tarefas

## Checkpoint P01 e P02

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
- [ ] Implementar e homologar P03: recuperação e retentativas seguras, sem
  repetir submissões incertas.
- [ ] Caso a resposta HTTP não tenha sido preservada antes da falha, reconciliar
  manualmente os IDs e custos incertos, sem reenviar `task_post`.

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

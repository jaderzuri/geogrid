# Smart Grid by ZURI — Próximas tarefas

## Retomada — não iniciar até nova sessão

- [ ] Investigar e corrigir a resposta `HTTP 502` quando o webhook persiste o
  pedido, mas a Landing Page não recebe confirmação.
- [ ] Adicionar teste que reproduza persistência + timeout/erro de resposta e
  confirme retry idempotente com o mesmo `request_id`.
- [ ] Reexecutar a carga integrada gradualmente: 50, 100 e 500 solicitações;
  registrar recebidas, respostas HTTP, persistidas, concluídas, filas,
  percentis, CPU/memória, custo simulado e duplicidades por lote.
- [ ] Executar teste de restart end-to-end interrompendo tarefa com IDs
  DataForSEO simulados já persistidos; confirmar polling recuperado sem novo
  `task_post`.
- [ ] Reconciliar os 7 casos `submission_uncertain` do ensaio (somente evidência
  simulada; nunca reenviar automaticamente).
- [ ] Repetir a suíte completa de 204 testes depois das correções.

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

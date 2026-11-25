# Changelog

Alterações relevantes do Smart Grid by ZURI.

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

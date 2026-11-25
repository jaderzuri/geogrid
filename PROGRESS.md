# Smart Grid by ZURI — Progresso

Última atualização: 2026-11-25.

## Checkpoint P01 — aguardando autorização

- Branch `feature/dataforseo-standard-normal`, HEAD `38adc28`. O checkpoint
  proposto ainda não foi staged nem commitado.
- Migration 006: testes repetidos em PostgreSQL isolado com o arquivo final,
  hash SHA-256 `44b2b67135073c49aac9323ecc3e9c1d623c7e5133e230d7ad990edd6478098d`
  antes e depois. Passaram autorização única, concorrência, reservas legadas e
  novas, virada UTC, custos desconhecidos, IDs tardios e reconciliação.
- Reinício n8n anterior com simuladores: 30 `done`, 30 POSTs simulados, 270
  IDs únicos e nenhuma reserva remanescente. A rodada precedeu apenas a
  validação extra de precisão decimal; a migration final passou no PostgreSQL.
- Regressão repetida: 206 aprovados, um aviso conhecido de depreciação.
- Se uma resposta HTTP não puder ser persistida nem recuperada, os IDs e custos
  exigem reconciliação manual, sem novo `task_post`. Não se comprovou chamada
  única em toda falha distribuída; esses resultados não autorizam produção.
- O volume isolado `geogrid-p01-20261125-checkpoint-postgres-state` foi
  preservado e seu PostgreSQL parado. Os volumes anteriores não foram alterados.
- Próximo passo: aguardar autorização do checkpoint; só então iniciar P02.

## Estado resumido

- Branch: `feature/dataforseo-standard-normal`.
- Evolução estimada do projeto: **89%**.
- Homologação integrada estimada: **100% dos critérios definidos para esta
  etapa**; isso não significa prontidão de produção.
- Testes automatizados: **206 aprovados, 0 reprovados**; um aviso de
  depreciação do `httpx` usado pelo `TestClient`.
- O checkpoint anterior registrava 204 testes aprovados; foram adicionados
  dois testes focados para timeout/configuração, e a suíte atual tem 206.
- Nenhum commit, push, merge, deploy, chamada DataForSEO real ou alteração de
  produção foi realizado.

Os percentuais e os critérios estão detalhados em `ROADMAP.md`. São estimativas
de marcos; não medem cobertura de código nem garantem prontidão para produção.
O projeto mantém a ponderação anterior (80/100) e recebe 9 pontos pelos marcos
de homologação agora comprovados. A homologação integrada atingiu 100/100 nos
critérios de teste definidos: recepção, cargas 50/100/500, restart, segurança
e regressão. Os casos de revisão manual e submissão incerta continuam como
riscos operacionais explícitos.

## Funcionalidades implementadas

- Smart Grid Live preservado, inclusive parâmetros de grade, histórico,
  comparações, indicadores, PDF e ranking de concorrentes.
- DataForSEO Live com idioma configurável, tratamento diferenciado dos estados
  de ponto e cobertura amostral.
- API externa autenticada/idempotente; geração de nove coordenadas 3×3 a 1 km.
- Fila PostgreSQL, workflows n8n Standard Normal, priority 1, polling por IDs
  persistidos e bloqueio de reenvio automático em submissão incerta.
- Landing Page, status e relatório individual protegidos; outbox e adaptador CRM
  preparados, com T2S apenas simulado.
- Compose de homologação isolado com os workflows reais e simuladores HTTP.

## Evidência da carga integrada

- Causa confirmada dos `502`: timeout fixo de 8 segundos no proxy da Landing
  Page, enquanto o webhook só confirma após persistir no PostgreSQL. O pedido
  podia ser persistido mesmo que o cliente expirasse. Timeout agora é
  configurável; timeout/falha de rede devolve `503` retryable e orienta repetir
  com o mesmo `request_id`.
- A configuração inicial corrigida (n8n concorrência 20, timeout 30 s) ainda
  teve respostas retryable em carga alta. A configuração isolada final usou
  n8n `N8N_CONCURRENCY_PRODUCTION_LIMIT=50` e timeout 60 s.
- Lotes progressivos: 50/concorrência 10, 100/25, 500/50. Todos os 650
  pedidos de carga e o caso `No Search Results` receberam `202`; três retries
  idempotentes explícitos foram reconhecidos como duplicados. Total: 654
  respostas `202`, zero `502`/`503`, 651 requests únicos e zero linhas
  duplicadas no PostgreSQL.
- Latência de recepção (média / p95 / máximo): 50 — 1,86 / 2,52 / 2,59 s;
  100 — 3,70 / 4,41 / 4,59 s; 500 — 9,30 / 12,86 / 13,84 s. Processamento:
  45,57 s, 90,97 s e 435,69 s, respectivamente.
- Estados: lote 50 — 50 `done`; lote 100 — 98 `done`, 1 `manual_review`,
  1 `submission_uncertain`; lote 500 — 446 `done`, 33 `manual_review`,
  21 `submission_uncertain`. Incluindo o caso especial (nove pontos
  `no_results`, código `40102`), total: **595 `done`, 34 `manual_review` e
  22 `submission_uncertain`**.
- O mock DataForSEO registrou 595 chamadas `task_post` e 5.355 IDs persistidos
  únicos; o custo simulado/real foi zero. 34 pedidos não foram submetidos por
  necessidade de revisão; 22 permanecem para reconciliação e não podem ser
  reenviados automaticamente. A mensagem de revisão manual foi genérica e a
  causa específica não foi isolada.
- O CRM simulado recebeu 3.760 eventos; o relatório protegido, isolamento de
  tenant, token inválido, nove pontos e `No Search Results` foram validados.
- Restart em rodada isolada com fila pendente: n8n e Smart Grid voltaram a
  ficar saudáveis; polling avançou de 2.874 para 3.183 consultas e pedidos
  `done` de 299 para 334. IDs persistidos permaneceram únicos; submissões em
  voo viraram `submission_uncertain`, sem reenvio automático.
- Pico amostrado na rodada final: n8n 173,95% de CPU e 1.767 MiB de memória.
  Serve como evidência local, não como sizing do Easypanel. A rede era interna,
  com simuladores externos.

## Segurança e preservação — rodada anterior

- As stacks Compose estão paradas; volumes de todas as rodadas foram
  preservados, sem `down -v` ou remoção.
- Evidências de recursos permanecem em `/tmp/geogrid-homolog-fixed-resource-samples.csv`,
  `/tmp/geogrid-homolog-verified-resource-samples.csv` e
  `/tmp/geogrid-homolog-verified2-resource-samples.csv`; contêm apenas
  amostras de CPU/memória.
- Não exportar bancos, dados sintéticos ou capabilities para o GitHub.
- A varredura de credenciais não encontrou chaves reais nos arquivos revisados.
  Há somente placeholders e identidades sintéticas de teste/documentação.
- `geogrid.db` está ignorado pelo Git e não foi alterado. Backups `.bak`
  permanecem no worktree e não fazem parte do checkpoint proposto.

## Inventário do worktree da rodada anterior

Modificados nesta retomada: `app/landing.py`, `docs/n8n-setup.md`,
`tests/integration/compose.yaml`, `tests/integration/run_homologation.mjs`,
`tests/test_landing.py`, `ROADMAP.md`, `PROGRESS.md`, `TASKS.md`,
`CHANGELOG.md`, `SESSION_HANDOFF.md` e `.github/copilot-instructions.md`.

Mantidos fora de qualquer proposta de commit: três backups `.bak`,
`instalar_ida_adaptativo.py`, banco local ignorado, volumes Compose e arquivos
temporários de amostragem. Nenhum commit/push foi feito.

# Smart Grid by ZURI — Progresso

Última atualização: 2026-11-25.

## Sprint de lançamento comercial — implementação local em validação

- Captura de concorrentes implementada sobre resultados de scans Express
  concluídos, com deduplicação, observações e qualificação; sem chamadas
  adicionais. O gate separado `ZURI_PROSPECTING_DATA_USE_APPROVED` controla
  armazenamento/exportação de candidatos reais; simulações não são exportáveis.
- Painel administrativo com sessão de operador, filtros e CSV; exportação de
  leads exige consentimento de marketing explícito.
- Express real encaminha ao webhook n8n existente apenas quando os gates estão
  aprovados. Profissional/Monitor no caminho real ficam em atendimento
  assistido sem checkout ou processamento automático. O caminho pago continua
  desativado por padrão; não houve chamada paga, deploy ou mudança de produção.
- O lead envia também a pesquisa original associada ao token, evitando perder
  a verificação de confirmação quando o candidato tem nome canônico diferente.
- Testes focados: **42 aprovados**; regressão Python completa:
  **253 aprovados**, um aviso conhecido Starlette/httpx. `node --check` nos
  dois scripts admin/comerciais e `git diff --check` passaram. Pylance não
  reportou diagnósticos nos arquivos verificados (`commercial_api.py`,
  `commercial_store.py`, `commercial.py` e testes) e confirmou a
  compatibilidade do call site de `commercial_store.create_order`.
- A revisão final confirmou somente código, testes e documentação comerciais
  no checkpoint; os backups `.bak` e `instalar_ida_adaptativo.py` ficam fora.
  Checkpoint local criado como `a95e079`; sem push, merge ou deploy. Nenhuma
  consulta paga ou alteração de produção ocorreu.

## Jornada comercial simulada — implementação concluída localmente

- Implementada a jornada nativa de homologação com busca sintética,
  confirmação explícita, lead, Express 3×3, relatório protegido, oferta de
  upgrade, pedido Professional, confirmação manual auditada e liberação 7×7
  por operador. Monitor permanece somente assistido, sem recorrência.
- Adicionadas persistência SQLite aditiva, limites configuráveis de diagnósticos
  e pontos, controle de capacidade e rotas WordPress autenticadas por bearer
  servidor-a-servidor. O contrato está em
  `docs/wordpress-commercial-api.md`.
- A interface Live anterior continua disponível em `/landing-legacy`. O
  simulador usa exclusivamente candidatos e resultados fictícios; o caminho
  real Express permanece separado e condicionado aos gates de licenciamento.
- Corrigida a gravação de pontos pendentes para evitar duplicação quando o
  Business Finder prepara a grade antes do worker. O worker também não
  reenvia scans finalizados ou parciais. O teste direcionado confirma que um
  scan parcial é encerrado como falha, sem nova execução.
- Testes focados da jornada, Business Finder e Landing: **62 aprovados**.
  Teste específico do worker/reexecução: **1 aprovado**; arquivo comercial
  completo após a adição: **5 aprovados**. Regressão Python completa:
  **249 aprovados**, um aviso conhecido de depreciação Starlette/httpx.
  `node --check app/static/commercial.js` e `git diff --check` passaram.
- A validação completa não incluiu homologação PostgreSQL/n8n para a nova
  jornada comercial, publicação, teste de WordPress externo nem chamadas reais.
  Não houve alteração de produção, commit, push, merge, checkout real ou
  consulta paga.
- Estado: candidata funcional apenas para ambiente simulado. Antes de ativar,
  resolver direitos upstream/MapTiler, configurar segredos no backend, validar
  operação e limites do ambiente, homologar o workflow 7×7 se necessário e
  obter autorização explícita de publicação.

## Sprint MVP comercial — retomada e resultado parcial

- Foram consultados novamente os termos oficiais. A FAQ DataForSEO permite
  analytics/relatórios comerciais sob a condição da seção 7.1, mas diz que não
  avalia casos particulares e não concede licenças de provedores upstream.
  Assim, o uso de ranking Google Maps no Smart Grid não foi tratado como
  autorizado. Os termos MapTiler limitam o plano gratuito a uso não comercial;
  o plano da conta ZURI não foi verificado.
- `app/commercial.py` agora centraliza os produtos experimentais e exige quatro
  flags explícitas para liberar o encaminhamento de diagnósticos da Landing.
  `app/landing.py` falha com HTTP 503 antes de chamar n8n se faltar qualquer
  aprovação; `/api/landing/config` informa blockers e produtos. Preços:
  Express R$ 0, Professional R$ 29,90 e Monitor R$ 97/mês, editáveis pelas
  variáveis `ZURI_PRICE_EXPRESS_BRL`, `ZURI_PRICE_PROFESSIONAL_BRL` e
  `ZURI_PRICE_MONITOR_BRL`.
- Todos os produtos retornam `available=false` e `checkout_url=null`. Ainda
  faltam busca/confirmação integrada à Landing, ordem comercial, links DOM e
  liberação manual de pagamentos. O fluxo n8n de produção continua com grid
  fixo 3×3; o Professional 7×7 não pode ser vendido como funcionalidade pronta.
- DataForSEO Standard: estimativa da tarifa pública é US$ 0,0006 por página
  SERP; 9 pontos = US$ 0,0054 e 49 pontos = US$ 0,0294 por palavra-chave,
  antes de resolução da empresa, câmbio, tributos e operação. Não é custo real
  verificado da conta.
- Regressão Python completa: 244 aprovados, 0 falhas e 1 aviso conhecido
  Starlette/httpx. Os testes Landing cobrem as quatro flags, bloqueio anterior
  à chamada n8n, catálogo, configuração inválida e precisão de centavos.
  Homologação comercial PostgreSQL/n8n ponta a ponta não foi executada porque
  a oferta/checkout permanece bloqueada; não houve chamada paga, alteração de
  banco, commit, push ou deploy.
- A candidata comercial fica bloqueada. O modo Live permanece inalterado.

## P02 — concluído

- P01 foi commitado e enviado sob autorização: `6fc53a142cbe0cb0edceed83a097e96853d8f2da`.
  P02 foi commitado localmente como `4ccc300`, sem push.
- Migration 007 e workflow 02 registram categorias e metadados sanitizados,
  distinguem a fase financeira e preservam as proteções do P01. Erros de claim
  interrompem a execução quando não é possível registrá-los no PostgreSQL.
- Classificador: 24 falhas induzidas. PostgreSQL isolado: 10 falhas anteriores
  à submissão, submissão incerta, resultados por ponto e orçamento; regressão
  P01 com 007 aplicada também passou. Suíte Python: 206 aprovados, um aviso.
- n8n com simuladores: identidade ambígua, divergência e caminho válido; nove
  erros de autenticação DataForSEO simulada mantiveram a reserva. Rodada
  isolada adicional injetou falha de claim e 17 cenários nos nós de processamento
  e callbacks; reserva prévia foi liberada e submissões incertas mantidas. Foram
  seis POSTs simulados esperados, sem repetição durante o período observado.
- Volumes de evidência preservados. Os percentuais históricos abaixo não
  incorporam novos critérios P02 e não representam prontidão de produção.

## P03 — concluído localmente

- Landing Page: retries limitados configuráveis para falhas de transporte,
  HTTP 429 e 5xx, backoff exponencial com jitter e `Retry-After`. Cada tentativa
  reutiliza URL, `request_id` e payload; o navegador preserva o mesmo payload
  durante a tentativa da sessão sem guardá-lo em `sessionStorage`.
- PostgreSQL isolado: passaram limites/backoff/Retry-After, falha permanente,
  claim pré-submissão expirado, journal da resposta, associação tardia de nove
  IDs e reserva mantida para custo desconhecido até reconciliação.
- n8n 1.123.21 com simuladores: claim e 17 falhas induzidas passaram. A resposta
  recebida antes de falhas de associação/persistência foi recuperada, com uma
  autorização e sem repetir `task_post`; `Retry-After: 6` foi propagado e
  respeitado pela agenda.
- Regressão Python: 211 aprovados, zero falhas, um aviso conhecido de
  depreciação do `httpx` no `TestClient`. Teste focado Landing Page: 25 passaram.
- Limitação: se a resposta HTTP paga se perder antes de entrar no journal, não
  há fonte segura para recuperar os IDs; manter `submission_uncertain`,
  reserva retida e reconciliação manual. Não reenviar `task_post`.
- Teste de reinicialização do n8n com simuladores já havia passado na rodada P03
  anterior; os testes atuais também cobriram a recuperação do journal após
  falhas induzidas, mas não repetiram o ciclo de restart.

## P04 — Smart Business Finder (implementado localmente)

- Checkpoint local: `f657b8d` (`feat: add Smart Business Finder backend`),
  sem push.
- Implementados os endpoints backend `POST /api/business-finder/search`,
  `/confirm` e `/diagnostics`, sem alterações na interface visual da Landing
  Page. A busca real requer provider/credenciais configurados; o limite diário
  padrão é zero (fail-closed).
- A busca classifica empresas encontradas, homônimos, divergências,
  insuficiência de dados e ausência de candidatos. Falhas de transporte,
  timeout, autenticação, rate limit e resposta inválida são separadas de
  `BUSINESS_NOT_FOUND`; resultados não escolhem automaticamente a empresa.
- A confirmação exige ação explícita e é de uso único/idempotente por
  `request_id`; confirmação apenas de identidade não concede direito de
  armazenamento. As tabelas SQLite são aditivas; limites e hashes persistem,
  mas os dados brutos da consulta/candidato não são gravados.
- Google Places é intencionalmente incompatível com o scanner: a política
  proíbe alimentar análise ponto-em-polígono com suas coordenadas. Place ID
  pode ser persistido; nomes/endereço/candidatos são transitórios. OSM exige
  flags explícitas de aceite ODbL e prontidão de atribuição, além de instância
  configurada própria para liberar o scanner. Nominatim público é bloqueado.
- Testes focados com providers simulados: **25 aprovados**, sem chamadas
  externas, com um aviso conhecido de depreciação Starlette/httpx.
  Regressão Python completa: **236 aprovados**, zero falhas, com o mesmo aviso
  conhecido. Pylance sem diagnósticos nos módulos de produção e testes
  alterados.
- P04.1 é bloqueio obrigatório antes de habilitar o fluxo ou publicar P06:
  revisão de provedor/termos, retenção, atribuição, quotas e compatibilidade
  geográfica. MapTiler ou outros provedores não foram contratados/ativados.

## Checkpoint P01 — registro anterior

- Registro histórico anterior ao checkpoint `6fc53a1`.
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
- O checkpoint P01 foi posteriormente autorizado e publicado.

## Estado resumido

- Branch: `feature/dataforseo-standard-normal`.
- Evolução estimada do projeto: **89%**.
- Homologação integrada estimada: **100% dos critérios definidos para esta
  etapa**; isso não significa prontidão de produção.
- Testes automatizados: **206 aprovados, 0 reprovados**; um aviso de
  depreciação do `httpx` usado pelo `TestClient`.
- O checkpoint anterior registrava 204 testes aprovados; foram adicionados
  dois testes focados para timeout/configuração, e a suíte atual tem 206.
- Nesta etapa P02 não houve novo commit, push, merge, deploy, chamada
  DataForSEO real ou alteração de produção.

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

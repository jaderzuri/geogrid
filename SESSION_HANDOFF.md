# Session Handoff — Smart Grid by ZURI

Data: 2025-01-16

## FASE A — Concluída, Validada e Documentada ✅

**Status:** PRONTO PARA REVIEW E PRODUÇÃO  
**Branch:** `feature/dataforseo-standard-normal`  
**HEAD:** `c830e21` (docs: Conclusão final da FASE A — resumo executivo)  
**Base:** upstream `origin/main` (+5 commits FASE A, +17 histórico)  
**Testes:** 280 passando (↑ 27 novos)  
**Worktree:** Limpo ✅

### Implementações Entregues:

1. **P0.1 — Captura de Dados de Contato**
   - Arquivos: `app/fetchers/dataforseo.py`, `tests/test_dataforseo.py`
   - Novas colunas: `phone`, `url`, `position` em `points.results` (JSON)
   - Sem schema migration; compatibilidade retroativa 100%
   - Teste: `test_additional_contact_fields_are_captured_when_available()`

2. **P0.2 — Interpretação de Resultados**
   - Arquivo novo: `app/interpretation.py` (172 linhas)
   - Funções: `generate_coverage_interpretation()`, `generate_positioning_interpretation()`, 
     `generate_competition_interpretation()`, `generate_full_interpretation()`
   - Integrado a: `/api/scans/{scan_id}/analysis` (vê `app/main.py:614`)
   - Testes: 15 casos em `tests/test_interpretation.py`

3. **P0.3 — Correção do PDF**
   - Arquivo novo: `app/static/print.css` (350+ linhas)
   - Arquivo novo: `app/pdf_utils.py` (231 linhas com Playwright)
   - Integrado a: `app/static/index.html`, `app/static/diagnostic.html`
   - PDFs validados: `tests/pdfs/test_grid_3x3.pdf`, `test_grid_7x7.pdf`, `test_grid_13x13.pdf`
   - Testes: 12 casos em `tests/test_pdf_generation.py`

### Status de Testes:

- **280 testes passando** (before: 253, +27 novos)
- 1 aviso conhecido: Starlette/httpx deprecation (não bloqueante)
- Sem chamadas pagas ao DataForSEO
- Sem mensagens reais enviadas
- Sem credenciais expostas

### Arquivos Modificados:
```
M  app/fetchers/dataforseo.py         (+3 linhas: phone, url, position)
M  app/main.py                        (+8 linhas: import interpretation, integrate)
M  app/static/index.html              (+1 linha: link print.css)
M  app/static/diagnostic.html         (+1 linha: link print.css)
M  tests/test_dataforseo.py           (+1 teste novo)

A  app/interpretation.py              (172 linhas)
A  app/pdf_utils.py                   (231 linhas)
A  app/static/print.css               (350+ linhas)
A  tests/test_interpretation.py       (297 linhas, 15 testes)
A  tests/test_pdf_generation.py       (297 linhas, 12 testes)
A  tests/pdfs/test_grid_*.pdf         (3 arquivos, validados)
```

### Próximas Etapas:

FASE B (MVP Comercial):
- [ ] Investigar recuperação histórica de contatos
- [ ] Implementar interface de oportunidades comerciais
- [ ] Implementar classificação de telefones (E.164 brasileiro)
- [ ] Utilitário dry-run de enriquecimento

FASE C (Integrações):
- [ ] Preparar integração CRM T2S
- [ ] Preparar fila comercial SMS/RCS
- [ ] Gerar workflows n8n válidos

FASE D (Futuro):
- [ ] ZURI Smart Report (personalização)
- [ ] ZURI Keyword Intelligence

### Validação Recomendada:

```bash
# Executar suite completa
python3 -m pytest tests/ -q

# Validar PDFs gerados
ls -la tests/pdfs/

# Verificar interpretações integradas
curl http://localhost:8000/api/scans/{scan_id}/analysis

# Testar geração de PDF com Playwright
python3 app/pdf_utils.py
```

### Segurança e Conformidade:

✅ Nenhuma chamada paga ao DataForSEO (mocks/fixtures apenas)
✅ Nenhuma mensagem real enviada
✅ Nenhuma credencial exposta
✅ Sem modificações em produção
✅ Sem push, merge, deploy
✅ Dados históricos preservados (backward compatible)
✅ Schema não alterado (JSON flexível)

---

## Homologação Express isolada — resultado consolidado

- Retomada da branch `feature/dataforseo-standard-normal`, HEAD anterior `e9fb195`.
  Alterações não rastreadas anteriores foram preservadas: três backups `.bak`
  e `instalar_ida_adaptativo.py`. A homologação também mantém seu override local
  não rastreado; esses arquivos não fazem parte do checkpoint.
- A stack temporária usou PostgreSQL, n8n 1.123.21, Smart Grid e mocks somente
  em rede Docker interna. Busca ambígua, confirmação explícita, lead, Express,
  fila, relatório protegido, painel/CSV e Professional assistido passaram.
  Cobertura observada: 9/9 pontos. O caminho sem resultados aceitou lead
  assistido e não criou diagnóstico.
- Repetição idempotente não duplicou a submissão: PostgreSQL registrou 1
  solicitação, 1 chave idempotente e 9 tarefas únicas/concluídas. Mock metrics:
  1 `task_post`, 9 `task_get`, 3 chamadas Places e 7 eventos para o mock CRM.
  Custo persistido: US$ 0.00. Nenhuma mensagem, consulta paga real ou chamada
  externa ocorreu; eventos CRM não saíram do simulador.
- O teste CSV do fluxo Standard Normal incluiu um registro comercial fictício
  retornado pelo mock DataForSEO para exercitar o painel. Não compartilhar o
  CSV de homologação. O teste específico do modo explicitamente `synthetic`
  continua assegurando que registros sintéticos não são elegíveis a exportação;
  leads sem marketing consentido não foram exportados.
- `docs/easypanel-commercial-readiness.md` reúne checklist, variáveis, gates,
  limites, estimativa de custo, bloqueios e procedimentos. Corrigida também a
  lista de migrações em `docs/n8n-setup.md` para incluir 008.
- Validação final: regressão completa **253 passed**, com o aviso conhecido
  Starlette/httpx; Compose isolado passou `config -q`, mock Python sem erros de
  sintaxe e `git diff --check` sem erros.
- A stack foi parada com `docker compose stop`; os volumes exclusivos
  `geogrid-commercial-homolog-{postgres-state,smart-grid-state,n8n-state,mock-certs}-20261125`
  permanecem preservados. Não foi usado `down -v` nem houve remoção de volume.
- O checkpoint local desta rodada contém somente `SESSION_HANDOFF.md`,
  `docs/n8n-setup.md`, `docs/easypanel-commercial-readiness.md` e
  `tests/integration/mock_services.py`. O override local, três backups `.bak`
  e `instalar_ida_adaptativo.py` foram preservados fora do checkpoint. Sem
  deploy, publicação, push, merge ou ativação real.
- Estimativa não vinculante: US$ 0.0054 de tarifa SERP para nove tarefas a
  US$ 0.0006/página; Places, taxas e variações não estão incluídos. Não é teto
  de custo. Não fazer consulta paga até receber autorização explícita e
  reconfirmar orçamento/limites.

## Sprint de lançamento — checkpoint local concluído

- Branch `feature/dataforseo-standard-normal`; checkpoint local
  `a95e079` (`feat: add gated Express and prospect capture`), baseado em
  `6aa2101`. Nenhum push, merge ou deploy. O worktree está limpo para arquivos
  rastreados; três backups `.bak` e `instalar_ida_adaptativo.py` permanecem
  não rastreados, preservados e fora do checkpoint.
- Implementada captura idempotente de concorrentes a partir dos pontos já
  armazenados em scans Express concluídos. Prospects se deduplicam por CID,
  Place ID, nome/endereço ou nome/localização; novas observações atualizam
  primeiro/último avistamento, ranking e qualificação. Não há enriquecimento.
  Dados sintéticos não são exportáveis; uso de prospects DataForSEO exige
  `ZURI_PROSPECTING_DATA_USE_APPROVED`.
- Painel admin autenticado com cookie HttpOnly/SameSite Strict, Secure por
  padrão, expiração de 30 minutos, filtros e CSV. Exportação de leads exige
  marketing consentido; prospectos exigem gate de proveniência.
- O Express real só encaminha ao webhook n8n existente após gates de licença,
  pipeline e armazenamento de coordenadas, com mesma chave idempotente. A
  associação de scan e captura ocorre após conclusão; não houve chamada paga.
  Professional/Monitor aceitam pedido assistido sem checkout, confirmação
  financeira ou processamento automático no modo real.
- `business_search` acompanha o cadastro do lead e é validado contra
  `search_request_id` e token de confirmação, preservando a pesquisa original
  quando o nome canônico do candidato escolhido é diferente.
- Validação desta retomada: testes focados `tests/test_commercial_api.py`
  + `tests/test_landing.py`: **42 passed**; regressão Python completa:
  **253 passed**, com o aviso conhecido Starlette/httpx. `node --check` nos
  scripts comerciais/admin, `git diff --check`, diagnósticos Pylance sem erros
  nos quatro arquivos analisados e compatibilidade do call site
  `create_order` passaram. Os 249 testes do checkpoint anterior precedem estas
  alterações.
- A revisão final e regressão estão concluídas. O checkpoint local contém
  apenas código, testes e documentação revisados. Não houve consulta paga ou
  alteração em produção. Próximo passo: entregar o relatório e aguardar
  autorização para teste real e publicação.

## Jornada comercial simulada — estado antes da sprint de captura

- Branch `feature/dataforseo-standard-normal`; sem commit criado nesta etapa.
  As mudanças locais de código e documentação estão preservadas. Não houve
  push, merge, deploy, uso de banco/volume de produção nem chamada paga.
- A jornada de referência foi implementada em modo estritamente sintético:
  descoberta com candidatos/homônimos, confirmação explícita, lead,
  diagnóstico Express 3×3, relatório com cobertura, oferta Professional,
  pedido idempotente, confirmação manual autenticada/auditada e liberação
  Professional 7×7. Monitor permanece somente assistido.
- API WordPress servidor-a-servidor, exemplos de payload, autenticação,
  estados/erros, configuração e rollback: `docs/wordpress-commercial-api.md`.
  Bearer tokens permanecem no backend; capacidades do lead protegem status e
  relatórios. Checkout simulado não comprova pagamento.
- A Landing Page anterior foi preservada em `/landing-legacy`. O scanner Live
  não foi removido nem substituído. Nenhum fluxo comercial usa DataForSEO,
  Google Places, MapTiler ou checkout externo em modo simulado.
- Fixes relacionados: `db.save_point` atualiza pontos pendentes em vez de
  duplicar pontos da grade; worker não reexecuta scans finalizados e encerra
  scans parciais sem novo envio. Teste dedicado cobre ambas as proteções.
- Validação: testes focados comercial/Business Finder/Landing **62 passed**;
  após adicionar o teste do worker, `tests/test_commercial_api.py` **5 passed**;
  regressão completa **249 passed**, com o aviso conhecido de depreciação
  Starlette/httpx. `node --check app/static/commercial.js` e
  `git diff --check` passaram. Pylance não reportou erros em
  `commercial_api.py`, `commercial_store.py`, `scan.py` ou nos testes comerciais;
  os avisos `unused` restantes estão em código legado fora do escopo de correção.
- A homologação PostgreSQL/n8n do fluxo comercial, WordPress externo, licenças
  e uso comercial real não foi executada. Continuam bloqueados ativação real,
  links de pagamento de produção, chamadas pagas e publicação. Antes disso,
  confirmar direitos upstream/MapTiler, configuração de segredos, limites e
  sizing no ambiente alvo; homologar o workflow 7×7 caso venha a processá-lo.
- Próximo passo: aguardar autorização explícita de ativação/publicação. Não
  repetir auditorias anteriores nem alterar os gates. Para rollback da futura
  aplicação, desabilitar o simulador e reverter a versão sem apagar banco,
  tabelas, históricos ou volumes. Nenhuma operação de publicação foi feita.

## Sprint MVP comercial — retomada, estado parcial

- Branch `feature/dataforseo-standard-normal`; base local `6aa2101`. Worktree
  contém apenas alterações desta retomada nos arquivos listados abaixo e
  quatro artefatos não rastreados preexistentes (três backups `.bak` e
  `instalar_ida_adaptativo.py`), preservados sem edição.
- Revisados os termos oficiais:
  [FAQ legal DataForSEO](https://dataforseo.com/help-center/quick-answers-to-frequently-asked-legal-questions),
  [Termos DataForSEO](https://dataforseo.com/terms-of-service) e
  [Termos MapTiler Cloud](https://www.maptiler.com/terms/cloud/). DataForSEO
  permite analytics/relatórios comerciais sob §7.1, mas recusa confirmar
  aplicação a produto específico e não licencia direitos upstream. A
  compatibilidade do uso de ranking Google Maps não está confirmada. O plano
  MapTiler da conta ZURI também não foi comprovado; o plano gratuito é para uso
  não comercial/P&D.
- Para evitar consultas pagas não autorizadas, `app/commercial.py` adiciona
  quatro gates fail-closed. `POST /api/landing/leads` retorna `503` antes de
  chamar n8n se algum gate estiver ausente; `GET /api/landing/config` publica
  apenas os nomes dos bloqueios e o catálogo experimental. Dados padrão
  Express/Professional/Monitor são configuráveis com `ZURI_PRICE_*_BRL`.
  Produtos permanecem `available=false`, sem URL de checkout.
- Limitações confirmadas: Google Places é descoberta apenas e não pode prover
  coordenadas para a grade; a fila n8n segue 3×3 e começa requests `queued`;
  não há pedido de upgrade, fluxo 7×7, integração DOM ou mecanismo de
  confirmação/liberação manual. Assim, não apresentar o MVP como pronto para
  vendas. O modo Live não foi alterado.
- Testes específicos Landing: 33 aprovados. Regressão Python completa:
  `244 passed`, 1 aviso conhecido de depreciação Starlette/httpx.
  `app/commercial.py` sem diagnósticos Pylance. `app/landing.py` e
  `tests/test_landing.py` reportam somente avisos de variável não usada já
  presentes nos arquivos; nenhum erro de análise.
- Nenhum teste integrado PostgreSQL/n8n foi repetido: a alteração não muda
  schema nem workflow e o fluxo comercial permanece bloqueado. Nenhuma chamada
  paga, mudança de banco, deploy, commit ou push ocorreu. Homologação comercial
  ponta a ponta e rollback não foram executados porque ainda não existe fluxo
  de venda/liberação operacional.
- Arquivos alterados: `app/commercial.py`, `app/landing.py`,
  `tests/test_landing.py`, `docs/n8n-setup.md`, `ROADMAP.md`, `TASKS.md`,
  `PROGRESS.md`, `CHANGELOG.md` e este handoff. Próxima retomada: resolver
  direitos upstream/contrato e plano MapTiler; definir fonte licenciada de
  coordenadas e contrato de Professional 7×7/ordem manual antes de implementar
  links de pagamento. Não ativar gates por variável sem as evidências acima.

## P04 — implementação local e validação

- Branch `feature/dataforseo-standard-normal`; checkpoint P04 local
  `f657b8d` (`feat: add Smart Business Finder backend`), sem push.
  Alterações preexistentes e não relacionadas — três backups `.bak` e
  `instalar_ida_adaptativo.py` — foram preservadas e não entraram no checkpoint.
- Implementados `app/business_finder.py`, alterações aditivas em `app/db.py`,
  registro/políticas de resposta em `app/main.py` e testes
  `tests/test_business_finder.py`.
- Testes focados simulados: **25 aprovados**; regressão Python completa:
  **236 aprovados**, zero falhas, um aviso conhecido de depreciação
  Starlette/httpx. Pylance sem diagnósticos nos módulos de produção e testes
  alterados.
- Busca/provedores reais não foram chamados. Limite diário padrão zero.
  Google Places não pode alimentar coordenadas para análise ponto-em-polígono;
  os dados de busca permanecem transitórios, exceto Place ID. OSM só libera
  armazenamento com `GEOGRID_OSM_ODBL_STORAGE_ACCEPTED=true` e
  `GEOGRID_OSM_ATTRIBUTION_READY=true`; scanner exige instância configurada
  própria. O endpoint público Nominatim é bloqueado.
- P04.1 — compatibilidade de licença, retenção, atribuição, quotas e scanner —
  é obrigatório antes de habilitar ou publicar P06. Nenhum provedor comercial
  foi contratado/ativado.
- P04 foi revisado, a regressão foi repetida e o checkpoint foi criado.
  Próximo passo é aguardar autorização para P05. Não iniciar P05/P06 sem
  orientação; não houve push, merge, deploy, chamada paga, alteração de
  produção ou exclusão.

## P03 — concluído; P04 autorizado anteriormente

- Checkpoint P02 criado localmente: `4ccc300`; nenhum push realizado. Os 22
  arquivos foram revisados e não incluem bancos, backups ou credenciais reais.
- P03 implementado localmente; checkpoint autorizado após aprovação dos testes:
  `feat: implement safe recovery and retry policies`. Nenhum push.
- Landing Page usa retries limitados configuráveis para falhas de transporte,
  429 e 5xx, backoff exponencial com jitter e `Retry-After`, enviando a mesma
  URL e o mesmo payload (incluindo `request_id`). O navegador mantém o payload
  idêntico em memória durante retries da sessão, sem armazená-lo em storage.
- PostgreSQL 16 isolado: passou teste de tentativas/backoff/Retry-After,
  falha permanente, recuperação de claim expirado pré-submissão, journal,
  associação idempotente de nove IDs, retenção de reserva com custo desconhecido
  e reconciliação posterior.
- n8n 1.123.21 com simuladores: passaram falha de claim e 17 falhas induzidas.
  Falhas de associação e persistência recuperaram a resposta journalizada sem
  novo POST; `Retry-After: 6` foi preservado e respeitado pela agenda.
- Regressão Python final: 211 aprovados, zero falhas e um aviso conhecido do
  `TestClient`; testes focados Landing Page: 25 aprovados. O restart P03 foi
  aprovado em rodada isolada anterior e não foi repetido nesta rodada.
- Limite operacional: se a resposta paga se perder antes de ser journalizada,
  não há recuperação automática segura. Manter `submission_uncertain`, reserva
  retida e reconciliação manual; nunca reenviar automaticamente `task_post`.
- As stacks deste ciclo foram paradas sem remover volumes. Volumes criados
  durante as validações falhas e bem-sucedidas foram preservados.
- Próximo passo autorizado: implementar P04 Smart Business Finder apenas no
  backend, usando simuladores nos testes; não alterar UI da Landing Page. Não
  iniciar P05/P06 nem fazer push, merge, deploy ou chamadas pagas reais.

## P02 — validação e checkpoint local concluídos

- Checkpoint P01 `6fc53a142cbe0cb0edceed83a097e96853d8f2da` foi commitado e
  enviado à branch autorizada; o remoto foi confirmado com o mesmo hash.
- P02 foi commitado localmente em `4ccc300`, sem push. A migration 007 e o workflow
  02 registram falhas classificadas em `geogrid_execution_logs`, com mensagens
  fixas sanitizadas e fase anterior/posterior à autorização. As transições
  financeiras continuam usando as funções P01.
- Validação final: 24 falhas induzidas no classificador; PostgreSQL isolado
  cobriu 10 falhas pré-submissão, reserva incerta, resultados e orçamento.
  Os testes P01 passaram com 007 instalada. Regressão: 206 aprovados, um aviso.
- n8n 1.123.21 com simuladores: identidade ambígua e divergente
  foram classificadas, e uma solicitação válida teve nove IDs e um POST simulado.
  Em rodada anterior, autenticação simulada inválida gerou nove classificações
  `AUTHENTICATION_ERROR`, com reserva `held_unknown_cost` e uma autorização.
  Rodada adicional `geogrid-p02-faults-v5-20261125` passou falha de claim e
  17 falhas induzidas nos nós de processamento e callbacks, com seis POSTs
  simulados previstos, sem repetição durante a observação. O workflow importado
  era cópia instrumentada exclusivamente em homologação; funções e caminhos
  de erro reais foram exercitados. Volumes v1–v5 foram preservados.
- Se o PostgreSQL estiver indisponível, não poderá registrar sua própria falha;
  a execução de claim falha visivelmente e requer monitoramento externo. A
  validação de localização propriamente dita pertence ao P04 e não foi feita.
- Evidências preservadas, inclusive volumes
  `geogrid-p02-20261125-final-p01-postgres-state`,
  `geogrid-p02-20261125-final-p02-postgres-state` e os quatro volumes com
  prefixo `geogrid-p02-20261125-final-n8n`. Serviços P02 foram parados;
  o PostgreSQL da homologação anterior permanece intocado.
- Próximo passo: iniciar P04 Smart Business Finder backend, autorizado neste
  escopo. A interface visual permanece para P06; push continua não autorizado.

Para repetir os testes, escolher nomes de volume inexistentes e exclusivos:

```bash
node tests/integration/test_p02_classifier.mjs
P01_VOLUME_NAME=<volume-novo-p02> docker compose -p <projeto-novo-p02> -f tests/integration/compose.p01.yaml -f tests/integration/compose.p02.yaml run --rm test-runner
P01_VOLUME_NAME=<volume-novo-p01> docker compose -p <projeto-novo-p01> -f tests/integration/compose.p01.yaml -f tests/integration/compose.p02-p01.yaml run --rm test-runner
P02_VOLUME_PREFIX=<prefixo-novo-n8n> docker compose -p <projeto-novo-n8n> -f tests/integration/compose.yaml -f tests/integration/compose.p02-processor.yaml run --rm test-runner node /app/test_p02_n8n.mjs
.venv/bin/python -m pytest -q
```

Substituir os marcadores, verificar previamente conflitos de portas e preservar
os volumes. Para a rodada de falhas, acrescentar o arquivo
`tests/integration/compose.p02-faults.yaml`, construir o simulador e executar
`node /app/test_p02_n8n_faults.mjs` no runner.

## Estado atual — desbloqueio P01

Este bloco registra o estado anterior ao checkpoint P01.
Branch `feature/dataforseo-standard-normal`, HEAD `38adc28`; há alterações
locais não commitadas nem staged. O checkpoint P01 aguarda autorização para
commit e push; P02 só deve começar após sua conclusão. P02 e os pacotes
seguintes não foram iniciados nesta rodada. Não aplicar a migração em produção
nem publicar sem autorização.

### Correções e limites

- `workflows/n8n/sql/006-paid-submission-authorization-and-budget.sql` mantém
  reservas legadas sem origem diária comprovada em `legacy_unreconciled`, sem
  deduzir a data do claim. `geogrid_reconcile_legacy_reservation` exige escopo
  e referência de evidência informados por operador; essa evidência deve provar
  a origem e, antes de liberar, que não houve submissão paga. Referências não
  devem conter dados pessoais. Restringir o acesso às funções SQL privilegiadas.
- Reservas novas registram a data UTC original. Claims pré-submissão expirados
  liberam os dois escopos uma só vez; submissões incertas mantêm a reserva e
  não autorizam novo `task_post`.
- IDs recebidos são persistidos mesmo com custo desconhecido; zero conhecido é
  distinto de custo desconhecido. `geogrid_reconcile_post_cost` contabiliza o
  custo e libera a reserva mediante evidência, de forma idempotente. Dados
  conflitantes são rejeitados. Uma resposta tardia pode ser associada pelo
  token original da autorização, sem novo POST.
- Limitação: a resposta do HTTP ainda não tem armazenamento durável separado
  antes da gravação dos IDs. Se o processo cair antes da persistência e a
  resposta não tiver sido preservada, não há recuperação automática dos IDs;
  manter `submission_uncertain` e fazer reconciliação manual, sem reenviar.
  Também não se comprovou garantia universal de exatamente uma chamada na
  fronteira entre banco e serviço externo em todas as falhas possíveis.
- Funções e migração foram testadas em dados sintéticos; o procedimento para
  reconciliar reservas antigas reais exige evidências independentes e revisão
  operacional antes de qualquer uso. A aplicação da migração exige processadores
  parados e backup validado; não foi aplicada a bancos anteriores.

### Evidências P01

- Teste SQL final: PostgreSQL 16 em
  `geogrid-p01-20261125-unlock-v5-postgres-state`. Passaram 20 claims
  concorrentes distintos, uma autorização entre 20 tentativas concorrentes,
  bloqueio de claim expirado e token divergente, recuperação após virada UTC,
  reservas legadas pré e pós-submissão, reconciliação financeira concorrente,
  custo zero e desconhecido, IDs tardios e reaplicação da migração. A primeira
  tentativa falhou por ambiguidade no teste; foi corrigida sem alterar volumes.
- Na preparação do checkpoint, o teste PostgreSQL foi repetido com a migration
  final; SHA-256 antes e depois:
  `44b2b67135073c49aac9323ecc3e9c1d623c7e5133e230d7ad990edd6478098d`.
  O volume novo `geogrid-p01-20261125-checkpoint-postgres-state` foi preservado
  e seu contêiner parado. A regressão foi repetida: `206 passed`, um aviso.
- Reinício real do n8n 1.123.21 em stack isolada com simuladores: 5 pedidos
  iniciais e 25 adicionais aceitos. Ao parar o n8n havia 18 `queued`, 3 `claimed`,
  1 `scan_created`, 3 `submitted` e 5 `done`. Os quatro claims pré-submissão
  foram expirados artificialmente com o n8n parado; após o reinício, a
  recuperação liberou suas reservas. Os leases de polling expiraram naturalmente.
  Resultado: 30 `done`, 30 `task_post` simulados, 270 IDs e tags distintos,
  30 autorizações, custo e reservas pendentes de US$ 0 no simulador.
  Essa rodada antecedeu apenas a validação adicional de precisão decimal do SQL;
  a versão final passou no teste PostgreSQL v5.
- Regressão: `206 passed`, um aviso conhecido de depreciação do `httpx`.
  Sem chamadas pagas reais, credenciais reais, commit, push ou deploy.
- Foram parados apenas os serviços criados para P01. Volumes `unlock`,
  `unlock-v2`, `unlock-v3`, `unlock-v4`, `unlock-v5` e os quatro volumes
  `geogrid-p01-20261125-processor-*` foram preservados. O PostgreSQL anterior
  `geogrid-homolog-20261125-verified2-postgres-1` permaneceu ativo e intacto.

### Arquivos locais do P01

- Modificados: `ROADMAP.md`, `TASKS.md`, `PROGRESS.md`, `CHANGELOG.md`,
  `workflows/n8n/02-processador-standard.json`,
  `tests/integration/compose.yaml`, `tests/integration/Dockerfile.test-runner`
  e `SESSION_HANDOFF.md`.
- Novos: `workflows/n8n/sql/006-paid-submission-authorization-and-budget.sql`,
  `tests/integration/compose.p01.yaml`,
  `tests/integration/compose.p01-processor.yaml`,
  `tests/integration/test_p01_postgres.mjs` e
  `tests/integration/test_p01_restart_submit.mjs`.
- Backups preexistentes e `instalar_ida_adaptativo.py` seguem fora do escopo.

Para repetir o teste PostgreSQL, escolher **outro nome de volume inexistente**:

```sh
P01_VOLUME_NAME=geogrid-p01-NOVO-postgres-state docker compose \
  -p geogrid-p01-NOVO -f tests/integration/compose.p01.yaml \
  run --rm test-runner
P01_VOLUME_NAME=geogrid-p01-NOVO-postgres-state docker compose \
  -p geogrid-p01-NOVO -f tests/integration/compose.p01.yaml stop
```

O teste exige banco vazio e a imagem local `integration-test-runner:latest`.
Não remover volumes. Aguardar autorização do commit/push; após concluir o
checkpoint, implementar somente P02, preservando as proteções P01.

## Não fazer na retomada sem confirmação

Não iniciar Smart Leads Intelligence nem novas funcionalidades. A homologação
local da correção dos `502` e do restart foi concluída; os próximos passos são
investigar os casos de revisão manual, reconciliar submissões incertas e revisar
o sizing. Não fazer commit, push, merge, deploy, chamadas pagas ou operações de
produção sem autorização.

## Estado Git da rodada anterior

- Branch atual: `feature/dataforseo-standard-normal`.
- HEAD continua no checkpoint `0c22286f72ae04a84195a3e2634ef73c3f0776b3`;
  há alterações locais não commitadas.
- Nenhum commit, push, merge ou deploy foi feito nesta retomada.
- `git diff --check` passou antes das atualizações documentais; repetir ao
  finalizar.
- Não incluir backups, banco, secrets, dados de contato ou volumes em qualquer
  checkpoint futuro sem revisão e autorização.

## Resultado técnico para retomar

- Causa confirmada dos `502`: timeout fixo de 8 s no proxy da Landing Page,
  menor que a confirmação de persistência do webhook.
- O timeout é configurável (`ZURI_N8N_TIMEOUT_SECONDS`, padrão 30 s, faixa
  1–120 s); timeout/rede retorna `503` retryable e requer o mesmo
  `request_id` e payload em qualquer retry.
- Carga final em n8n `1.123.21`, concorrência 50 e timeout 60 s: lotes
  50/10, 100/25 e 500/50 concluídos com 654 respostas HTTP `202`, zero
  `502`/`503`; 651 solicitações únicas, três duplicatas idempotentes
  reconhecidas e zero linhas duplicadas.
- Estados finais: 595 `done`, 34 `manual_review`, 22 `submission_uncertain`.
  Os 34 casos de revisão não iniciaram `task_post`; os 22 incertos não podem
  ser reenviados automaticamente. A mensagem de revisão é genérica e sua causa
  específica não foi isolada.
- Mock: 595 `task_post`, 5.355 IDs salvos e distintos, 5.355 `task_get`, 617
  respostas válidas do Places e 3.760 eventos CRM; custo simulado/real zero.
- Segurança integrada: status e relatório protegidos, tenant isolation,
  bearer inválido rejeitado, nove pontos e `40102 No Search Results` aprovados.
- Restart isolado com tarefas pendentes: n8n e Smart Grid voltaram a ficar
  saudáveis; polling e requests `done` avançaram; IDs persistidos permaneceram
  únicos e não foram reenviados. Submissões em voo foram marcadas incertas.
- Regressão final: `206 passed`, 0 falhas, 1 aviso de depreciação do `httpx`
  em `TestClient`.
- Percentuais: projeto **89%**; homologação integrada **100% dos critérios
  locais definidos**, sem equivaler a prontidão de produção.
- O resultado histórico anterior permanece: 650 solicitações, 623 `done`,
  20 `manual_review`, 7 `submission_uncertain` e respostas `502`.
- Métricas e limitações completas estão em `docs/n8n-setup.md` e `PROGRESS.md`.

## Homologação preservada — registro anterior

Todos os containers de homologação estão parados. Volumes históricos e das
novas rodadas foram preservados; nenhum volume foi removido:

- `geogrid-homolog-postgres-state`
- `geogrid-homolog-n8n-state`
- `geogrid-homolog-smart-grid-state`
- `geogrid-homolog-mock-certs`
- `geogrid-homolog-20261125-{postgres,n8n,smart-grid}-state` e
  `geogrid-homolog-20261125-mock-certs`
- `geogrid-homolog-20261125-fixed-{postgres,n8n,smart-grid}-state` e
  `geogrid-homolog-20261125-fixed-mock-certs`
- `geogrid-homolog-20261125-verified-{postgres,n8n,smart-grid}-state` e
  `geogrid-homolog-20261125-verified-mock-certs`
- `geogrid-homolog-20261125-verified2-{postgres,n8n,smart-grid}-state` e
  `geogrid-homolog-20261125-verified2-mock-certs`

Os arquivos de amostragem de CPU/memória, sem dados pessoais, estão preservados
em `/tmp/geogrid-homolog-fixed-resource-samples.csv`,
`/tmp/geogrid-homolog-verified-resource-samples.csv` e
`/tmp/geogrid-homolog-verified2-resource-samples.csv`. Dados sintéticos dos
bancos/volumes ficam fora do Git. Não usar `docker compose down -v`,
`docker volume rm` ou limpeza de volumes. O SQLite local e backups foram
preservados.

## Comandos para retomada

Na raiz do repositório:

```sh
# Regressão completa
.venv/bin/python -m pytest -q

# Validar o Compose sem iniciar serviços
docker compose -p geogrid-homolog-next -f tests/integration/compose.yaml config -q

# Para uma rodada nova, defina nomes de volumes exclusivos fora do repositório.
# Nunca reutilize os projetos/volumes de evidência listados acima.
cat > /tmp/geogrid-homolog-next-volumes.yaml <<'YAML'
volumes:
  postgres-state:
    name: geogrid-homolog-next-postgres-state
  smart-grid-state:
    name: geogrid-homolog-next-smart-grid-state
  n8n-state:
    name: geogrid-homolog-next-n8n-state
  mock-certs:
    name: geogrid-homolog-next-mock-certs
YAML

# Nova stack e volumes próprios; somente serviços mockados
docker compose -p geogrid-homolog-next -f tests/integration/compose.yaml -f /tmp/geogrid-homolog-next-volumes.yaml up -d --build
docker compose -p geogrid-homolog-next -f tests/integration/compose.yaml -f /tmp/geogrid-homolog-next-volumes.yaml --profile test run --rm test-runner

# Parar sem remover evidências
docker compose -p geogrid-homolog-next -f tests/integration/compose.yaml -f /tmp/geogrid-homolog-next-volumes.yaml stop
```

O runner espera banco vazio e falha na checagem de contagem se reutilizar uma
homologação que já contém pedidos. O Compose usa nomes de volume explícitos;
somente mudar `-p` não isola os dados. O override acima cria nomes distintos e
evita alterar os volumes de evidência existentes. Nunca usar `down -v`.
Em qualquer retry, preservar o mesmo `request_id`; não interpretar persistência
sem resposta HTTP `202` como confirmação ao usuário.

## Inventário do worktree da rodada anterior

Modificados:

`app/landing.py`; `docs/n8n-setup.md`; `tests/integration/compose.yaml`;
`tests/integration/run_homologation.mjs`; `tests/test_landing.py`;
`ROADMAP.md`; `PROGRESS.md`; `TASKS.md`; `CHANGELOG.md`;
`SESSION_HANDOFF.md`; `.github/copilot-instructions.md`.

Backups mantidos fora de qualquer commit:

`app/competitive_metrics.py.before_recurrence_fix.bak`;
`app/competitor_identity.py.before_conflict_fix.bak`;
`app/metrics.py.pre_ida_adaptativo.bak`.

`instalar_ida_adaptativo.py` também fica fora da proposta: é um script auxiliar
de alteração de código com backup, não necessário à execução da aplicação.
`geogrid.db` é ignorado pelo Git e fica excluído.

## Checklist de segurança do checkpoint

- Varredura por padrões comuns de chaves, tokens e chaves privadas: sem
  credenciais reais encontradas.
- Strings de email/telefone em documentação/testes são exemplos sintéticos.
  Não foram encontrados dados de contato de produção.
- Placeholders `homolog-only-*`, token inválido de teste, UUIDs/CIDs sintéticos
  não são credenciais reais. URLs de DataForSEO aparecem como endpoints de
  código/documentação; qualquer teste usa DNS/rede interna e simuladores.
- Arquivos `.env` não encontrados. Banco local `geogrid.db`, bancos dos volumes
  e certificados/chaves temporários dos mocks não são candidatos a commit.
- `git diff --check` concluído sem erros.

## Próxima atividade

1. Isolar a causa dos 34 `manual_review` com evidência segura, sem introduzir
   novas chamadas pagas.
2. Revisar e reconciliar os 22 `submission_uncertain` pelo procedimento
   operacional; nunca reenviá-los automaticamente.
3. Confirmar recursos, backup/restauração, retenção e monitoramento no Easypanel
   antes de qualquer proposta de publicação.
4. Obter documentação/contratos oficiais do CRM T2S antes de implementar a
   integração real.

Smart Leads Intelligence permanece futuro, não iniciado e não autorizado.
Nenhum commit, push, merge, deploy ou chamada paga está autorizado.

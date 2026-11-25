# Session Handoff — Smart Grid by ZURI

Data: 2026-11-25

## Retomada P02 — validação concluída; checkpoint em preparação

- Checkpoint P01 `6fc53a142cbe0cb0edceed83a097e96853d8f2da` foi commitado e
  enviado à branch autorizada; o remoto foi confirmado com o mesmo hash.
- P02 permanece local, sem commit. A migration 007 e o workflow
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
- Próximo passo: revisão de arquivos e checkpoint local P02 autorizado; após
  concluído, implementar P03. P04 e Landing Page 2.0 não foram autorizados.
  Push não foi autorizado.

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

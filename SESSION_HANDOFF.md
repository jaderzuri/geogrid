# Session Handoff — Smart Grid by ZURI

Data: 2026-11-25

## Não fazer na retomada sem confirmação

Não iniciar Smart Leads Intelligence nem novas funcionalidades. A homologação
local da correção dos `502` e do restart foi concluída; os próximos passos são
investigar os casos de revisão manual, reconciliar submissões incertas e revisar
o sizing. Não fazer commit, push, merge, deploy, chamadas pagas ou operações de
produção sem autorização.

## Estado Git

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

## Homologação preservada

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

## Inventário do worktree nesta retomada

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

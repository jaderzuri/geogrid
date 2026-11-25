# Session Handoff — Smart Grid by ZURI

Data: 2026-11-25

## Não fazer na retomada sem confirmação

Não iniciar Smart Leads Intelligence nem novas funcionalidades. Primeiro
retomar a correção/homologação dos `502` e o teste de restart. Não fazer commit,
push, merge, deploy, chamadas pagas ou operações de produção sem autorização.

## Estado Git

- Branch atual: `feature/dataforseo-standard-normal`.
- Sem commit ou push nesta sessão.
- `git diff --check` passou; lista proposta de checkpoint deve ser revista no
  final deste handoff e aprovada antes de staging/commit.
- Não incluir backups, bancos, secrets, contatos reais ou volumes no checkpoint.

## Resultado técnico para retomar

- Suíte: `204 passed`, 0 falhas, 1 aviso de depreciação do `httpx` em
  `TestClient`.
- Carga planejada 50/100/500: 50 e 100 aceitos; runner falhou no lote de 500
  por HTTP 502. PostgreSQL isolado registrou 650 IDs/chaves idempotentes
  únicos: 623 `done`, 20 `manual_review`, 7 `submission_uncertain`.
- Nenhum custo real; chamadas apenas aos simuladores locais. Casos incertos não
  podem ser reenviados automaticamente.
- Status/relatório privado, tenant isolation, bearer incorreto, nove pontos e
  `40102` passaram no smoke integrado anterior.
- Recuperação pós-restart com trabalho pendente não foi comprovada. Uma
  execução parcial do runner após restart parou na asserção de contagem, pois
  o banco isolado já continha dados.
- A documentação `docs/n8n-setup.md` contém métricas observadas e limitações.

## Homologação preservada

Todos os containers de aplicação estão parados. Volumes existentes não foram
removidos:

- `geogrid-homolog-postgres-state`
- `geogrid-homolog-n8n-state`
- `geogrid-homolog-smart-grid-state`
- `geogrid-homolog-mock-certs`

Não foi encontrado arquivo `geogrid-*` em `/tmp`. A evidência estruturada fica
nos volumes isolados; contém apenas dados sintéticos e deve permanecer fora do
Git. Não usar `docker compose down -v`, `docker volume rm` ou limpeza de
volumes. O banco SQLite local e backups foram preservados.

## Comandos para retomada

Na raiz do repositório:

```sh
# Regressão
.venv/bin/python -m pytest -q

# Validar compose e workflows sem iniciar chamadas externas
docker compose -p geogrid-homolog-next -f tests/integration/compose.yaml config -q
node -e 'const fs=require("fs"); for (const f of fs.readdirSync("workflows/n8n").filter(x=>x.endsWith(".json"))) JSON.parse(fs.readFileSync(`workflows/n8n/${f}`,"utf8"))'

# O compose declara nomes de volume fixos. Para uma rodada nova sem tocar
# nos volumes de evidência atuais, crie este override fora do repositório:
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
docker compose -p geogrid-homolog-next -f tests/integration/compose.yaml -f /tmp/geogrid-homolog-next-volumes.yaml up -d
docker compose -p geogrid-homolog-next -f tests/integration/compose.yaml -f /tmp/geogrid-homolog-next-volumes.yaml --profile test run --rm test-runner

# Parar sem remover evidências
docker compose -p geogrid-homolog-next -f tests/integration/compose.yaml -f /tmp/geogrid-homolog-next-volumes.yaml stop
```

O runner espera banco vazio e falha na checagem de contagem se reutilizar uma
homologação que já contém pedidos. O Compose usa nomes de volume explícitos;
somente mudar `-p` não isola os dados. O override acima cria nomes distintos e
evita alterar os volumes de evidência existentes. Nunca usar `down -v`.
Repetir o lote de 500 somente após tratar os `502`; não interpretar persistência
como resposta HTTP bem-sucedida.

## Inventário integral do worktree

Modificados:

`README.md`; `app/cli.py`; `app/db.py`; `app/fetchers/base.py`;
`app/fetchers/dataforseo.py`; `app/main.py`; `app/metrics.py`; `app/scan.py`;
`app/static/app.js`; `app/static/index.html`; `app/static/style.css`;
`tests/test_scan_analysis.py`; `tests/test_ui_help_panel.py`.

Novos arquivos não-backup:

`app/external_api.py`; `app/landing.py`; `app/static/diagnostic.html`;
`app/static/diagnostic.js`; `app/static/landing.css`;
`app/static/landing.html`; `app/static/landing.js`; `docs/crm-t2s-adapter.md`;
`docs/n8n-integration.md`; `docs/n8n-setup.md`;
`tests/integration/005-homologation-budget.sql`;
`tests/integration/Dockerfile.mock`;
`tests/integration/Dockerfile.test-runner`;
`tests/integration/bootstrap-n8n.sh`; `tests/integration/compose.yaml`;
`tests/integration/mock_services.py`; `tests/integration/nginx.conf`;
`tests/integration/run_homologation.mjs`; `tests/test_dataforseo.py`;
`tests/test_external_api.py`; `tests/test_landing.py`;
`tests/test_n8n_queue_artifacts.py`; `tests/test_scan_coverage.py`;
`workflows/n8n/01-recepcao-leads.json`;
`workflows/n8n/02-processador-standard.json`;
`workflows/n8n/03-recuperacao-resultados.json`;
`workflows/n8n/04-status-diagnostico.json`;
`workflows/n8n/05-relatorio-protegido.json`;
`workflows/n8n/06-crm-outbox-adapter.json`;
`workflows/n8n/sql/000-create-database.sql`;
`workflows/n8n/sql/001-schema.sql`; `workflows/n8n/sql/002-functions.sql`;
`workflows/n8n/sql/003-landing-crm-capability.sql`;
`workflows/n8n/sql/004-capability-lifecycle-and-crm-dispatch.sql`.

Backups mantidos, fora do commit proposto:

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

1. Identificar precisamente o caminho que gera `502` após persistência e definir
   resposta/retry idempotente sem duplicar pedidos.
2. Rodar regressão focada e suíte completa.
3. Reexecutar cargas progressivas e coletar métricas por lote.
4. Demonstrar recuperação após restart com tarefas pendentes e IDs mockados.
5. Atualizar relatório e preparar checkpoint para aprovação.

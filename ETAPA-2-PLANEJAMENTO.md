# ETAPA 2 — Recuperação de Contatos Históricos

**Data:** 2026-11-27  
**Status:** PLANEJAMENTO (pronto para início)  
**Objetivo:** Investigar possibilidade de recuperar telefones e dados de contato dos 4.454 registros históricos sem executar novas pesquisas pagas.

---

## 1. Dados Históricos Conhecidos

### Situação Atual
- **Registros históricos:** 4.454 ocorrências
- **Empresas distintas:** 1.165 identificadores únicos
- **Telefones capturados:** 0 (nenhum nos resultados analisados)
- **Método principal:** Live (não Standard)
- **Tabela comercial:** `commercial_prospects` vazia

### Questões a Responder
1. ❓ Existem respostas originais do DataForSEO preservadas em arquivos/logs/backups?
2. ❓ Que identificadores de tarefas estão associados às pesquisas históricas?
3. ❓ Qual foi o histórico de execuções n8n que processou esses dados?
4. ❓ O DataForSEO permite recuperar resultados históricos do método Live?
5. ❓ Quais endpoints do DataForSEO suportam enriquecimento por CID/Place ID?
6. ❓ Qual alternativa apresenta o menor custo por empresa?
7. ❓ Quais permissões de armazenamento e utilização comercial são necessárias?

---

## 2. Fase 1 — Investigação Local

### 2.1 Análise do Banco de Dados de Produção

**Checklist:**
- [ ] Conectar ao SQLite de produção (apenas leitura)
- [ ] Listar esquema completo: tabelas, colunas, índices
- [ ] Contar registros em cada tabela:
  - `scans` — registros de pesquisas
  - `points` — pontos do grid processados
  - `businesses` — empresas encontradas
  - `business_finder_data_provenance` — origem dos dados
  - `commercial_prospects` — oportunidades comerciais

**Esperado:**
```sql
SELECT COUNT(*) FROM scans;              -- N pesquisas executadas
SELECT COUNT(*) FROM businesses;         -- 4.454 (ou ~4.454)
SELECT COUNT(DISTINCT cid) FROM businesses WHERE cid IS NOT NULL;  -- 1.165
SELECT COUNT(*) FROM businesses WHERE phone IS NOT NULL;  -- 0 ou X
```

### 2.2 Busca de Respostas Originais

**Checklist:**
- [ ] Verificar se campo `results` em `points` tem dados em JSON
- [ ] Procurar por arquivos de dump/backup:
  - `*.db-*` (backups SQLite)
  - `*.json` (exports)
  - `*.csv` (históricos)
  - Logs em `/var/log`, `/home/*/logs`
- [ ] Verificar se há diretório `app/data/cache` ou similar
- [ ] Procurar por requests/responses em journais de sistema

**Comando sugestivo:**
```bash
# Em produção (VPS/Easypanel):
find /var -name "*geogrid*" -o -name "*dataforseo*" 2>/dev/null
ls -la /home/*/backups 2>/dev/null
sqlite3 geogrid.db "SELECT COUNT(*) FROM points WHERE results IS NOT NULL;"
```

### 2.3 Investigação de Task IDs

**Checklist:**
- [ ] Verificar se campo `provider_ref` em scans contém task IDs do DataForSEO
- [ ] Procurar por referências de task em logs/históricos
- [ ] Verificar se n8n guarda IDs de requisições em suas execuções
- [ ] Buscar em documentação se DataForSEO permite recuperar by task_id

**Comando sugestivo:**
```bash
sqlite3 geogrid.db "SELECT id, provider_ref FROM scans LIMIT 5;"
# Espera: valores como "task_12345" ou similar
```

---

## 3. Fase 2 — Investigação DataForSEO

### 3.1 Documentação Oficial

**URLs a Consultar:**
- https://docs.dataforseo.com/api/v3/google_maps/google_maps_search_live
- https://docs.dataforseo.com/api/v3/google_maps/business_info
- https://docs.dataforseo.com/api/v3/business_data_api (para enriquecimento)
- https://docs.dataforseo.com/tutorials/how-do-i-retrieve-results

**Questões:**
- [ ] Live API: Suporta recuperação de task_id anterior?
- [ ] Business Info API: Requer pagamento ou é incluído na Live?
- [ ] Qual é o período de retenção de dados históricos?
- [ ] Quais campos são retornados por `business_info`?
- [ ] Existe limite de requisições diárias/mensais?

### 3.2 Enriquecimento por CID/Place ID

**Endpoints Candidatos:**
1. **Google Maps → Business Info** (endpoint específico para empresas)
2. **Google Places API** (alternativa ao DataForSEO)
3. **DataForSEO Business Data API** (enriquecimento puro)

**Validação:**
```python
# Testar com um CID conhecido
curl -X POST https://api.dataforseo.com/v3/business_data/google/business_info/task_post \
  -d '{
    "login": "...",
    "password": "...",
    "data": {
      "cid": "cid_12345"
    }
  }' | jq .
```

**Esperado:** Resposta com campos (name, phone, website, reviews, etc.)

### 3.3 Custos

**Checklist:**
- [ ] Qual é o custo por requisição de Business Info?
- [ ] Comparar com custo de nova Live search (~ 1 ponto)
- [ ] Calcular: 1.165 empresas × custo = X reais

**Exemplo de Cálculo:**
```
Se Business Info custa $0.01 por requisição:
  1.165 × $0.01 = $11.65 ≈ R$ 60 (com conversão)
  
Se nova Live search custa $0.05 (3×3 = 9 pontos):
  Novo grid: 1.165 / 9 × $0.05 = $6.47 ≈ R$ 32
  (mas requer novos grids geográficos)
```

---

## 4. Fase 3 — Investigação n8n

### 4.1 Histórico de Execuções

**Checklist:**
- [ ] Listar workflows que processaram dados históricos
- [ ] Verificar execuções passadas: status, timestamp, inputs/outputs
- [ ] Procurar por logs de integração com DataForSEO
- [ ] Verificar se há webhook callbacks que gravaram resultados

**Em Easypanel:**
```bash
# Acessar logs de n8n
docker logs n8n 2>&1 | grep -i dataforseo | head -100
```

### 4.2 Referências de Task

**Checklist:**
- [ ] Se há um workflow que cria tasks no DataForSEO, verificar se grava task_id
- [ ] Procurar por arquivo de mapping: scan_id → task_id
- [ ] Se n8n armazena em PostgreSQL, consultar histórico de execuções

**Query Sugestiva (se PostgreSQL):**
```sql
SELECT * FROM executions 
WHERE workflow_name LIKE '%geogrid%' 
  OR workflow_name LIKE '%dataforseo%'
ORDER BY created_at DESC 
LIMIT 100;
```

---

## 5. Utilitário Dry-Run de Enriquecimento

### 5.1 Escopo

Após investigação, criar utilitário com recursos:

```python
class HistoricalEnrichmentUtility:
    """Recupera e enriquece contatos históricos sem custos desnecessários."""
    
    def __init__(self, db_path: str, mode: str = "dry-run"):
        self.mode = mode  # "dry-run" ou "execute"
        self.db = db_path
        self.costs = []
        self.recovered = []
        self.errors = []
    
    async def audit_historical_data(self) -> dict:
        """1. Audita dados históricos no banco."""
        return {
            'total_records': 4454,
            'unique_companies': 1165,
            'with_phone': 0,
            'with_website': 0,
            'by_method': {'Live': 4454, 'Standard': 0},
        }
    
    async def estimate_costs(self) -> dict:
        """2. Estima custos de recuperação antes de executar."""
        return {
            'method': 'business_info_api',
            'per_request': 0.01,
            'total_requests': 1165,
            'estimated_cost': 11.65,
            'estimated_brl': 60,
        }
    
    async def recover_from_local_cache(self) -> dict:
        """3. Tenta recuperar de arquivos/logs locais."""
        return {
            'recovered': 234,  # Se encontrados
            'from_source': 'backup_files',
            'timestamp': '2026-01-15',
        }
    
    async def enrich_by_identifier(self, cid: str, place_id: str) -> dict | None:
        """4. Enriquece um empresa por CID/Place ID."""
        if self.mode == "dry-run":
            return {'status': 'simulated', 'would_cost': 0.01}
        # Execute real enrichment
    
    async def process_batch(self, limit: int = 100) -> dict:
        """5. Processa lote com deduplicação e elegibilidade."""
        return {
            'processed': 100,
            'deduplicated': 5,
            'eligible': 95,
            'errors': 0,
        }
    
    async def generate_report(self) -> dict:
        """6. Gera relatório CSV exportável."""
        return {
            'rows': 1165,
            'columns': ['name', 'cid', 'place_id', 'phone', 'website', 'eligible'],
            'file': 'enrichment_results.csv',
        }
```

### 5.2 Modo Dry-Run

**Comportamento:**
- Simula requisições sem custo real
- Exibe estimativas de custo
- Gera relatório de o que seria feito
- Permite validar antes de executar

**Exemplo:**
```bash
$ python3 app/utils/historical_enrichment.py --mode dry-run --limit 100
```

**Output:**
```
╔════════════════════════════════════════════════════════╗
║ ETAPA 2 — ENRIQUECIMENTO HISTÓRICO (DRY-RUN)          ║
╚════════════════════════════════════════════════════════╝

✓ Auditoria:
  - Registros históricos: 4.454
  - Empresas distintas: 1.165
  - Com telefone: 0
  - Sem telefone: 1.165

✓ Estratégia recomendada: Business Info API

✓ Estimativa de custo:
  - Por requisição: $0.01
  - Total (1.165 empresas): $11.65 (≈ R$ 60)
  - Duração estimada: 10-15 min

✓ Checkpoint: Aprove antes de executar
```

---

## 6. Plano de Execução

### Fase 1: Investigação (esta semana)
1. [ ] Conectar ao banco de produção (leitura)
2. [ ] Procurar respostas originais em arquivos/logs
3. [ ] Verificar task IDs do DataForSEO
4. [ ] Consultar documentação oficial
5. [ ] Investigar histórico n8n
6. **Resultado:** Relatório com achados e recomendação

### Fase 2: Desenvolvimento (próxima semana)
7. [ ] Implementar `HistoricalEnrichmentUtility`
8. [ ] Modo dry-run com estimativas
9. [ ] Testes com dados sintéticos
10. [ ] Geração de CSV de oportunidades
11. **Resultado:** Utilitário pronto, sem custos reais

### Fase 3: Validação (após aprovação)
12. [ ] Revisor aprova estratégia
13. [ ] Executar enriquecimento (modo execute)
14. [ ] Validar resultados
15. [ ] Popular `commercial_prospects`
16. **Resultado:** Banco pronto com 1.165 oportunidades

---

## 7. Riscos e Mitigações

| Risco | Probabilidade | Mitigação |
|-------|---------------|-----------|
| Task IDs não preservados | Alta | Usar CID/Place ID como fallback |
| Dados obsoletos | Média | Validar data_updated, aplicar timeout |
| Custos inesperados | Baixa | Sempre modo dry-run antes de execute |
| Elegibilidade comercial desconhecida | Alta | Validar consentimento antes de contato |
| Duplicidade não-óbvia | Média | Usar múltiplas chaves (CID + Place ID + name) |

---

## 8. Critérios de Aceitação

- [ ] Relatório de investigação completo
- [ ] Utilitário implementado em Python
- [ ] Modo dry-run validado com dados históricos
- [ ] Estimativa de custos precisa
- [ ] CSV exportável com 1.165 oportunidades
- [ ] Zero custos reais executados
- [ ] Documentação completa
- [ ] Testes automatizados

---

## 9. Próximas Etapas Após Conclusão

**ETAPA 3 — Interface de Oportunidades Comerciais:**
1. Criar novo menu "Oportunidades Comerciais"
2. Implementar tabela com dados recuperados
3. Adicionar filtros, busca, exportação

**ETAPA 4 — Integração Comercial:**
1. Preparar adaptador CRM T2S
2. Fila SMS/RCS com limite 200/dia
3. Workflows n8n (inativos por padrão)

---

## 10. Blockers e Dependências

**Necessário antes de começar:**
- ✅ Acesso ao banco de produção (leitura apenas)
- ✅ Acesso à documentação DataForSEO
- ✅ Acesso aos logs de n8n
- ⏳ Autorização para consumir até X reais em teste (se aplicável)

**Não bloqueia:**
- Já completou FASE A ✅
- Pode rodar em paralelo com ETAPA 3

---

## 11. Contatos

**Para dúvidas sobre ETAPA 2:**
- WhatsApp: +55 (11) 99691-9411
- Telefone: +55 (11) 4210-3311
- Email: contato@zurismartec.com.br

---

**Documento de planejamento:** 2026-11-27  
**Status:** Pronto para início após aprovação de FASE A

# Integração externa Smart Grid ↔ n8n

Este contrato descreve a API autenticada para receber diagnósticos processados
por n8n. A interface web permanece independente e continua usando DataForSEO
Live. A API externa **não cria tarefas nem faz chamadas à DataForSEO**:

- o n8n envia as tarefas Standard Normal com `priority: 1`;
- o n8n mantém sua fila persistente e os IDs das tarefas antes de consultar os
  resultados assíncronos;
- o Smart Grid gera e persiste uma grade fixa 3×3, recebe os resultados,
  calcula os indicadores existentes e disponibiliza o relatório.

Todos os endpoints exigem autenticação e escopo de cliente. O corpo máximo é
1 MiB. Não coloque o token no navegador, na Landing Page, em logs ou em
parâmetros de URL.

## Sequência n8n recomendada

1. Registre/identifique a empresa em `POST /businesses`.
2. Crie uma pesquisa em `POST /scans` e persista o `scan_id`,
   `external_request_id` e os nove pontos antes de submeter tarefas pagas.
3. Envie uma tarefa Standard para cada ponto usando
   `POST https://api.dataforseo.com/v3/serp/google/maps/task_post`, com
   `priority: 1`, `language_code: "pt"` e as coordenadas devolvidas pelo
   Smart Grid. Por exemplo:

   ```json
   [
     {
       "keyword": "serviço local",
       "location_coordinate": "-23.5415,-46.6437,14z",
       "language_code": "pt",
       "priority": 1,
       "depth": 20,
       "tag": "landing-submission-2026-00451:0:0"
     }
   ]
   ```

   Uma chamada pode conter até 100 tarefas; para a grade fixa, envie as nove
   coordenadas. O exemplo `14z` corresponde ao zoom usado atualmente no Live.
   O envio Standard é cobrado; não reenvie uma tarefa depois que o n8n salvou
   seu ID.
4. Persista cada `tasks[].id` no armazenamento durável do n8n e registre-o no
   ponto Smart Grid usando `status: "pending"` antes da recuperação. O `tag`
   pode ajudar a relacionar a resposta com `row` e `col`.
5. Recupere cada resultado por
   `GET https://api.dataforseo.com/v3/serp/google/maps/task_get/advanced/{id}`.
   Atualize o mesmo ponto pelo endpoint `/points`, preservando o código e a
   mensagem DataForSEO. Use `ok`, `no_results` ou `error` conforme o resultado.
6. Consulte o endpoint GET do Smart Grid para retomar IDs/pontos pendentes
   após reinicializações. Chame `/complete` apenas quando não houver pontos
   pendentes; use encerramento parcial somente quando houver uma decisão
   explícita e uma razão registrada.

O caminho `/task_post` cobra a submissão, enquanto a recuperação por ID não
gera uma nova cobrança de tarefa. Confira os detalhes dos campos e respostas
nas referências oficiais de
[Google Maps task POST](https://docs.dataforseo.com/v3/serp/google/maps/task_post/)
e [Google Maps task GET](https://docs.dataforseo.com/v3/serp/google/maps/task_get/advanced/).

## Identidade e autenticação

Defina `GEOGRID_EXTERNAL_API_TOKEN` no ambiente do Smart Grid. O valor precisa
ser o mesmo segredo enviado pelo n8n como:

```http
Authorization: Bearer <GEOGRID_EXTERNAL_API_TOKEN>
X-GeoGrid-Client-ID: <identificador-estavel-do-cliente>
Content-Type: application/json
```

O `X-GeoGrid-Client-ID` deve ser estável e ser determinado pelo n8n, não
escolhido livremente pelo visitante da Landing Page. Ele isola os registros
externos: uma pesquisa só pode ser lida ou alterada no mesmo escopo em que foi
criada. O token é uma credencial de serviço compartilhada; mantenha-o apenas no
n8n e no backend. Qualquer detentor desse token pode selecionar um escopo de
cliente, portanto não o distribua a integrações não confiáveis.

Com o token ausente, a API responde `503` e permanece desabilitada. Token
ausente ou inválido responde `401`. IDs de pesquisa inexistentes ou de outro
cliente respondem igualmente `404`, sem revelar se o registro existe.

## Endpoints

### `GET /api/external/health`

Verifica o serviço e o SQLite. É autenticado e não contata a DataForSEO.

Resposta `200`:

```json
{
  "status": "ok",
  "storage": "ok",
  "dataforseo_calls_enabled": false
}
```

### `POST /api/external/businesses`

Registra ou identifica a empresa dentro do escopo de cliente. O n8n deve
fornecer o centro geográfico da empresa já identificado; este endpoint não faz
busca externa.

Entrada:

```json
{
  "external_business_id": "landing-business-451",
  "name": "Empresa Exemplo",
  "cid": "12345678901234567890",
  "place_id": "ChIJ...",
  "lat": -23.5505,
  "lng": -46.6333,
  "address": "Rua Exemplo, 100, São Paulo",
  "category": "Serviço local"
}
```

`external_business_id` é a chave estável fornecida pelo sistema chamador.
`cid`, `place_id`, endereço e categoria são opcionais. Latitude e longitude
são obrigatórias e validadas. Repetir a mesma chave e os mesmos dados é
idempotente; reutilizá-la com outra identidade ou localização responde `409`.

Resposta `200`:

```json
{
  "business_id": "landing-business-451",
  "name": "Empresa Exemplo",
  "cid": "12345678901234567890",
  "place_id": "ChIJ...",
  "lat": -23.5505,
  "lng": -46.6333,
  "address": "Rua Exemplo, 100, São Paulo",
  "category": "Serviço local",
  "created": true
}
```

O `business_id` retornado é o identificador externo; o ID interno do banco
nunca é exposto.

### `POST /api/external/scans`

Cria ou recupera um diagnóstico com configuração fixa. Não aceita grade,
espaçamento, centro ou múltiplas palavras-chave na entrada.

Entrada:

```json
{
  "external_request_id": "landing-submission-2026-00451",
  "external_business_id": "landing-business-451",
  "keyword": "serviço local"
}
```

- uma palavra-chave por diagnóstico;
- grade 3×3, espaçamento de 1 km e nove pontos;
- centro copiado da empresa registrada;
- coordenadas calculadas pelo backend;
- idioma retornado por `GEOGRID_EXTERNAL_LANGUAGE_CODE` (padrão `pt`).

Resposta `200`:

```json
{
  "scan_id": "8e91211a-9fd8-476f-b6be-145a97aef311",
  "external_request_id": "landing-submission-2026-00451",
  "business_id": "landing-business-451",
  "keyword": "serviço local",
  "language_code": "pt",
  "status": "queued",
  "grid_size": 3,
  "spacing_km": 1.0,
  "center": {"lat": -23.5505, "lng": -46.6333},
  "progress": 0,
  "total": 9,
  "created": "2026-11-24T18:00:00+00:00",
  "updated": "2026-11-24T18:00:00+00:00",
  "finished": null,
  "completion_reason": null,
  "metrics": null,
  "coverage": {
    "requested_points": 9,
    "valid_points": 0,
    "no_results_points": 0,
    "failed_points": 0,
    "pending_points": 9,
    "coverage_pct": 0.0,
    "coverage_level": "no_valid_measurement"
  },
  "competitors": null,
  "points": [
    {
      "row": 0,
      "col": 0,
      "lat": -23.5415,
      "lng": -46.6437,
      "rank": null,
      "status": "pending",
      "dataforseo_task_id": null,
      "error": null,
      "error_category": null,
      "api_status_code": null,
      "api_status_message": null,
      "results": []
    }
  ],
  "created_new": true
}
```

`points` contém os nove pontos (o exemplo mostra apenas o primeiro). O ID
externo da pesquisa é UUID e pode ser usado nos endpoints seguintes. A chave
única de idempotência é `(X-GeoGrid-Client-ID, external_request_id)`.
Repetições com o mesmo conteúdo retornam a pesquisa original; a mesma chave
com empresa ou palavra-chave diferentes responde `409`.

### `POST /api/external/scans/{scan_id}/points`

Recebe de um a nove pontos por lote. Os pontos devem corresponder às
coordenadas e células devolvidas na criação da pesquisa. O backend calcula a
posição usando a identidade registrada; não aceite nem envie `rank`.

Para registrar IDs enquanto as tarefas ainda aguardam recuperação, envie
`status: "pending"` e `dataforseo_task_id`. Reenvios de resultados idênticos
são idempotentes e não incrementam o progresso.

Exemplo de resultado final:

```json
{
  "points": [
    {
      "row": 0,
      "col": 0,
      "lat": -23.5415,
      "lng": -46.6437,
      "status": "ok",
      "dataforseo_task_id": "11131718-1535-0161-0000-bc49145f65fc",
      "results": [
        {
          "name": "Empresa Exemplo",
          "cid": "12345678901234567890",
          "place_id": "ChIJ...",
          "lat": -23.5415,
          "lng": -46.6437,
          "rating": 4.7,
          "reviews": 123,
          "category": "Serviço local",
          "address": "Rua Exemplo, 100"
        }
      ],
      "error": null,
      "error_category": null,
      "api_status_code": 20000,
      "api_status_message": "Ok."
    }
  ]
}
```

Estados por ponto aceitos:

- `ok`: consulta válida com resultados; o Smart Grid calcula a posição;
- `no_results`: ausência legítima de resultados;
- `error`: falha, com `error` ou `error_category`;
- `pending`: tarefa ainda não recuperada; pode incluir seu ID DataForSEO.

`results` aceita no máximo 20 itens. Códigos e mensagens originais podem ser
informados em `api_status_code` e `api_status_message`. Campos desconhecidos,
células repetidas no mesmo lote, coordenadas fora da grade e IDs de tarefa
reutilizados em outro ponto são rejeitados.

Resposta `200`:

```json
{
  "scan_id": "8e91211a-9fd8-476f-b6be-145a97aef311",
  "status": "partial",
  "progress": 1,
  "total": 9,
  "accepted_points": 1,
  "idempotent": false
}
```

`progress` conta pontos finais, não IDs de tarefa registrados.

### `GET /api/external/scans/{scan_id}`

Devolve o estado, progresso, pontos, IDs DataForSEO persistidos, cobertura,
métricas existentes, concorrentes e análise calculada pelos mecanismos atuais.
O conteúdo está sempre limitado ao `X-GeoGrid-Client-ID` autenticado.

Resposta resumida `200`:

```json
{
  "scan_id": "8e91211a-9fd8-476f-b6be-145a97aef311",
  "external_request_id": "landing-submission-2026-00451",
  "business_id": "landing-business-451",
  "keyword": "serviço local",
  "language_code": "pt",
  "status": "processing",
  "progress": 1,
  "total": 9,
  "metrics": null,
  "competitors": null,
  "points": [],
  "business": {"name": "Empresa Exemplo", "cid": "12345678901234567890"},
  "analysis": {
    "scan_id": "8e91211a-9fd8-476f-b6be-145a97aef311",
    "metrics": {"arp": 1.0, "coverage_pct": 11.1},
    "icm": {},
    "icpb_v2": {},
    "ice": {},
    "iop": {},
    "points": []
  }
}
```

No exemplo resumido, `points` e os objetos de análise estão abreviados. As
fórmulas e gates permanecem os do Smart Grid.

### `POST /api/external/scans/{scan_id}/complete`

Finaliza idempotentemente. A chamada normal não pode encerrar enquanto houver
pontos `pending`.

Finalização normal:

```json
{}
```

Se ainda houver pontos pendentes, responde `409` com a quantidade pendente.
Quando os nove pontos forem finais, o Smart Grid determina `done`, `partial`
ou `failed` de acordo com os resultados e falhas.

Encerramento parcial explícito:

```json
{
  "allow_partial": true,
  "completion_reason": "Evento encerrado; tarefas restantes não serão recuperadas"
}
```

É obrigatório registrar a razão; os pontos não recuperados permanecem
`pending` e continuam fora da base válida. Falha definitiva:

```json
{
  "failure_reason": "Workflow interrompido definitivamente"
}
```

Resposta:

```json
{
  "scan_id": "8e91211a-9fd8-476f-b6be-145a97aef311",
  "status": "done",
  "progress": 9,
  "total": 9,
  "finished": "2026-11-24T18:10:00+00:00",
  "completion_reason": null,
  "idempotent": false
}
```

Repetir a mesma finalização retorna o estado original com `idempotent: true`.
Uma finalização diferente após o encerramento responde `409`.

## Estados e recuperação

- `queued`: pesquisa registrada; nenhum resultado foi recebido ainda;
- `processing`: o n8n registrou tarefa(s) ou todos os pontos já têm resultado e
  aguardam a chamada de finalização;
- `partial`: alguns pontos foram recebidos enquanto outros continuam pendentes;
- `done`: finalizada sem falhas técnicas;
- `failed`: falha explícita ou todos os pontos falharam sem medição válida.

Ao reiniciar, o Smart Grid preserva pesquisas externas abertas; elas não são
marcadas como interrompidas pelo mecanismo destinado às pesquisas Live. O n8n
deve retomar sua própria fila persistente, consultar o Smart Grid pelo
`scan_id`, usar os IDs DataForSEO retornados e reenviar apenas os pontos que
continuam pendentes. Não há reenvio automático nem criação de novas tarefas
pagas pelo Smart Grid.

O SQLite serializa as operações de criação e atualização em transações. A
unicidade da requisição e dos IDs DataForSEO, mais a imutabilidade de um ponto
final, protege contra concorrência e cobranças acidentais decorrentes de
callbacks repetidos. O n8n ainda é responsável por não submeter novamente uma
tarefa paga quando seu próprio estado persistido já contém o ID DataForSEO.

## Erros HTTP

| HTTP | Significado |
|---|---|
| `401` | Bearer ausente ou inválido |
| `404` | Empresa/pesquisa inexistente dentro do escopo atual |
| `409` | Idempotency key em conflito, ponto divergente, tarefa duplicada ou finalização prematura |
| `413` | Corpo maior que 1 MiB |
| `422` | JSON ou campos inválidos |
| `503` | Token não configurado ou armazenamento indisponível |

As respostas de erro não contêm o token. Não registre o cabeçalho
`Authorization` em proxies, workflows ou logs de depuração.

## Variáveis de ambiente

| Variável | Uso |
|---|---|
| `GEOGRID_EXTERNAL_API_TOKEN` | Segredo Bearer; sem ele, toda a API externa é desabilitada |
| `GEOGRID_EXTERNAL_LANGUAGE_CODE` | Idioma nos metadados n8n; padrão `pt` |
| `GEOGRID_DB` | Caminho do SQLite usado pelo Smart Grid |
| `DATAFORSEO_LANGUAGE_CODE` | Configuração independente do fetcher Live da interface |

Configure o mesmo `GEOGRID_DB` persistente no serviço Smart Grid e assegure que
o backup/volume SQLite inclua também arquivos WAL quando aplicável. Nenhuma
credencial DataForSEO é necessária no serviço Smart Grid para processar os
resultados enviados pelo n8n.

## Chamadas HTTP de exemplo

Os exemplos supõem que a URL interna `http://smart-grid:8000` seja acessível
entre os serviços do Easypanel. Use um segredo armazenado no gerenciador de
credenciais do n8n:

```sh
export GEOGRID_EXTERNAL_API_TOKEN='configure-no-ambiente-do-n8n'
BASE='http://smart-grid:8000'
CLIENT='t2s-demo'

curl -sS -X POST "$BASE/api/external/businesses" \
  -H "Authorization: Bearer $GEOGRID_EXTERNAL_API_TOKEN" \
  -H "X-GeoGrid-Client-ID: $CLIENT" \
  -H 'Content-Type: application/json' \
  -d '{"external_business_id":"landing-business-451","name":"Empresa Exemplo","lat":-23.5505,"lng":-46.6333}'

curl -sS -X POST "$BASE/api/external/scans" \
  -H "Authorization: Bearer $GEOGRID_EXTERNAL_API_TOKEN" \
  -H "X-GeoGrid-Client-ID: $CLIENT" \
  -H 'Content-Type: application/json' \
  -d '{"external_request_id":"landing-submission-2026-00451","external_business_id":"landing-business-451","keyword":"serviço local"}'

curl -sS "$BASE/api/external/scans/8e91211a-9fd8-476f-b6be-145a97aef311" \
  -H "Authorization: Bearer $GEOGRID_EXTERNAL_API_TOKEN" \
  -H "X-GeoGrid-Client-ID: $CLIENT"
```

Os workflows n8n não fazem parte desta etapa; esta documentação define somente
o contrato necessário para construí-los.

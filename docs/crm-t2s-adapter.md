# Adaptador CRM T2S — contrato pendente de documentação oficial

## Situação

O Smart Grid ainda não envia chamadas à T2S. O repositório não contém
documentação oficial da API da conta, endpoint, esquema de autenticação,
identificador de funil/etapa ou IDs de etiquetas. Esses valores não foram
inventados.

A migração `workflows/n8n/sql/003-landing-crm-capability.sql` cria a outbox
durável `geogrid_crm_outbox`. Ela é a fronteira de integração: a recepção do
lead e as mudanças de status registram eventos transacionais, mas não chamam
serviços externos.

## Eventos e deduplicação

| `event_type` | Produzido quando | Uso pretendido |
|---|---|---|
| `lead_received` | Um pedido novo é persistido | Criar/atualizar contato e registrar origem/consentimentos |
| `diagnostic_status_changed` | Estado ou scan ID muda | Atualizar status e associar o protocolo/diagnóstico ao contato |

`event_key` é único para a solicitação e evento lógico; a transação de origem
não pode criar cópias. O futuro consumidor deve enviar `event_key` como chave
idempotente se a T2S oferecer suporte e marcar a linha `delivered` apenas após
uma confirmação documentada. Não faça retry cego de uma chamada cujo resultado
seja ambíguo.

## Payload normalizado atual

```json
{
  "event_type": "lead_received",
  "source": "zuri_smart_grid_landing",
  "campaign_id": "evento-configurado",
  "contact": {
    "name": "Nome informado",
    "email": "pessoa@example.invalid",
    "whatsapp": "+5511999999999"
  },
  "company": {
    "name": "Empresa informada",
    "city": "Cidade",
    "state": "SP",
    "google_maps_url": null
  },
  "consent": {
    "privacy_acknowledged": true,
    "privacy_policy_version": "versao-configurada",
    "marketing_consent": false,
    "recorded_at": "timestamp RFC3339"
  },
  "diagnostic": {
    "request_id": "UUID",
    "scan_id": null,
    "status": "queued"
  },
  "labels": ["smart-grid", "zuri", "local-diagnostic"],
  "pipeline_mapping_key": "CONFIGURE_FROM_T2S_DOCUMENTATION",
  "stage_mapping_key": "CONFIGURE_FROM_T2S_DOCUMENTATION"
}
```

O payload contém dados pessoais e deve ser acessível somente ao serviço
adaptador autorizado. Não copie o conteúdo para logs, execução n8n ou alertas.
Configure retenção/expurgo e base legal para o tratamento antes de ativar um
consumidor.

## Mapeamentos ainda necessários

Obter da T2S/administração antes de implementar o cliente:

- URL base e versão atual da API;
- autenticação, escopos e política de rotação de credenciais;
- operação oficial de upsert de contato e chave de deduplicação externa;
- IDs válidos de origem, etiquetas e funil/etapas;
- operação de associação do identificador externo do diagnóstico;
- estados/status aceitos e regras de transição;
- limites de taxa, idempotência, paginação e códigos de erro;
- forma oficial de adicionar links/arquivos PDF;
- requisitos de opt-in, revogação e sincronização da preferência comercial.

Placeholders com a chave `CONFIGURE_FROM_T2S_DOCUMENTATION` são rótulos
semânticos internos, não IDs aceitos pela API. Não configure um workflow HTTP
de CRM até que os itens acima estejam confirmados e testados contra uma
instância sandbox/mock aprovada.

## Integração posterior

O adaptador deverá:

1. Reivindicar outbox com transação e lease, sem fazer SELECT/UPDATE separados.
2. Enviar somente dados necessários e respeitar `marketing_consent`.
3. Usar timeout limitado e retry com backoff somente quando a semântica de
   idempotência da T2S for conhecida.
4. Persistir o ID de contato T2S e o resultado sanitizado.
5. Não misturar o envio de comunicação comercial com o envio transacional do
   diagnóstico; WhatsApp/e-mail requerem templates, autorização e contratos
   de provedor aprovados.
6. Tratar resposta ambígua como reconciliação pendente, não como sucesso
   presumido.

Para homologação atual, substitua o consumidor por um mock local que aceita o
payload, deduplica por `event_key` e devolve um `crm_contact_id` sintético. Não
use credenciais nem endpoints T2S reais.

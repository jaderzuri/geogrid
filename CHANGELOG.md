# Changelog

Alterações relevantes do Smart Grid by ZURI. As entradas abaixo descrevem o
worktree preparado para homologação; ainda não foram publicadas em commit.

## Não lançado — homologação integrada

- Preservada a interface Smart Grid Live, com idioma DataForSEO configurável,
  estados de resultados/falhas e cobertura.
- Adicionadas API externa, Landing Page, consulta de status e relatório
  protegido.
- Adicionadas fila PostgreSQL e seis workflows n8n para recepção, processamento
  Standard Normal, recuperação, status, relatório e outbox CRM.
- Adicionados testes automatizados e Compose isolado com DataForSEO, Places e
  CRM simulados.
- Regressão atual: 204 testes aprovados.
- Homologação integrada de 500 pedidos não aprovada: respostas HTTP 502;
  650 pedidos persistidos, com 623 concluídos, 20 para revisão manual e 7
  submissões incertas.
- Restart com tarefa pendente ainda não homologado. T2S real não conectado.
- Documentação de progresso e retomada registrada em `SESSION_HANDOFF.md`.

# Monitoramento de integrações

## Objetivo

Rastrear chamadas reais a serviços externos, agrupar falhas repetidas, pedir uma triagem objetiva à
IA e acionar o responsável sem expor CPF, telefone, e-mail, tokens ou chaves. O monitor nunca pode
derrubar o fluxo principal: falha de persistência, IA ou notificação apenas gera log operacional.

## Fluxo

1. A integração chama `record_success()` ou `record_failure()`.
2. Cada chamada grava um `ValidationCheck` append-only.
3. Falhas com a mesma impressão digital atualizam um único `IntegrationIncident` aberto.
4. Ao atingir `failure_threshold`, uma task do Django-Q executa triagem por IA.
5. O resumo sanitizado é enviado ao telefone/e-mail operacional configurado.
6. Um sucesso posterior resolve automaticamente os incidentes daquela operação.

CPFHub, ViaCEP e notify-server já usam esse fluxo. IA continua com a telemetria específica de
`AiCall`; Asaas conserva seus checks financeiros próprios.

## Configuração

```env
INTEGRATION_FAILURE_THRESHOLD=2
INTEGRATION_AI_TRIAGE_ENABLED=true
INTEGRATION_ALERT_PHONE=55DDDNUMERO
INTEGRATION_ALERT_EMAIL=operacao@example.com
INTEGRATION_AUTO_ACTIONS_ENABLED=false
INTEGRATION_AUTO_PURCHASE_ALLOWLIST=
```

`INTEGRATION_AUTO_ACTIONS_ENABLED` é o kill-switch global e nasce desligado. Nunca inclua secrets no
contexto passado ao monitor; ainda assim, o sanitizador remove padrões comuns antes de persistir.

## Compra mínima

Uma compra exige simultaneamente:

1. `IntegrationAutomationPolicy.auto_purchase_enabled=True` para o provedor.
2. Saldo menor que `minimum_balance`.
3. `purchase_amount` positivo e dentro de `daily_limit`.
4. Provedor em `INTEGRATION_AUTO_PURCHASE_ALLOWLIST`.
5. Kill-switch `INTEGRATION_AUTO_ACTIONS_ENABLED=true`.
6. Executor registrado com `register_purchase_executor()` e idempotência suportada pelo provedor.

Por padrão, `requires_approval=True`: o sistema cria uma `IntegrationAction` pendente e o staff aprova
via `POST /api/v1/staff/integrations/actions/{external_id}/approve`. Sem executor registrado, a ação
fica `blocked` e nenhum dinheiro é movimentado.

## Operação

- `GET /api/v1/staff/integrations`: checks e quantidade de incidentes abertos.
- `GET /api/v1/staff/integrations/incidents`: histórico operacional sanitizado.
- `POST /api/v1/staff/integrations/incidents/{external_id}/resolve`: resolução manual.
- `GET /api/v1/staff/integrations/actions`: compras propostas e resultados.

Para o próprio notify-server, o incidente é persistido e aparece no painel, mas não tenta alertar pelo
canal quebrado. Antes de produção plena, configure um canal fora do notify-server para esse caso.

# Matrícula Supletivo — contrato de produção

## Evento de conversão

O aluno deixa de ser `lead` e passa a ter `enrollment` somente quando o pagamento é confirmado pelo webhook.

Dentro da mesma transação, `lead.mark_paid`:

1. marca `Checkout.is_paid=true`;
2. muda `Lead.status` para `paid`;
3. credita a comissão do promotor ativo;
4. cria uma única `Enrollment` no polo herdado;
5. promove a role do usuário para `enrollment`.

O processamento é idempotente. Reentrega do webhook não duplica matrícula nem comissão. Falha do efeito principal propaga erro para o gateway repetir; falha da bolsa automática do promotor fica isolada em savepoint.

## Pix

- O checkout do lead pode ser pago por Pix ou cartão.
- O aluno não informa nem possui `pix_key` no contrato da matrícula.
- Chave Pix pertence ao fluxo do promotor para recebimento de comissões.
- As duas parcelas da taxa de matrícula são operações internas do coordenador e nunca viram etapa do aluno.

## Documento de identidade

- Matrícula aceita somente RG: RG antigo ou CIN.
- CNH não é aceita nem na matrícula nem no documento pessoal pós-liberação.
- O aluno envia RG inteiro ou frente + verso.
- Captura completa libera endereço imediatamente; OCR e validação detalhada continuam em segundo plano.
- Número, órgão emissor, filiação, naturalidade, nacionalidade e estado civil são extrações best-effort. Campo ausente nunca é solicitado ao aluno e nunca bloqueia o fluxo.

## Recuperação assíncrona

Reprovação de RG, comprovante ou selfie cria um `ValidationBlock`. O frontend consulta `/enrollment/me` a cada 6 segundos e devolve o wizard à etapa correta, preservando tudo que já foi preenchido.

A selfie pode ser enviada enquanto as análises terminam, mas a coleta só muda para `awaiting_release` quando:

- selfie está aprovada, ou foi encaminhada ao encontro presencial após o limite de tentativas;
- RG está aprovado;
- comprovante de endereço está aprovado;
- não existe bloqueio ativo de RG, comprovante ou selfie.

## Notificações do momento

| Momento | Evento | Destinatário |
|---|---|---|
| pagamento confirmado | `lead.paid` | aluno |
| recibo disponível | `lead.paid.receipt` | aluno |
| nova matrícula paga | `lead.paid.coordinator` | coordenador |
| indicação convertida | `lead.paid.promoter` ou `.scholarship` | promotor |
| RG aprovado/reprovado | `enrollment.rg_approved` / `.rg_rejected` | aluno |
| RG em dúvida | `enrollment.rg_in_review` | coordenador |
| comprovante reprovado | `enrollment.address_proof_rejected` | aluno |
| selfie aprovada/reprovada | `enrollment.selfie_approved` / `.selfie_rejected` | aluno |
| coleta pronta | `enrollment.awaiting_release` | coordenador |
| matrícula liberada | `enrollment.released` + `.credentials` | aluno |

As notificações são best-effort e ficam fora da transação do pagamento: falha de WhatsApp/e-mail não desfaz comissão nem matrícula.

## Evidência automatizada

- suíte backend: `324 passed`;
- E2E prova `register → pagamento → enrollment → RG sem PATCH → endereço → estudos → selfie → student`;
- teste dedicado prova que o prompt do documento pessoal rejeita CNH;
- frontend Next.js 16.2.7 compilado com TypeScript e 17 rotas geradas.

# Notificações do funil V7M — auditoria de produção

## Onde cada parte vive

- Disparo e regra de momento: `users/roles/**/service.py`.
- Textos de fallback: `users/roles/notifications.py`.
- Catálogo versionado: `notify/seed/templates.md`.
- Fonte ativa em produção: banco do `notify-server`, conta `supletivo`.
- Entrega e histórico: `notify.Notification` no CT `30114` (`notify`).
- WhatsApp: número configurado no `notify-server`, entregue pela Evolution GO.
- E-mail: identidade configurada no `notify-server`.

O catálogo versionado precisa ser sincronizado com o banco remoto. Alterar apenas o Python ou apenas o
Markdown não muda o texto já ativo no `notify-server`.

## O que ocorreu no E2E real de 22/07/2026

| Momento | Evento | O que aconteceu | Auditoria |
| --- | --- | --- | --- |
| Cinco fotos recusadas | `candidate.selfie_rejected` | WhatsApp enviado em cada tentativa; e-mail ignorado | Coerente, mas repetitivo e sem explicar que o cadastro continuava salvo |
| Documento aprovado depois da quinta recusa | `candidate.document_approved` + `training.approved` | O documento completou as validações e a regra de contingência promoveu o candidato | A mensagem “continue o preenchimento” ficou falsa porque a promoção já tinha ocorrido |
| RG enviado como comprovante | `enrollment.address_proof_rejected` | O motivo técnico da IA substituiu toda a mensagem | Incorreto: evento de matrícula usado no candidato e texto bruto, sem orientação humana |
| Foto real aprovada | `training.approved` + `candidate.selfie_approved` | A promoção foi enfileirada junto da confirmação da foto | Incorreto: “promotor ativo” chegou antes de “continue o preenchimento” |

## Regras corrigidas

1. A confirmação da foto só é enviada quando ainda existem análises pendentes. Se a foto conclui o
   cadastro e promove imediatamente, somente a mensagem de promoção é enviada.
2. A rejeição do comprovante usa `candidate.address_proof_rejected`, preserva o template humano e passa
   o motivo da IA apenas no campo `{detail}`.
3. Documento aprovado não promete que ainda há formulário: informa que o aplicativo avançará ou
   concluirá automaticamente.
4. Candidato sem ensino médio completo recebe uma mensagem própria de promoção, explicando os marcos
   reais de 3 matrículas pagas para efetivar a bolsa e 10 para cumprir o requisito de indicações da prova.
5. Cada indicação paga mostra o progresso real da bolsa enquanto o promotor estiver nos marcos 1–2 e
   4–10. No terceiro pagamento, uma mensagem especial confirma a efetivação da bolsa.
6. “Fechamento de sexta” virou “próximo fechamento semanal”, porque o dia e horário são configuráveis.
7. O OTP identifica a V7M, informa validade e orienta a não compartilhar o código.

## Matriz completa — candidato e promotor

| Evento | Destinatário | Momento real | Canais efetivos | Estado |
| --- | --- | --- | --- | --- |
| `users.auth.otp` | candidato | código criado para login | WhatsApp | ativo |
| `auth.cpf_conflict` | titular do CPF | tentativa com outro telefone é bloqueada | WhatsApp | ativo |
| `candidate.doc_type_reset` | candidato | coordenador libera troca entre RG e CNH | WhatsApp + e-mail | ativo |
| `candidate.document_rejected` | candidato | IA ou coordenador reprova o documento | WhatsApp + e-mail | ativo |
| `candidate.document_in_review` | coordenador | IA não decide com segurança | WhatsApp | ativo |
| `candidate.document_approved` | candidato | documento é aprovado sem concluir necessariamente o cadastro | WhatsApp + e-mail | ativo |
| `candidate.address_proof_rejected` | candidato | arquivo não comprova o endereço | WhatsApp + e-mail | novo |
| `candidate.selfie_rejected` | candidato | foto não confirma a identidade | WhatsApp | ativo |
| `candidate.selfie_in_review` | coordenador | foto exige decisão humana | WhatsApp | ativo |
| `candidate.selfie_approved` | candidato | foto aprovada, mas documento ou comprovante ainda pendente | WhatsApp + e-mail | ativo e não duplicado |
| `candidate.awaiting_approval` | coordenador | fluxo manual antigo | nenhum | desativado; não possui caller atual |
| `candidate.rejected` | candidato | coordenador rejeita o cadastro | WhatsApp | ativo |
| `training.must_train` | promotor | aprovado, com treinamento obrigatório pendente | WhatsApp + e-mail | ativo |
| `training.must_train.scholarship` | promotor sem médio completo | aprovado, com treinamento e trilha da bolsa | WhatsApp + e-mail | novo |
| `training.approved` | promotor com médio completo | promoção concluída e painel liberado | WhatsApp + e-mail + voz | ativo |
| `training.approved.scholarship` | promotor sem médio completo | promoção concluída, painel e trilha da bolsa liberados | WhatsApp + e-mail + voz | novo |
| `training.cleared` | promotor | treinamento obrigatório concluído | WhatsApp + e-mail + voz | ativo |
| `training.new_material` | promotor | nova matéria obrigatória bloqueia o painel | WhatsApp + e-mail | ativo |
| `lead.captured.promoter` | promotor | nova indicação entra na rede | WhatsApp + e-mail | ativo |
| `lead.paid.promoter` | promotor | indicação paga gera comissão | WhatsApp + e-mail | ativo |
| `lead.paid.promoter.scholarship` | promotor na trilha | indicação paga avança o marco 3/10 | WhatsApp + e-mail | novo |
| `promoter.scholarship_enrolled` | promotor | terceira matrícula paga efetiva a bolsa | WhatsApp + e-mail + voz | novo |
| `student.exam_released` | promotor bolsista/aluno | documentos e requisitos, inclusive 10 indicações, liberam a prova | WhatsApp + e-mail | ativo |
| `promoter.suspended` | promotor | coordenador suspende a atuação | WhatsApp + e-mail | ativo |
| `promoter.reactivated` | promotor | coordenador reativa a atuação | WhatsApp + e-mail | ativo |

## Critérios de produção

- Nenhum texto promete avanço que ainda não aconteceu.
- Eventos operacionais não usam TTS; voz fica restrita a conquista real.
- Mensagens de erro informam o que ocorreu, como corrigir e que os dados continuam salvos.
- Mensagens ao coordenador identificam o candidato pelo nome.
- Idempotency keys continuam estáveis para impedir duplicidade em retry.
- Templates possuem título e assunto quando usam e-mail.
- O histórico deve registrar `sent` ou `skipped` esperado, sem `*_error`.
- O deploy termina com teste unitário, seed parseado, sync remoto e consulta do template ativo.

# API Staff (Admin)

Base: `/api/v1/staff/...` (grupo Ninja `staff`, ver `api/staff.py`).

## Visão geral

`staff` é o SUPERUSER da plataforma — o "boss" (Victor), único dono. Não é uma role de funil
(cliente/promotor/coordenador): é a flag nativa `User.is_superuser` do Django. Todas as rotas do
grupo (exceto `/auth/check` e `/auth/login`) exigem `require_superuser(request.auth)`, que confere
`is_superuser` **no banco** (não nos claims do JWT) e devolve 403 `STAFF_ONLY` se falhar.

Pelo staff, o Victor:
- cadastra polos (hubs) e define quem é coordenador de cada um;
- é o único autor das matérias do treino (LMS) — cria, publica, edita, sobe vídeo, apaga;
- vê e movimenta finanças: saldo Asaas, comissões, fila de payouts, resumo, fechamento semanal,
  pagamento avulso (PIX/boleto) a terceiro livre;
- resolve leads travados (pagamento sem webhook) e apaga usuários de teste (purge);
- gerencia o catálogo de notificações (templates/triggers) sem precisar de deploy;
- acompanha a saúde das integrações (Asaas/WhatsApp/mail/IA/...) e do próprio servidor.

### Login (staff/auth)

Login passwordless por OTP, espelhando o fluxo do cliente mas só enxergando staff — **públicas**
(`auth=None`), são a porta de entrada do app de staff:

| Método/Path | O que faz |
|---|---|
| `POST /api/v1/staff/auth/check` | Acha o staff por `cpf`/`phone`/`external_id` e dispara OTP. **Não vaza** quem é staff: usuário comum (ou inexistente) sai `found:false` igual, mesmo comportamento. |
| `POST /api/v1/staff/auth/login` | Login com `external_id`+`otp` → emite JWT. Usuário existe mas não é superuser → 403 `NOT_STAFF`. OTP errado (mas ainda válido) → 401 `OTP_INVALID`; sem código utilizável (venceu, esgotou tentativas ou já usado) → 401 `OTP_EXPIRED`. |
| `POST /api/v1/staff/auth/refresh` | Refresh do JWT (via `add_auth_refresh`, padrão dos outros grupos). |

Diferença chave do login do cliente: lá o gate é por **role de funil** (um superuser puro cairia em
`NOT_IN_FUNNEL`); aqui o gate é **`is_superuser`**, não role — por isso o staff tem rota própria.

## Gestão de polos (hubs)

| Método/Path | O que faz |
|---|---|
| `POST /hubs` | Cria um polo: `brand` (do catálogo) + `coordinator_external_id` opcional (precisa ser um promotor ativo). |
| `GET /hubs` | Lista todos os polos. |
| `GET /promoters` | Lista promotores (pra escolher quem vira coordenador). |
| `PUT /hubs/{external_id}/coordinator` | Designa/troca o coordenador (um promotor). |
| `PUT /hubs/{external_id}/default` | Marca um polo como PADRÃO (fallback de captação) — único, desmarca os outros. |
| `PATCH /hubs/{external_id}/address` | Preenche o endereço do polo pelo CEP (ViaCEP). O polo nasce sem endereço. |

Erros de borda do módulo `hub` chegam como `Exception` com slug na mensagem (ex.: `hub_not_found`) e
são traduzidos em `_raise_hub_error` para o envelope padrão `{detail, code}`:

| slug | HTTP | `code` |
|---|---|---|
| `hub_not_found` | 404 | `HUB_NOT_FOUND` |
| `coordinator_not_found` | 422 | `COORDINATOR_NOT_FOUND` |
| `coordinator_not_promoter` | 422 | `COORDINATOR_NOT_PROMOTER` |
| `invalid_brand:...` | 422 | `INVALID_BRAND` |
| CEP inexistente | 422 | `CEP_NOT_FOUND` |

`HubOut` devolve `{external_id, brand, coordinator_external_id, is_default}`.

## Gestão de treino (materiais LMS)

Autoria das matérias é **só do staff** (o coordenador vê pelo funil de promotor, mas não autora).

| Método/Path | O que faz |
|---|---|
| `POST /training/materials` | Cria uma matéria (texto + questão + gabarito). Body: `MaterialIn` (schema compartilhado). |
| `PUT /training/materials/{external_id}` | Edita campos enviados; `active=false` desativa. |
| `GET /training/materials` | Lista TODAS as matérias, com gabarito (visão de autoria). |
| `POST /training/materials/{external_id}/publish` | Publica uma matéria **TRANSITÓRIA**: atribui aos promotores já existentes, re-trava a ordem e notifica. (As FIXAS não precisam — entram automaticamente em cada novo promotor aprovado.) |
| `DELETE /training/materials/{external_id}` | Descarta uma matéria **EFÊMERA** (descartável). Matéria não-efêmera: desative via `PUT .../active=false`, não apague. |
| `POST /training/materials/{external_id}/video` | Sobe o vídeo da matéria (multipart, 1 por matéria — substitui o anterior). Formato inválido → 422 `INVALID_VIDEO_TYPE`; matéria inexistente → 404 `MATERIAL_NOT_FOUND`. Salvo em `media/training/`, devolvido como path relativo (front prefixa `/media/`). |

## Finanças

Fonte: `finance/interface/__init__.py` (leitura agregada, read-only) + `finance/interface/manual.py`
(escrita do pagamento avulso) + `finance/interface/commissions.py` (fechamento semanal).

| Método/Path | O que retorna |
|---|---|
| `GET /finance/balance` | Saldo **ao vivo** da conta Asaas (lê o gateway; não move dinheiro). |
| `GET /finance/summary` | `{commissions, payment_requests}`, cada um `{status: {count, total}}` — resumo por status pro cabeçalho do painel. |
| `GET /finance/commissions?status=` | Lista de comissões (`pending\|processed\|paid\|failed`), mais recentes primeiro. Cada item: `{external_id, payee_external_id, payee_role, source_type, amount, status, external_reference, created_at}`. |
| `GET /finance/payouts?status=&kind=` | Fila de saída (`PaymentRequest`); `kind=commission\|fee\|manual`. Cada item: `{external_id, kind, method, amount, status, supplier_name, week_of, scheduled_for, asaas_status, external_reference, boleto_line, receipt, created_at}`. |

### Fechamento semanal

| Método/Path | O que faz |
|---|---|
| `POST /finance/closing/run` | Adianta o fechamento da semana corrente (em vez de esperar sexta 18h). **Idempotente**: beneficiário já fechado é pulado numa re-execução. Devolve o resumo do fechamento (`run_weekly_closing`). |
| `GET /finance/closing/health` | Cruza o **saldo Asaas ao vivo** com a **obrigação estimada** (comissões pendentes da semana + fila de saída ativa: `queued/awaiting_pix/submitted/awaiting_balance`). Devolve `{week_of, pending_commissions, queued_payouts, obrigacao_estimada, saldo, suficiente, deficit}`. Saldo indisponível (sem key/erro de rede) → `saldo/suficiente/deficit = null` + `balance_error`. |

### Pagamento avulso (PIX/boleto) — `POST /finance/payments`

Multipart/form. Enfileira um pagamento a um **terceiro livre** (não precisa ser usuário da
plataforma), pela conta Asaas. Entra na **mesma fila** de comissões/fees — visível depois em
`GET /finance/payouts?kind=manual`. Em produção real, quem efetivamente move o dinheiro é o worker
(Django-Q) via fila money-safe (idempotente + retry); este endpoint só enfileira.

Campos (form): `kind` (`"pix"` ou `"boleto"`), `amount` (obrigatório no PIX, opcional no boleto —
o Asaas lê o valor do próprio boleto), `description`, `supplier_name`, `pix_key` (kind=pix),
`boleto_line` (linha digitável, kind=boleto), `receipt` (arquivo opcional, comprovante).

**Header `Idempotency-Key` é OBRIGATÓRIO** (dinheiro real): um UUID gerado pelo front. A
`external_reference` do pagamento é derivada dessa key (`manual_<sha256[:16]>`) — um retry com a
MESMA key devolve o `PaymentRequest` já criado em vez de disparar um 2º PIX. Sem a key → 422
`IDEMPOTENCY_KEY_REQUIRED` (fail-closed, recusa antes de tocar em qualquer lógica de negócio).

Erros de validação (via `ManualPaymentError`, mapeados em `_raise_manual_payment_error` → 422
`PAYMENT_<SLUG>`):

| slug | quando |
|---|---|
| `invalid_amount` | valor não parseável como decimal |
| `amount_must_be_positive` | valor <= 0 |
| `pix_key_required` | `kind=pix` sem `pix_key` |
| `line_code_required` | `kind=boleto` sem `boleto_line` |
| `kind` inválido | nem `pix` nem `boleto` → `PAYMENT_INVALID_KIND` (raise direto, não passa pelo `ManualPaymentError`) |

Resposta: `{external_id, kind, method, amount, status, external_reference, receipt}`.

## Gestão de leads/usuários

| Método/Path | O que faz |
|---|---|
| `GET /leads?hub=&status=` | Lista TODOS os leads (link de pagamento + comprovante) de todos os polos. `hub` filtra por external_id do polo — se passado e inexistente, 404 `HUB_NOT_FOUND` (não cai silenciosamente em "todos os leads"). |
| `POST /leads/{external_id}/mark-paid` | Staff força o pagamento de um lead (webhook perdido, pagamento confirmado manualmente etc.) — promove lead→enrollment como se o webhook tivesse chegado. Lead sem `payment_id` (nunca teve checkout) → 409 `NO_CHECKOUT`. Lead inexistente → 404 `LEAD_NOT_FOUND`. |
| `DELETE /funnel-user?user_external_id=\|lead_external_id=\|candidate_external_id=\|cpf=\|phone=` | **APAGA por completo** um usuário do funil (lead e/ou candidato) — atômico e **irreversível**. Identifique por exatamente UM dos parâmetros. Cascade leva Profile, Lead+Checkout, Candidate, documentos, matrícula, aluno, OTPs, biometria — libera CPF/telefone para novo cadastro. |
| `GET /users?role=&limit=` | Lista usuários + roles ativas (read-only; mudar role pelo staff ainda não existe). |
| `PUT /users/{external_id}/phone` | Resgate de login: staff troca o telefone de quem perdeu o número/chip e não recebe mais OTP (valida formato + WhatsApp ativo no novo número + unicidade). É a ponta da hierarquia de resgate user→coordenador→staff. |

### `purge` — regras de recusa (importante pro front avisar o Victor)

`DELETE /funnel-user` recusa apagar quando o usuário já "subiu de nível" no funil:

| Situação | HTTP | `code` / `reason` |
|---|---|---|
| Sem nenhum identificador | 422 | `MISSING_FIELD` |
| Usuário não encontrado | 404 | `USER_NOT_FOUND` |
| É staff (`is_superuser`/`is_staff`) | 403 | `PURGE_STAFF_FORBIDDEN` |
| É coordenador de algum hub | 409 | `USER_NOT_PURGEABLE` + `reason: "hub_coordinator"` |
| É promotor (ou já captou leads) | 409 | `USER_NOT_PURGEABLE` + `reason: "promoter"` |
| Promoveu alguma matrícula | 409 | `USER_NOT_PURGEABLE` + `reason: "promoter"` |
| Tem comissões/payment_requests | 409 | `USER_NOT_PURGEABLE` + `reason: "has_finance_records"` |

Sucesso devolve `{user_external_id, deleted: {Model: count, ...}}`. Arquivos de mídia órfãos ficam
no disco (paths com token aleatório, não-enumeráveis) — aceito como custo do caso de uso.

## Visão global (todos os polos)

| Método/Path | O que faz |
|---|---|
| `GET /enrollments?hub=&status=` | Matrículas de TODOS os polos. |
| `GET /students?hub=&status=` | Alunos de TODOS os polos. |
| `PUT /students/{external_id}/platform-credentials` | Staff corrige login/senha (e url/notes) da plataforma de um aluno **já concluído** — só staff mexe depois de concluído (coordenador/bot não podem). Login duplicado → 409 `PLATFORM_LOGIN_TAKEN`. |

## Notificações (staff_notify — sub-router `/notify`)

Fonte: `api/staff_notify.py`, montado em `/api/v1/staff/notify`. `event` é o slug do Template
(único, estável). Fonte de verdade é o banco (`notify.Template`); `notify/seed/templates.md` é só
o seed inicial — edições via este CRUD prevalecem.

### Envio avulso e histórico

| Método/Path | O que faz |
|---|---|
| `POST /notify` | Envia notificação avulsa (WhatsApp e/ou e-mail) a um `user_external_id` (herda phone/email do Profile) OU a destino livre (`phone`/`email` sem cadastro). `channels` opcional (default: todos com destino). Devolve `{external_id}` da notificação enfileirada. |
| `GET /notify/history?caller=&whatsapp_status=&email_status=&tts_status=&limit=` | Notificações enviadas (`Notification`), mais recentes primeiro. `limit` máx 500. |

### CRUD de Template/Trigger

| Método/Path | O que faz |
|---|---|
| `GET /notify/templates` | Todos os Templates + Trigger. |
| `GET /notify/templates/stats` | Dashboard: contagem por flag (`total/active/inactive/with_tts/with_storytelling/with_media`) e por canal. (Rota registrada ANTES de `/templates/{event}` pra `stats` não ser capturada como slug.) |
| `GET /notify/templates/{event}` | Detalhe. Inexistente → 404 `TEMPLATE_NOT_FOUND`. |
| `PUT /notify/templates/{event}` | Upsert completo (`body_md` obrigatório). Invalida o cache em memória na hora. `body_md` vazio → 422 `EMPTY_BODY`; `media_type` fora do catálogo → 422 `INVALID_MEDIA_TYPE`; canal inválido → 422 `INVALID_CHANNELS`. |
| `PATCH /notify/templates/{event}` | Atualização parcial — só altera os campos enviados (`exclude_unset`). Mesmas validações do PUT nos campos presentes. |
| `DELETE /notify/templates/{event}` | Apaga o Template (Trigger junto, cascade OneToOne). `send_event` volta a cair no catálogo in-memory legado. Recupera com `POST /restore-seed`. |
| `PUT /notify/templates/{event}/trigger` | Cria/atualiza o Trigger. **`active=false` é o "interruptor" do Victor** — desliga o evento sem código (`send_event` retorna `None` sem disparar). Template inexistente → 404. |

### Como o Victor liga/desliga um evento

Sem mexer em código: `PUT /notify/templates/{event}/trigger` com `{"active": false}` desliga; com
`{"active": true}` religa. O gate é conferido dentro de `send_event` — quando `active=false`, a
função simplesmente não dispara (retorna `None`).

### DX (utilidades pro front)

| Método/Path | O que faz |
|---|---|
| `GET /notify/events` | Catálogo COMPLETO de eventos conhecidos (DB ∪ in-memory legado) — pro dropdown do form escolher `event` sem adivinhar o slug. Cada item: `{event, has_template, has_in_memory, active}`. |
| `POST /notify/templates/{event}/preview` | Renderiza o `body_md` com um `ctx` opcional (regex render, **sem** chamar IA/LLM) — o texto exato que sairia pro destinatário. Não despacha nada de verdade. Se `storytelling=true`, `story_rendered` vem `null` (o front pode avisar "esse evento gera com IA" sem pagar a chamada). |
| `POST /notify/templates/{event}/test` | Disparo **REAL** (síncrono) do evento pro próprio staff logado (phone/email da sessão) — sem destinatário externo, sem `body_md_override` (o staff vê exatamente o que o Template produz). **Sem** idempotency-key de propósito: cada clique em "testar" deve enviar de novo, não é evento de negócio com risco de duplicação. Evento inexistente (nem DB, nem in-memory) → 404 `EVENT_NOT_FOUND`. |
| `POST /notify/templates/{event}/restore-seed` | Recarrega UM Template a partir de `notify/seed/templates.md`, sobrescrevendo o do banco — desfaz uma edição ruim. Evento fora do seed → 404 `EVENT_NOT_IN_SEED`. Seed ausente no servidor (erro de deploy) → 500 `SEED_FILE_MISSING`. |

## Integrações (health)

Fonte: `integrations/status.py`. Visão read-only de config (só BOOL de presença de env, nunca o
valor do secret) + fluxo declarado + último resultado do ledger `ValidationCheck`.

| Método/Path | O que faz |
|---|---|
| `GET /integrations` | Lista TODAS: `{name, configured, config: {ENV_VAR: bool}, flow, checks}`. Sem rede. Integrações conhecidas: `asaas, infinitepay, whatsapp, mail, ai, biometric, cep, cpf`. |
| `GET /integrations/{name}` | Detalhe. **Asaas** roda `run_checks` AO VIVO (saldo + webhook, bate rede) e devolve em `live`; erro de rede vira `{"error": ...}` em vez de estourar. Demais integrações: só o último do ledger (o teste ao vivo delas roda por command assíncrono, pesado demais pro request). Nome desconhecido → 404 `INTEGRATION_NOT_FOUND`. |
| `POST /integrations/{name}/setup` | Onboarding (só Asaas tem ação real: auto-cadastra o webhook — idempotente). Demais devolvem um `detail` dizendo que não têm setup (config é via `.env`). |
| `POST /integrations/{name}/test` | Teste de saúde ao vivo, carimba o ledger. Asaas roda de verdade; os demais devolvem o último do ledger com uma nota. |

### Saúde do servidor e ledgers

| Método/Path | O que faz |
|---|---|
| `GET /system` | `{db_ok, migrations_pending, qcluster_alive, qcluster_count, queued_tasks, debug, external_url}` — DB vivo, migrações pendentes, se o worker Django-Q está de pé e quantas tasks na fila. |
| `GET /health/full` (auth JWT) | Ping ao vivo de asaas/infinitepay/omniroute/whatsapp + migrações pendentes + info de deploy. Rota separada de `/health` porque `GET /api/v1/staff/health` puro é a liveness PÚBLICA (`build_group`) — registrar aqui a versão autenticada seria sombreada por ela. |
| `POST /health/run-tests` (auth JWT) | Dispara a Actions workflow `ci.yml` no GitHub (via `GH_PAT`/`GITHUB_TOKEN`) para rodar a suíte de testes. Sem token configurado → `{"ok": false, "error": "..."}`. |
| `GET /logs/unrouted?resolved=&limit=` | Eventos que chegaram sem consumidor (fallback rastreável do core). |
| `GET /logs/ai-calls?status=&limit=` | Ledger de chamadas de IA: provider/modelo/operação/custo/latência/erro. |
| `GET /logs/checks?scope=&limit=` | Histórico do ledger de validação (`ValidationCheck`). |

## Códigos de erro relevantes

| `code` | HTTP | Onde |
|---|---|---|
| `STAFF_ONLY` | 403 | qualquer rota, se `is_superuser` falhar |
| `NOT_STAFF` | 403 | login staff, usuário existe mas não é superuser |
| `OTP_INVALID` | 401 | login staff, OTP errado — o código ainda vale, dá pra digitar de novo |
| `OTP_EXPIRED` | 401 | login staff, sem código utilizável (venceu, esgotou tentativas ou já usado) — pedir outro |
| `HUB_NOT_FOUND` | 404 | hub |
| `COORDINATOR_NOT_FOUND` / `COORDINATOR_NOT_PROMOTER` | 422 | hub |
| `INVALID_BRAND` | 422 | criar hub |
| `CEP_NOT_FOUND` | 422 | endereço do hub |
| `INVALID_VIDEO_TYPE` / `MATERIAL_NOT_FOUND` | 422 / 404 | vídeo da matéria |
| `LEAD_NOT_FOUND` / `NO_CHECKOUT` | 404 / 409 | mark-paid |
| `MISSING_FIELD` / `USER_NOT_FOUND` / `PURGE_STAFF_FORBIDDEN` / `USER_NOT_PURGEABLE` | 422/404/403/409 | purge |
| `IDEMPOTENCY_KEY_REQUIRED` | 422 | pagamento avulso, header ausente |
| `PAYMENT_INVALID_AMOUNT` / `PAYMENT_AMOUNT_MUST_BE_POSITIVE` / `PAYMENT_PIX_KEY_REQUIRED` / `PAYMENT_LINE_CODE_REQUIRED` / `PAYMENT_INVALID_KIND` | 422 | pagamento avulso |
| `PLATFORM_LOGIN_TAKEN` | 409 | credenciais de plataforma do aluno |
| `PHONE_INVALID` / `PHONE_NOT_ON_WHATSAPP` / `PHONE_EXISTS` | 422/422/409 | troca de telefone |
| `TEMPLATE_NOT_FOUND` / `EMPTY_BODY` / `INVALID_MEDIA_TYPE` / `INVALID_CHANNELS` | 404/422/422/422 | templates |
| `EVENT_NOT_FOUND` | 404 | test-send |
| `EVENT_NOT_IN_SEED` / `SEED_FILE_MISSING` | 404 / 500 | restore-seed |
| `INTEGRATION_NOT_FOUND` | 404 | integrações |

## O que o backend ESPERA do frontend

- [ ] **`Idempotency-Key` obrigatório em `POST /finance/payments`.** Gerar um UUID novo por
  tentativa de pagamento (não por sessão!) e reenviar a MESMA key em qualquer retry/duplo-clique —
  é o que impede um 2º PIX real de sair. Sem o header, o backend recusa de cara (422), então não
  tem como "esquecer" silenciosamente — mas o front deve garantir que a key é estável durante o
  retry e nova a cada novo pagamento.
- [ ] **Confirmação explícita em ações destrutivas/irreversíveis**: `DELETE /funnel-user` (apaga
  cascade, sem undo) e `DELETE /notify/templates/{event}` (perde o teor customizado — só recupera
  se existir no seed). Modal de "tem certeza?" antes de disparar.
- [ ] **Tratar `USER_NOT_PURGEABLE` com o `reason`** (`hub_coordinator`/`promoter`/
  `has_finance_records`) para explicar ao Victor por que não dá pra apagar aquele usuário, em vez
  de só mostrar erro genérico.
- [ ] **`mark-paid` é uma correção manual**, não o fluxo normal — usar como ação explícita de
  "resgate", não expor como botão de primeira linha na lista de leads.
- [ ] **Preview antes de salvar template**: usar `POST /notify/templates/{event}/preview` para
  mostrar o texto renderizado antes de persistir via PUT/PATCH — evita salvar Markdown quebrado
  sem ver o resultado.
- [ ] **`test-send` dispara de verdade** para o próprio staff (WhatsApp/e-mail real) — avisar que
  vai chegar uma mensagem, não é só simulação (diferente do `preview`).
- [ ] **`run_closing` é idempotente** — pode expor um botão "adiantar fechamento" sem medo de
  re-cobrar quem já foi fechado; mas ainda é uma ação real (gera `PaymentRequest`), não repetir sem
  necessidade.
- [ ] **Saldo (`/finance/balance`, `/finance/closing/health`) é lido ao vivo do Asaas** — pode
  falhar/demorar por rede; tratar `saldo: null` + `balance_error` em vez de assumir número.

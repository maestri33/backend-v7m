# api/clients — funil do aluno (onde o $$ entra)

> Grupo Django Ninja `clients` (`/api/v1/clients/`). Público: o **aluno**, ao longo da vida
> `lead → enrollment → student → veteran`. Casca fina ([CONVENTION](../../../.claude/CONVENTION.md)
> §3): o router valida a borda + gate de role e chama o `interface/` de `lead`/`enrollment`/
> `student`/`auth` in-process. Doc OpenAPI viva: **`/api/v1/clients/docs`** (Swagger) e
> `/api/v1/clients/openapi.json`.
>
> **Organização (Victor 2026-06-07):** a **entrada** (cadastro/login) vive em **`clients/auth/*`**;
> a fase **lead** vira **`clients/lead/*`**. TODO cliente entra obrigatoriamente como `lead`.
>
> ⚠️ **Estado real (não confiar cego — régua: o código de hoje):**
> - **lead** → a lógica foi **provada real** (cartão e PIX), mas no path ANTIGO (`/leads`). Os caminhos
>   mudaram pra `/auth/*` + `/lead/*` em 2026-06-07 → **re-testar no path novo** (Portão 3, com o Victor).
> - **student** (`/student/*`) → ✅ **provado real** fim-a-fim (2026-06-06), via tokens mintados.
> - **enrollment** (`/enrollment/*`) → ⚠️ **NUNCA testado de verdade** (nem in-process completo).
>   O código está escrito, mas no teste do aluno essa etapa foi **pulada no atalho**. Tratar
>   como **não confiável** até rodar com aluno real.

---

## Como o frontend autentica

Não há senha — login é **passwordless por OTP** (código de 6 dígitos no WhatsApp). Fluxo:

1. `POST /auth/check` com `cpf` **ou** `phone` → dispara o OTP no WhatsApp e **diz honestamente** se já
   existe cadastro (`found`) + os papéis. O front decide **cadastro novo × login**.
2. `POST /auth/login` com `external_id` + `otp` → devolve `access_token` + `refresh_token`.
3. Nas rotas autenticadas, mandar o header **`Authorization: Bearer <access_token>`**.

O token carrega os papéis ativos. Trocar de papel (ex.: lead→enrollment ao pagar) **invalida o
token antigo** — o front precisa **refazer o login** (novo OTP) pra pegar o token com o papel novo.

---

## O funil (ordem das chamadas)

```
ENTRADA (pública, clients/auth)            AUTENTICADO (Bearer)
POST /auth/register ──▶ nasce role `lead`   (TODO cliente entra como lead)
  │   login: POST /auth/check → POST /auth/login → Bearer
  ▼
role lead:        GET /lead/me  (estado + a URL ✦ checkout/recibo)  ·  GET /lead/checkout-url
  │   paga (fora) ──▶ webhook confirma ──▶ vira role `enrollment`  (re-login: papel mudou)
  ▼
role enrollment:  GET /enrollment/me → profile → address/cep → address/data
                  → documents/rg + documents/rg/photo (front/back) → education → selfie
  │   (coordenador LIBERA no grupo `leadership`) ──▶ vira role `student`  (re-login)
  ▼
role student:     GET /student/me → blood-type → documents → exam/schedule
                  → pendencies → diploma/pickup ──▶ vira `veteran`
```

> **Dependência cross-grupo:** alguns passos NÃO são do aluno — são do **coordenador**, no grupo
> [[wiki/api/leadership]] (liberar a matrícula, decidir selfie/documento em revisão, corrigir a
> prova, emitir o diploma). O front do aluno **aguarda** esses passos (o `status` muda quando
> acontecem). Ver os estados abaixo.

---

## Rotas públicas — `clients/auth` (entrada do cliente)

### `GET /health`
Liveness do grupo. Resposta `200`: `{ "group": "clients", "version": "1.0", "status": "ok" }`.

### `POST /auth/register` — cadastro (cria o lead + a cobrança)
**TODO cliente entra OBRIGATORIAMENTE como `lead`.** Cria o cadastro mínimo e responde rápido (<2s):
a cobrança no GATEWAY é criada em **task async com retry** (2026-06-11) — o `short_url` já nasce
válido; se o cliente clicar antes do gateway responder, o redirect cria na hora (gateway fora → 503
amigável, o link continua valendo).

- **Body** (`LeadCreateIn`):
  | campo | obrigatório | formato |
  |---|---|---|
  | `cpf` | sim | CPF (com ou sem máscara) |
  | `phone` | sim | telefone BR |
  | `email` | sim | e-mail |
  | `payment_method` | não | `"card"` (ou `"credit_card"`) ou `"pix"`. **Default: `card`** |
  | `ref` | não | `external_id` do promotor que indicou (landing `?ref=`) |
- **Resposta `201`** (`LeadOut`): `{ external_id, status, checkout }` onde `checkout` (`CheckoutOut`):
  `{ payment_method, provider, amount, is_paid, checkout_url?, short_url?, qrcode_payload?,
  qrcode_image?, due_date? }`.
  - **`checkout_url`/`qrcode_*`/`due_date` podem vir `null` no 201** (criação async): use o
    `short_url` (sempre presente) ou re-leia depois em `GET /lead/me`.
  - cartão → `checkout_url`/`short_url`; PIX → `qrcode_payload` (copia-e-cola) + `qrcode_image`.
  - `short_url` = link curto no nosso domínio (`/lead/checkout/<token>` → 302), bom pra WhatsApp.
- **Erros:** `422` `invalid_payment_method`; `400/409/422` de domínio (cpf/phone/email inválido
  ou já existente — `{"detail": "..."}`).
- **Depois de pagar:** o pagamento é confirmado por **webhook** (não pelo front). Ao confirmar, o
  lead vira role `enrollment`. O front descobre no próximo login (ou consultando `GET /lead/me`).

### `POST /auth/check` — dispara o OTP e **VAZA existência**
- **Body** (`CheckIn`): `cpf` **ou** `phone` (um dos dois).
- **Resposta `200`** (`CheckOut`): `{ found, external_id?, otp_sent, otp_wait?, whatsapp?, roles? }`.
  - **VAZA existência DE PROPÓSITO** (CONVENTION §5, regra dura): se NÃO há cadastro → `found=false` +
    `otp_sent=false` (o front sabe que é **cadastro novo**); se existe → `found=true` + `roles` + OTP
    enviado. **NÃO é anti-enumeração** — é a lógica de entrada.
  - `otp_wait` = segundos a esperar se o OTP foi pedido cedo demais.
- **Erros:** `422` `CPF_INVALID` / `PHONE_INVALID` / `MISSING_FIELD`.

### `POST /auth/login` — troca OTP por token
- **Body** (`LoginIn`): `{ external_id, otp }`.
- **Resposta `200`** (`TokenOut`): `{ access_token, refresh_token, token_type }`.
- Resolve o papel **mais avançado** do funil do cliente (`lead→enrollment→student`; `veteran` exige
  `student`) e emite o JWT com TODAS as roles ativas.
- **Erros:** `404` `USER_NOT_FOUND`; `403` não faz parte do funil do aluno; `401` em **dois sabores**:
  - `OTP_INVALID` — errou o código, mas ele **ainda vale**: é só digitar de novo.
  - `OTP_EXPIRED` — **não existe código utilizável** (venceu o TTL, queimou as tentativas ou já foi
    consumido). Tentar de novo aqui nunca passa — o caminho é **pedir outro** (`POST /auth/check`).
  - O front do funil (tela 2) usa a diferença pra escolher a tela: 👀 "confere e digita de novo"
    × ⏳ "esse venceu, já mandei um novo" (com reenvio automático).

### `GET /pricing` — preço (público, fora do `/auth`)
O que o cliente VÊ na landing — **é o MESMO valor que será COBRADO** (Victor 2026-06-07: vitrine = cobrança,
**uma fonte só** no `.env`). Lido do `.env`.
- **Resposta `200`** (`PricingOut`) — exemplo com os valores de **DEV** (PIX R$5 / cartão R$1):
  ```jsonc
  { "pix": "5.00", "card": { "installments": 12, "installment": "0.08", "total": "1.00" } }
  ```
  - `pix` = valor cheio do PIX (reais, **string**) — `.env ENROLLMENT_PRICE_PIX`.
  - `card.total` = valor do cartão (reais) — `.env ENROLLMENT_PRICE_CARD_CENTS` (**CENTAVOS**) ÷ 100;
    `card.installment` = `total ÷ 12`; `card.installments` = `12`.
  - **É a MESMA fonte da cobrança** (`POST /auth/register` cobra exatamente esse valor). Em prod, basta
    pôr o preço real no `.env` → vitrine e cobrança mudam juntas.

---

## Rotas autenticadas — comuns

### `GET /whoami`
Eco do token (debug/sanity): `{ external_id, roles }`. Exige Bearer. `401` sem token válido.

---

## Rotas da fase LEAD — role `lead` (`clients/lead`)

A fase `lead` tem **2 endpoints** (só leitura — o cliente já tem tudo do cadastro; o que falta é pagar).
Exigem Bearer com papel `lead` (`403` se não tiver; `404` se não houver lead).

### `GET /lead/me` — tudo que existe do lead
Resposta `200` (dict):
```jsonc
{
  "external_id": "…",
  "status": "pending | paid | failed",
  "failed_reason": null,            // só quando failed
  "created_at": "ISO-8601",
  "customer": { "name": "…", "phone": "…", "email": "…", "cpf": "…" },   // o próprio cliente
  "promoter": { "external_id": "…", "name": "…" },                       // quem indicou
  "checkout": {                     // null se ainda não há checkout
    "payment_method": "credit_card | pix",
    "provider": "asaas | infinitepay",
    "amount": "5.00",
    "is_paid": false,
    "url": "https://…/lead/checkout/<token>",   // ✦ a URL única: redireciona checkout↔recibo
    "checkout_url": "…",                         // url crua do gateway
    "receipt_url": null,                         // preenchido quando paga
    "qrcode_payload": "…", "qrcode_image": "…", "due_date": null   // só PIX
  }
}
```

### `GET /lead/checkout-url` — só a URL de pagamento/recibo
Resposta `200`: `{ "url": "https://…/lead/checkout/<token>" }` — a MESMA url ✦ do `me`. É o link
curto que `checkout_links.resolve()` roteia: **não pago → gateway; pago → recibo**. `404` se não há
checkout.

---

## Rotas da matrícula — role `enrollment` ⚠️ NÃO TESTADO

Todas exigem Bearer com papel `enrollment` (`403` se não tiver). O `status` é a seção a
preencher AGORA; gates ESTRITOS (2026-06-11): postar seção já concluída → `409`
`{detail, code:WRONG_STATUS, expected_status}` (o front salta pro lugar certo). A seção `rg`
conclui com número + foto da frente + do verso (qualquer ordem).

| Método | Caminho | Body | Resposta |
|---|---|---|---|
| GET | `/enrollment/me` | — | `EnrollmentOut` (ou `404`) |
| POST | `/enrollment/profile` | `{mother_name?, father_name?, marital_status?, birthplace?, nationality?}` | `EnrollmentOut` |
| GET | `/enrollment/address` | — | objeto endereço |
| POST | `/enrollment/address/cep` | `{cep}` | objeto endereço (preenchido via ViaCEP) |
| POST | `/enrollment/address/data` | `{street?, number?, complement?, neighborhood?, city?, state?}` | objeto endereço (só preenche o que está vazio) |
| POST | `/enrollment/documents/rg` | `{number, issuing_agency?, issue_date?}` | `EnrollmentOut` |
| POST | `/enrollment/documents/rg/photo/{slot}` | **multipart** `file=` ; `slot` ∈ `front`/`back` | `{slot, stored}` |
| POST | `/enrollment/education` | `{last_year_studied, last_school, last_year_when?}` | `EnrollmentOut` |
| POST | `/enrollment/selfie` | **multipart** `file=` | `EnrollmentOut` |

- **`EnrollmentOut`**: `{ external_id, status, hub_external_id, selfie_verified, selfie_status }`.
  - `selfie_status` ∈ `pending`/`approved`/`rejected`/`review` — em `review` a IA ficou em dúvida
    e o **coordenador** decide (grupo `leadership`).
- **objeto endereço**: `{ cep, zipcode (alias DEPRECATED), street, number, complement, neighborhood, city, state, country }`.
- **`status`** (enum) → ver [Estados da matrícula](#estados-da-matrícula).
- A **selfie** é verificada por IA: aprovada → avança pra `awaiting_release`; reprovada → o aluno
  refaz; em dúvida → vai pra revisão do **coordenador** (grupo `leadership`).
- **Erros:** `422` (`EnrollmentError` / ViaCEP / etapa errada), `403` (papel), `401` (token).

---

## Rotas do aluno — role `student` ✅ provado real

Todas exigem Bearer com papel `student` (`403` se não tiver).

| Método | Caminho | Body | Resposta |
|---|---|---|---|
| GET | `/student/me` | — | objeto aluno (ou `404`) |
| POST | `/student/blood-type` | `{blood_type}` (ex.: `O+`) | objeto aluno |
| POST | `/student/documents/{doc_type}` | **multipart** `file=` ; `doc_type` ∈ [tipos abaixo] | objeto aluno |
| POST | `/student/exam/schedule` | `{subject, scheduled_at}` (`scheduled_at` ISO 8601) | objeto aluno |
| GET | `/student/pendencies` | — | lista `[{external_id, kind, description, amount_cents}]` |
| POST | `/student/diploma/pickup` | **multipart** `file=` (foto retirando o diploma) | objeto aluno |

- **objeto aluno** (`/student/me` e os POST): `{ external_id, status, hub_external_id, blood_type,
  platform: {url, login, password, notes}, documents: [{doc_type, validation_status, has_photo}],
  pendencies: [{external_id, kind, description, amount_cents, resolved}], diploma: {issued_at,
  picked_up} | null }`.
- **`doc_type`** (enum `StudentDocument.Type`): `military_service` (só homens), `certificate`,
  `transcript`, `blood_type`, `address_proof`, `id_card`, `birth_certificate`.
- **`validation_status`** do documento: `pending` (IA processando) · `approved` · `rejected`
  (o aluno refaz) · `review` (a IA ficou em dúvida → o **coordenador** decide).
- **`diploma/pickup`** é o fim da linha: vira **veteran** (papel somado) e dispara a comissão do
  coordenador.
- **Erros:** `422` (`StudentError`, ex.: `invalid_doc_type`, etapa errada), `403`, `401`.

---

## Tabelas de valores (enums)

### Estados da matrícula
`EnrollmentOut.status` = a seção a preencher AGORA (2026-06-11): `started` (= perfil) →
`address` → `rg` → `education` → `selfie` → `awaiting_release` (espera o coordenador) →
`completed`. Selfie reprovada/em revisão NÃO avança (fica `selfie`; `selfie_status` diz o porquê).

### Estados do aluno
`student.status` ∈ `awaiting_documents` → `documents_under_review` → `exam_released` →
`exam_scheduled` ⇄ `exam_failed` → `awaiting_documentation_dispatch` ⇄ `pending` →
`awaiting_diploma_issuance` → `awaiting_pickup` → `veteran`.

### Tipo sanguíneo
`A+` `A-` `B+` `B-` `AB+` `AB-` `O+` `O-`.

### Status do lead
`pending` → `paid` / `failed`.

---

## Padrão de erros — envelope `{detail, code, …extra}` (proposta API #5, 2026-06-12)

**TODO 4xx/5xx sai com `code`** — o front roteia por `switch(code)`, nunca parseando `detail`.
O registry completo (tabela code → quando → extras) está na **descrição do grupo no OpenAPI**
(`GET /api/v1/clients/openapi.json` → `info.description`). Destaques:

- `WRONG_STATUS` (409) + `expected_status` (e `missing_fields` quando faltam campos do RG/perfil)
  — o front roteia o wizard com isso.
- Validação do body → `422 {"detail": [lista pydantic], "code": "VALIDATION_ERROR"}`.
- `401 UNAUTHORIZED` (sem token/expirado/role trocada) · `SESSION_EXPIRED` (refresh vencido) ·
  `403 FORBIDDEN_ROLE`/`NOT_IN_FUNNEL` · `*_NOT_FOUND` (404) · `SLOT_INVALID` (422) ·
  `RATE_LIMITED` (429, + `retry_after_s`).

## Contrato unificado das análises + resposta canônica (propostas #2/#3/#4, 2026-06-12)

- **`analysis_status`** (`pending|approved|rejected|review`) + **`analysis_reason`** = o nome
  canônico ÚNICO da análise por IA em RG **e** selfie. `validation_status`/`selfie_status`/
  `status` (selfie)/`description` seguem como **alias deprecated** até o front migrar.
- **TTL**: `pending` que estoura `ANALYSIS_TTL_SECONDS` (default 120s) **vira `review`** nas
  leituras do funil — nunca "analisando…" eterno. Uploads (foto RG, selfie) respondem o **ack**
  `{analysis_status, poll_after_ms, expires_at}`: o front polla até `expires_at` e para.
- **Toda mutação devolve o `EnrollmentMe` canônico** (PATCH rg, POST/PATCH address, POST
  education, POST selfie) — mesmo shape do `GET /me`, que ganhou os blocos completos `address`
  (com `missing_fields`) e `selfie`. Zero re-fetch pra rotear. Upload de foto devolve o ack.
- **Gates** (#9/#10): `POST /selfie` exige RG com frente|inteira **aprovada** e
  `missing_fields` vazio → senão `409 WRONG_STATUS expected_status:"rg"`; a virada
  selfie→awaiting_release também trava com campos faltando (o PATCH rg destrava).
- **Push na resolução** (#11): aprovado/reprovado (IA ou coordenador) notifica o aluno por
  WhatsApp + e-mail com deep-link de volta (`FRONTEND_URL` + `ENROLLMENT_RESUME_PATH`).

---

## Pontos abertos / decisões pendentes (não documentar como pronto)

- ⚠️ **lead re-estruturado, ainda NÃO re-testado** nos paths novos (`/auth/*`, `/lead/*`) — a lógica
  é a mesma já provada, mas o contrato mudou; precisa do E2E (PIX/cartão) de novo, com o Victor.
- ⚠️ **enrollment não testado** — os 9 endpoints de `/enrollment/*` nunca rodaram de verdade.
- ✅ **PK do endereço resolvido na borda** (Victor 2026-06-07) — a borda Ninja (clients/
  collaborators) usa `address_iface.as_public_dict` (sem `id`); as views DMZ legadas seguem com
  `as_dict` (intactas). `Address` ainda não tem `external_id` próprio — o front acessa o endereço
  pelo contexto do user logado (não precisa de id próprio).
- ✅ Sinal de "próximo passo" resolvido (2026-06-12): `status` canônico + `missing_fields`
  por seção no `/me` + `expected_status` nos 409 — o front roteia sem mapear nada à mão.

## Ver também

- [[wiki/api/ninja]] — visão geral dos 4 grupos · [[wiki/api/leadership]] — o que o coordenador faz no funil.
- Domínio: [[wiki/users/student]] · `users/roles/{lead,enrollment,student}` (a regra mora no `service`).

# Mudanças no backend que o FRONTEND precisa acompanhar

> Nota viva. O backend muda aqui primeiro; cada item abaixo é algo que o app/landing
> precisa ajustar. O Victor repassa pro time de frontend. Datas em AAAA-MM-DD.

## 2026-06-21 — Passo "educação" virou ESTRUTURADO (breaking)

`POST /enrollment/education` **não aceita mais** `last_year_studied` (texto livre). Novo payload:

```json
{
  "level": "fundamental",   // "fundamental" | "medio"
  "grade": 9,                // fundamental: 1–9 | médio: 1–3
  "completed": true,         // concluiu o nível?
  "last_school": "Escola Estadual X",
  "city": "Curitiba",        // cidade da escola
  "state": "PR",             // UF (2 letras)
  "last_year_when": "2010"   // opcional
}
```

- `GET /enrollment/education` e o bloco `education` do `GET /enrollment/me` devolvem esse mesmo shape.
- Erros (HTTP 422): `EDUCATION_LEVEL_INVALID` (nível inválido); `EDUCATION_GRADE_OUT_OF_RANGE`
  (série fora da faixa do nível — vem com `{min, max}` no `extra`).
- UI sugerida: dropdown nível → dropdown série (muda 1–9 vs 1–3) → toggle "concluí" → escola + cidade/UF.

## 2026-06-21 — API de LIDERANÇA (coordenador) publicou o contrato (OpenAPI tipado)

Antes vários GET devolviam dict solto (`{"description":"OK"}`); agora todos têm `response=<Schema>`
publicado no OpenAPI (`/api/v1/leadership/docs` e `/openapi.json`). Shapes reais (snake_case):

- **`GET /leads`** → `list[HubLeadRowOut]`: `external_id, status, name, phone, promoter_external_id, payment_link, receipt_url` (⚠️ **não** tem cpf/email — esses só no detalhe, aninhados em `customer`).
- **`GET /leads/{id}`** → `HubLeadDetailOut`: `external_id, status, failed_reason, created_at, customer{name,phone,email,cpf}, promoter{external_id,name}, checkout{payment_method,provider,amount,is_paid,url,receipt_url,...}`.
- **`GET /enrollments`** → `list[HubEnrollmentRowOut]`: `external_id, name, phone, status` (REAL, sem máscara), `fees{...}`, `created_at`.
- **`GET /promoters`** → `list[HubPromoterRowOut]`: `external_id, name, status, locked`.
- **`GET /candidates`** → `list[CandidateAwaitingOut]`: `external_id, name, since, rejected`.

### `GET /reviews` — NORMALIZADO (tela-âncora)
Top-level = objeto de 7 baldes (`enrollment_rg, enrollment_selfie, candidate_document, candidate_selfie, student_documents, candidates_awaiting_approval, locked_promoters`). **Cada item agora é `ReviewItemOut` homogêneo**: sempre `external_id` + `type` + `kind` + extras, em vez do nome-de-id-que-muda-por-balde de antes.
- `type`: `enrollment | candidate | student | promoter`
- `kind`: `rg | selfie | document | awaiting_approval | locked_training`
- `external_id` = o id do recurso a decidir. Casos especiais: `student_documents` traz também `student_external_id` + `document_external_id`; `locked_promoters` traz `promoter_external_id` + `pending_materials`.
- O front roteia por `type`+`kind` e linka por `external_id`.

### NOVO `GET /students?status=&limit=&offset=` (A2)
Antes o coordenador só tinha `GET /students/{id}`. Agora lista alunos do polo: `response=PaginatedOut {items:[HubStudentRowOut{external_id,name,phone,status,created_at}], total, limit, offset}`.

### Catálogo de codes (A3) + paginação (A5)
A `description` do grupo leadership agora lista ~46 codes (`NOT_HUB_COORDINATOR, WRONG_STATUS, FEES_INCOMPLETE, RG_NOT_IN_REVIEW, SELFIE_NOT_IN_REVIEW, RATE_LIMITED, EDUCATION_*`, etc.) com status e extras — `switch(code)`. `/students` usa `limit/offset/total`; demais listas seguem arrays diretos.

### Gating/Login (cravado p/ o front)
- Role do coordenador = **`"coordinator"`** (não `hub_coordinator`); mas o gate DURO é coordenar um Hub (`Hub.coordinator_id == user.id`, senão 403 `NOT_HUB_COORDINATOR`).
- Login **por área**: existe `/leadership/auth/check` (dispara OTP) e `/leadership/auth/login` (exige coordenar hub). O token do collaborators **não** é o caminho pro leadership — criar handlers `/api/leadership/auth/*`.
- `is_coordinator` (do `/auth/check`) = `hub_iface.coordinated_by(user) is not None`.

- `GET /enrollment/documents/rg` agora devolve `analysis_status: null` quando **não há foto enviada**
  (antes vinha `"pending"` e o front ficava preso em "Lendo seu documento…").
- Regra pro front: `analysis_status == null` → mostrar o **formulário de upload**;
  `"pending"` → "analisando…"; `"approved"`/`"rejected"`/`"review"` → como já era.

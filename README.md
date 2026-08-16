# backend — monólito Django (MVP)

O **cérebro** da plataforma: toda a lógica de negócio + o banco moram aqui. A **API pública
vive DENTRO do Django, via Django Ninja** (in-process), em 4 grupos por público sob
`/api/v1/` — sem serviço separado, sem hop HTTP (edges FastAPI foram descartados 2026-06-01).
Arquitetura completa nas instruções do projeto (`.claude/CONVENTION.md`).

## Rodar em dev

```bash
cd backend
uv sync
uv run python manage.py migrate
uv run python manage.py runserver
```

Config em `backend/.env` (não versionado). Dev usa SQLite.

## Estado

- **Bootstrap (step 0):** Django sobe, migra, `/admin/` no ar. → [[wiki/core/bootstrap]]
- **integrations/asaas (§4 item 1-a):** gateway de pagamento — fundação + status/onboarding +
  webhook receiver + validação de saque. Faltam charge e payout. → [[wiki/integrations/finance/asaas]]
- **integrations/infinitepay (§4 item 1-b):** gateway de checkout — link de pagamento + webhook que
  reconfirma via payment_check. E2E real aprovado (pagou R$1 → PAID). → [[wiki/integrations/finance/infinitepay]]
- **core/fallback:** logger rastreável de evento sem destino (usado pelo webhook do asaas).
  → [[wiki/core/fallback]]
- **core/validation:** registro de validações (flags + horário) dos testes que rodamos, mostrado no
  `/status/` de cada integração. → [[wiki/core/validation]]
- **integrations/tools/cep (§4 item 1):** tool de CEP — lookup ViaCEP (API pública, sem api-key).
  Cliente async; o app `address` consome depois. → [[wiki/integrations/tools/cep]]
- **integrations/tools/cpf (§4 item 1):** tool de CPF — lookup CPFHub.io (api-key `x-api-key`,
  server-side). Cliente async; o app `profiles` consome depois. → [[wiki/integrations/tools/cpf]]
- **integrations/tools/biometric:** biometria facial — face-match documento × selfie (InsightFace
  `buffalo_l`, CPU; pesos fora do repo). Persiste templates no perfil + auditoria; "somado" à validação
  de selfie por IA nos funis `candidate`/`enrollment`. Provado real. → [[wiki/integrations/tools/biometric]]
- **integrations/communication/whatsapp (§4 item 1):** cliente WhatsApp (Evolution GO) — contrato
  completo, async, com resolução do 9º dígito BR. O app `notify` consome depois.
  → [[wiki/integrations/communication/whatsapp]]
- **integrations/ai (§4 item 1):** engine LLM multi-provider OpenAI-compatible + fallback (DeepSeek,
  DashScope, Groq, OpenAI, OpenRouter, NVIDIA). Interface in-process (`service.py`) + auditoria
  `AiCall`; somar provider é só `.env`. O `training` consome depois (correção). → [[wiki/integrations/ai]]
- **integrations/communication/mail (§4 item 1):** cliente de email (SMTP STARTTLS:587) + validador
  (formato/MX) + templates HTML. Porte do legado, async; envia inclusive imagem por URL. O `notify`
  consome depois. → [[wiki/integrations/communication/mail]]
- **notify (§4 item 2):** despachante multi-canal in-process (WhatsApp + e-mail + voice-note/TTS).
  Model de auditoria `Notification` + envio async (Django-Q); dispatcher puro (o caller passa o
  contato), conteúdo pronto do caller, **envia mídia/imagem** (WhatsApp pela LAN, e-mail pela URL
  pública), falha isolada por canal. **3 canais provados REAIS.** → [[wiki/notify/notify]]
- **users (§4 item 3):** identidade da plataforma — custom `User` (`external_id`), `profiles`
  mínimo (unicidade cpf/phone/email), `jwt` (RS256 + JWKS), `otp` (login passwordless por WhatsApp),
  `roles` (catálogo no `.env` + histórico). register/check/recover/login (DMZ). **E2E real
  aprovado** (register→OTP no zap→login→JWT). → [[wiki/users/users]]
- **users/address (§4 item 3, ciclo 3b):** entidade de endereço (DMZ) — GET/CEP(ViaCEP)/PATCH; nasce
  vazio no provisionamento. → [[wiki/users/address]]
- **users/documents (§4 item 3, ciclo 3b):** RG/CNH/certidão/militar (1-1, null no cadastro) + upload
  de foto + gate de gênero no militar. → [[wiki/users/documents]]
- **finance (§4 item 4):** motor de comissão/payout — creditar (valor do `.env`) → fechamento
  semanal (bônus FLAT ≥ threshold) → solicitação de pagamento → **PIX real** via `asaas.payout`, com
  validação externa e reconciliação. FK→`users.User`, pix do profile. **Fechado e provado com dinheiro
  real**: comissão (−R$1) e `fees` (despesas via PIX QR — imediato, dinâmico e **agendado por vencimento**
  `--at-due`; R$1 + R$1,07 reais). → [[wiki/finance/finance]]
- **hub (§4 item 5, Fatia 1):** o **polo** — entidade `Hub` (Address FK + marca do catálogo `.env` +
  coordenador) + `seed_defaults` (conta-mãe = staff superuser + promoter + coordinator; hub padrão =
  fallback de captação). Criação simples agora (a complexa é futuro). Testado in-process. → [[wiki/hub/hub]]
- **users/roles/student (§4 item 9):** o funil final do aluno — a matrícula liberada vira `student`,
  percorre documentos (IA assíncrona + `review`→coordenador decide) → prova (coordenador corrige) →
  pendências (doc/taxa) → diploma → **retirada (foto) → `veteran`** + **comissão do coordenador do polo**.
  **Testado REAL (Portão 3, 2026-06-06):** fluxo completo por HTTP, IA real, uploads e **PIX real** da
  comissão (−R$3). → [[wiki/users/student]]
- **api/ (§4 item 13):** API pública Django Ninja in-process — 4 grupos versionados
  (`/api/v1/{clients,collaborators,leadership,staff}/`) + auth JWT compartilhada (reusa o
  `users/auth/jwt`). Esqueleto (`health`/`whoami`) + **grupo `staff` com as rotas de hub/coordenador**
  (cria/lista polo, lista promotores, designa coordenador; gate de superuser). Faltam os demais grupos,
  nomes (placeholder), reexpor status. → [[wiki/api/ninja]] · [[wiki/api/staff]]

> Apps de negócio (`users`, `hub`, `notify`, `finance`, `integrations`...) entram um a um,
> pelo `.claude/WORKFLOW.md`.

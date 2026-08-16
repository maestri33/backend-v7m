# notify — despachante multi-canal (§4 item 2)

> **ESTADO:** app de negócio do monólito; **despachante** in-process de notificações (WhatsApp +
> e-mail + voice-note/TTS), **com envio de mídia/imagem**. **Testado REAL** (2026-06-01): os **3
> canais** entregues no aparelho/e-mail do Victor numa passada. Consome `integrations/communication/
> whatsapp` + `…/mail` + `integrations/ai` (TTS) — **não** é integração, **não** expõe endpoint/webhook.

App Django que **orquestra** o envio (o legado `~/coders/backend/notify` era um serviço FastAPI maior;
aqui portamos só o **dispatcher**: contatos/logs-timeline/templates-em-DB/métricas/IA-gera-texto são de
apps futuros ou já viraram `integrations/`). **Dispatcher puro:** o caller passa `phone`/`email` e o
**conteúdo pronto** (notify não tem model de contato — isso é do `profiles`, §4-3). Envio **assíncrono**
(Django-Q), **idempotente**, e **nunca quebra o fluxo do caller** (§12): cada canal é isolado.

## Superfície pública (CONVENTION §3) — `notify/interface/send.py`

Outros apps chamam **só**:

```python
from notify.interface.send import send

send(
    text="...", caller="asaas.charge",
    phone="5543...", email="a@b.com",
    title=None, subject=None,
    whatsapp=True, email_channel=False, tts=False,
    media_url=None, media_type=None,   # mídia por URL pública (auto-detect do tipo pela extensão)
    gender=None,                       # "M"/"F" → voz do TTS (resolvido no integrations.ai)
    mail_template="default",           # slug do mail.templates
    idempotency_key=None,              # mesma key ⇒ devolve a notificação existente (não re-envia)
    run_sync=False,                    # True = despacho inline (testes/commands)
) -> str                              # external_id (handle estável)
```

Persiste a intenção **antes** de enviar (auditoria/idempotência §8), enfileira o despacho no Django-Q
(`transaction.on_commit`) e devolve o `external_id` na hora. Canal pedido **sem destinatário** nasce
`skipped`.

## Model `notify.Notification` (auditoria)

Uma linha = uma notificação (pode atingir vários canais). Campos-chave: `external_id` (UUID, handle),
`idempotency_key` (unique), `caller`, `recipient_phone`/`recipient_email`, `title`/`text`/`subject`,
`mail_template`, **`media_url`/`media_type`**, **`gender`**, `want_*` por canal, **`*_status`**
(`pending`/`sent`/`failed`/`skipped`) e `*_error` por canal, `tts_audio_path`, `attempts`, timestamps
(`America/Sao_Paulo`). Um banco, migração Django.

## Despacho `notify/dispatch.py` (Django-Q, síncrono)

`dispatch(notification_id)` roda os canais ainda `pending`, cada um em `try/except` isolado (§12), e
grava status+erro. Clientes async via `async_to_sync`.

- **WhatsApp via Evolution GO** — com `media_url`: `send_media(num, _to_lan(url), media_type,
  caption=corpo)`; sem mídia:
  `send_text(num, corpo)`. Corpo = `*título*\n\n texto`. Número resolvido por `resolve_br_number` (9º dígito BR).
- **E-mail** — `mail.templates.render(template, title, content=text)`; com mídia, embute pela **URL
  pública** via `text_to_html(text) + media_html(url, type)` e `content_is_html=True`. Envia por
  `mail.client.send_email(...)`.
- **TTS** — `ai.service.tts(text, caller=..., gender=...)` gera o mp3 (`media/ai/audio/...`) →
  `send_whatsapp_audio(num, audio_url)` → `/send/media` com `type=audio` (voice-note/PTT).
  `audio_url` pela **URL LAN**.

### Roteamento de URL de mídia (decisão do Victor)
- **WhatsApp = IP local + caminho** (`MEDIA_LAN_BASE` + `/media/...`) — o Evolution GO
  alcança o arquivo pelo **IP interno**, sem egress/TLS/DNS (porte do `_to_lan` do legado).
- **E-mail = endereço externo** (`EXTERNAL_URL` = `https://dev.m33.live/media/...`) — o cliente de
  e-mail do destinatário busca pela **internet**.
- O áudio do TTS segue o mesmo padrão do WhatsApp (URL LAN).

## Voz por gênero — em `integrations/ai` (não no notify)

`notify` repassa `gender` (M/F); a **resolução gênero→voz** mora no `ai.service.tts` (ordem: `voice_id`
explícito > voz por gênero `ELEVENLABS_VOICE_MALE`/`ELEVENLABS_VOICE_FEMALE` > voz default
`ELEVENLABS_VOICE_ID`). Os defaults M/F **apontam pra voz padrão**, então sem config o TTS não-quebra.

**⚠️ Regra CRUZADA de propósito (Victor):** o destinatário **homem recebe voz de mulher** e a **mulher
voz de homem**. Por isso os valores no `.env` ficam "invertidos" no nome: `ELEVENLABS_VOICE_MALE` = a voz
que o **homem** recebe (voice-id **feminino**), `ELEVENLABS_VOICE_FEMALE` = a que a **mulher** recebe
(voice-id **masculino**). **NÃO "corrigir" a inversão.**

`«PENDÊNCIA»`: setar os 2 voice-ids distintos no `.env` (cruzados, como acima); e a **fonte do gênero**
(hoje o caller passa) virá do `profiles`/`users` (§4-3) quando existir.

## Config (.env) — reusa o existente, zero novo

| Chave | Exemplo | Uso |
|---|---|---|
| `MEDIA_LAN_BASE` | `http://10.1.20.30` | base LAN p/ WhatsApp buscar mídia/áudio |
| `EXTERNAL_URL` | `https://dev.m33.live` | base pública p/ e-mail (mídia) e fallback do áudio |
| `MEDIA_URL`/`MEDIA_ROOT` | `/media/` | onde a mídia/áudio é servida (porta :80 deste host) |
| `ELEVENLABS_VOICE_MALE`/`_FEMALE` | (default = voz padrão) | voz do TTS por gênero |

System check `notify.W001` (**Warning**, não trava): avisa se faltar `MEDIA_LAN_BASE`/`EXTERNAL_URL`.
Os checks `E*` que travam o boot são dos integrations (whatsapp/mail/ia).

## Catálogo de mensagens dos funis (teor + regra de TTS) — Victor 2026-06-05

O **teor** de cada notificação dos funis mora num lugar só: **`users/roles/notifications.py`**
(`_MESSAGES`, `_TTS_EVENTS`). Os serviços só citam a **chave do evento** + passam o nome do
destinatário; o `notify` continua dispatcher puro. **Regras** (na cabeça do arquivo):

- **Toda troca de role notifica os envolvidos** (cada serviço dispara no seu ponto).
- **O 1º nome do destinatário aparece ≥2× em cada mensagem** (calor/proximidade).
- **TTS (voz) só em MOMENTO ESPECIAL** (acolhimento/conquista); o resto é texto.

> Aqui só **citamos** os eventos — o texto exato (editável pelo Victor) está no `notifications.py`.

| Evento (caller) | Destinatário | TTS? | Disparado em |
|---|---|---|---|
| `lead.captured` | lead/aluno | 🔊 voz | `lead/service._notify_captured` |
| `lead.captured.promoter` | promotor | texto | `lead/service._notify_promoter_new_lead` |
| `lead.checkout.pix` / `.card` | lead/aluno | texto | `lead/service._notify_checkout` |
| `lead.paid` | lead/aluno | 🔊 voz | `lead/service._notify_paid` (parabéns; **recibo à parte**) |
| `lead.paid.receipt` | lead/aluno | texto | `lead/service._notify_paid` (comprovante = URL) |
| `lead.paid.coordinator` / `.promoter` | coord. / promotor | texto | `lead/service._notify_paid` |
| `enrollment.awaiting_release` | coordenador | texto | `enrollment/service._notify_coordinator_awaiting` |
| `enrollment.released` | aluno | 🔊 voz | `enrollment/service._notify_released` |
| `candidate.training_started` | candidato | texto | `candidate/service._notify_training_started` |
| `training.awaiting_interview` | coordenador | texto | `training/service._notify_coordinator_interview` |
| `training.approved` | novo promotor | 🔊 voz | `training/service._notify_approved` |
| `student.document_rejected` | aluno | texto | `student/service.apply_validation` (reprova IA) |
| `student.exam_released` | aluno | texto | `student/service._maybe_release_exam` |
| `student.exam_scheduled` | coordenador | texto | `student/service.schedule_exam` |
| `student.exam_passed` | aluno | 🔊 voz | `student/service.grade_exam` |
| `student.exam_failed` | aluno | texto | `student/service.grade_exam` |
| `student.pendency_opened` | aluno | texto | `student/service.open_pendency` |
| `student.diploma_issued` | aluno | texto | `student/service.issue_diploma` |
| `student.veteran` | aluno | 🔊 voz | `student/service.register_pickup` |
| `student.veteran.coordinator` | coordenador | texto | `student/service.register_pickup` (comissão) |
| `hub.coordinator_assigned` | novo coordenador | texto | `hub/interface._ensure_coordinator_role` |

**🔊 voz (TTS) = momentos especiais:** captação, pagamento confirmado, virou aluno, virou promotor,
passou na prova, formou (veteran). Tudo o mais é texto. O `otp` (login) é texto, fora deste catálogo
(template `users/auth/otp/otp.md`).

## Como validar (§8 — chamada real)

```bash
python manage.py mail_health    # confirma a auth SMTP antes do e-mail
python manage.py notify_send --phone 5543... --email a@b.com \
    --title "Teste" --text "olá do notify" \
    --media-url https://dev.m33.live/media/qrcodes/pay_x.png --media-type image \
    --gender M --whatsapp --email-channel --tts
```

Evidência: `.claude/tests/2-notify.md` (3 canais entregues REAIS, aprovado pelo Victor 2026-06-01).

## Rabo pra trás (vira spec/feature nova)
- Gerar-texto-por-IA (`--ai`) e gerar-imagem (`--img`) no envio — adiado até um emissor precisar.
- Contacts/logs-timeline/métricas/templates-em-DB+CRUD+edição-IA/mailcow-admin/API-webhook do legado —
  pertencem a `profiles`/`users` (§4-3), a `integrations/`, ou a uma fatia futura.
- Voz por gênero distinta — depende de voice-ids no `.env` + `profiles` p/ a fonte do gênero.
- Retry/stale persistente sofisticado — só se preciso (hoje: 1 tentativa/passada + re-exec do Django-Q).

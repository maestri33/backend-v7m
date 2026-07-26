# notify → serviço independente multi-tenant (plano de desmembramento)

> **ESTADO: PLANO** (nada implementado ainda). Decisões do Victor incorporadas em 2026-07-17;
> pendências abertas na [última seção](#pendências-abertas-victor). Contexto: o notify hoje é um
> app in-process do monólito ([[wiki/notify/notify]]); a decisão é desmembrá-lo em um **serviço
> próprio, com servidor próprio**, onde cada cliente ("conta") tem **seu número de WhatsApp**, seu
> e-mail (mailcow) e sua voz de TTS. O notify vira **universal** (todos os serviços do Victor,
> dentro da VPN), **comandado pelo staff** — o backend Supletivo é só o primeiro cliente.

## Visão

```
HOJE (in-process)                          DEPOIS (serviço)
─────────────────                          ────────────────
callers (63) ──► notify/interface ──►      callers (63) ──► notify/interface (MESMA assinatura,
  Django-Q ──► dispatch ──► Evolution        vira SDK HTTP) ──► notify-server (LXC própria, VPN)
  / SMTP / ElevenLabs                          ├─ Account "default" ── número+e-mail PADRÃO
  (1 número fixo no .env)                      ├─ Account "supletivo" ── número ── mailcow ── vozes
  Templates no DB do backend                   ├─ Account "outro-app" ── número ── mailcow ── vozes
                                               ├─ Templates/Triggers POR CONTA + painel staff
                                               └─ Django-Q ──► dispatch ──► Evolution / SMTP /
                                                              omnirouter (TTS, 10.1.30.35)
```

O que o serviço **é** (Victor 2026-07-17 — mudou do rascunho anterior): a plataforma de
notificação **universal** da casa — entrega (WhatsApp texto/mídia/voice-note + e-mail), **teor**
(Templates/Triggers editáveis POR CONTA + painel do staff), auditoria por canal, e dona das
instâncias da Evolution (números). Uso só dentro da VPN; nunca exposto à internet.

O que **fica no backend** (e em cada app cliente): perfil/contato (o caller resolve e manda
destinatário pronto), regras de funil, e o momento imperativo de disparar. O bot (inbound
conversacional) segue FORA deste plano — vai sair do backend depois; o desenho só não fecha a
porta pra ele (Fase 3).

## Decisões (Victor 2026-07-17)

| # | Decisão | Racional |
|---|---|---|
| 1 | **Stack:** Django + Ninja + Django-Q + Postgres, **repo novo** (sug.: `notify-server`), LXC própria na DMZ, database própria no Postgres geral (CT 2100). **Infra confirmada pelo Victor** | mesma receita provada do monólito; clients já async/desacoplados portam quase 1:1 |
| 2 | **Templates + painel MIGRAM pro serviço** (Victor: "template muda, painel também — a partir daí notify vai ser universal, não exclusivo de um serviço, do qual staff comanda") | Template/Trigger por conta no DB do serviço; `send_event` vive no serviço; painel staff de teor/histórico/adhoc é do serviço. O catálogo do backend vira só a ORIGEM DO SEED da conta supletivo |
| 3 | **Tenancy: `Account` (conta) com N números** + **conta `default`**: um número e um e-mail "padrão da casa" rodando na instância default; cada app diferente tem SEU e-mail (mailcow) + SEU número | Supletivo = conta própria com o número atual. A default cobre serviços pequenos/internos sem identidade própria |
| 4 | **Inbound:** o serviço persiste o webhook da Evolution (evento bruto); **relay por conta é fase 3** | o bot está morto; ninguém consome inbound hoje. Quando o bot renascer (fora do backend), pluga no relay |
| 5 | **`external_id` gerado pelo CLIENTE** (SDK) e aceito pelo serviço | preserva o contrato de hoje: `send()` devolve o handle NA HORA e nunca bloqueia (§12) mesmo com o serviço fora — o SDK enfileira retry local com o mesmo UUID |
| 6 | **Validação de telefone vira endpoint** (`POST /v1/phone/check`) | o register (`users/auth/service.py`) usa `check_numbers` da Evolution; com o endpoint, o backend zera credencial de Evolution/SMTP/TTS no `.env` |
| 7 | **TTS via omnirouter** (`10.1.30.35`, LAN/DMZ): o serviço chama o omnirouter direto — NÃO embute client de provider. **Vozes: opção (a)** — o notify guarda os voice-ids POR CONTA (regra CRUZADA M/F preservada: homem recebe voz feminina e vice-versa — NÃO "corrigir") e passa `voice` pronto; o omnirouter só executa | keys de provider moram no omnirouter; o notify recebe o áudio e serve o mp3 do próprio MEDIA_ROOT (Evolution busca pela LAN). Demais funções de IA (storytelling, bot, OCR): CASO A CASO |
| 8 | **E-mail por conta no mailcow**: cada conta com caixa/identidade SMTP própria; a default usa o e-mail padrão da casa | isola remetente e reputação por app |
| 9 | **Evolution v2 DEFINITIVA; evolution-go DESCARTADO** (Victor 2026-07-17: "desista do evolution-go, fique no v2 mesmo") | motivo do descarte: licença obrigatória (503 até ativar + heartbeat pro license-server, preço não divulgado) e API não-compatível. A avaliação abaixo fica como registro histórico; a interface `WhatsAppDriver` do notify-server, se já existir, não custa manter — mas v2 é O driver, sem piloto planejado |

### Avaliação evolution-go (2026-07-17 — DESCARTADO; registro histórico)

[github.com/evolution-foundation/evolution-go](https://github.com/evolution-foundation/evolution-go)
— WhatsApp API em Go sobre **whatsmeow** (não Baileys), Swagger, WebSocket/Webhook/AMQP/NATS,
mídia com MinIO/S3 opcional, QR pairing, Docker/binário leve.

- **Postgres:** sim — 2 connection-strings (`POSTGRES_AUTH_DB`, `POSTGRES_USERS_DB`), mensagens
  opcionais no DB. Cabe no Postgres geral (CT 2100) com databases `evogo_*`, e o binário cabe na
  MESMA LXC do notify (ok do Victor).
- **NÃO é drop-in da Evolution v2:** paths (`/send/text`, `/send/media`, `/instance/create`,
  `GET /instance/{name}/qrcode`), formato de resposta e payload de webhook
  (`instanceId`/`instanceToken`/`event`) todos DIFERENTES → client próprio, não o porte do atual.
- **Capacidades críticas CONFIRMADAS (2026-07-17, wiki `guias-api/`):** voice-note = `/send/media`
  com `type: "audio"` — **converte automaticamente qualquer áudio pra Opus (PTT)** (a v2 usa o
  `sendWhatsAppAudio` dedicado — "um pouco diferente", como o Victor ouviu); check de número =
  `POST /user/check` → `IsInWhatsapp` por número (equivale ao `whatsappNumbers` da v2).
- **⚠️ Bloqueio restante: LICENÇA** — API responde 503 até ativar no Manager; heartbeat periódico
  ao license-server = dependência externa de produção; preço/termos não divulgados.
- **Veredito final (Victor 2026-07-17): DESCARTADO** — a licença (custo desconhecido + heartbeat
  externo em produção) não compensa; a casa fica na **Evolution v2** para todos os números.

## Fase 1 — o serviço (repo novo, sem tocar o backend)

### Models
- `Account` — slug, nome, ativo. O tenant. Row `default` = conta padrão da casa (decisão 3).
- `ApiKey` — FK conta, hash (sha256) da chave, label, ativo. Auth `Authorization: Bearer`.
- `WhatsAppNumber` — FK conta, `instance_name` (Evolution v2), slug, default flag, status de
  conexão. A instância atual do Supletivo é ADOTADA, não recriada.
- `MailIdentity` — FK conta, SMTP host/port/user/senha (mailcow), from_name/from_email, timeout.
- `TtsVoices` — FK conta, voice-ids M/F (regra cruzada — decisão 7). Keys de provider: omnirouter.
- `Template` + `Trigger` — porte dos models atuais **+ FK conta** (`event` único POR CONTA).
  Mesmos campos (body_md, subject, is_tts, storytelling/story_prompt, channels, mídia default,
  mail_template, notes; trigger com active/fires_on/source). Cache com TTL 30s porta junto.
- `Notification` — porte do model atual **+ FK conta + FK número usado**. Mesmos status/erros por
  canal, `idempotency_key` (unique POR CONTA), `attempts`, `tts_audio_path`.
- `InboundEvent` — payload bruto da Evolution por instância (idempotente por `wa_message_id`).
  Só armazena no v1 (decisão 4).

### Ports do monólito (copiar quase 1:1)
- `integrations/communication/whatsapp/client.py` — Evolution v2, 9º dígito BR, send_text/
  send_media/send_whatsapp_audio/check_numbers — vira o driver `evolution-v2` atrás da interface
  `WhatsAppDriver` (decisão 9). Config: `.env` → row do número.
- `integrations/communication/mail` — client SMTP + templates HTML. Config: `.env` → `MailIdentity`.
- `notify/interface/{templates,events}.py` — cache de Template + `send_event` (render regex de
  placeholders, canais default, is_tts, body_md_override) — agora resolvendo POR CONTA.
- `notify/sanitize.py` (for_whatsapp/for_tts) e `notify/dispatch.py` — o claim em 3 fases
  (G16: CLAIM sob lock → envio fora da transação → resultado; recuperação por texto sem regenerar
  TTS) porta INTEIRO. É a parte mais valiosa e mais testada do app.
- TTS: client HTTP fino pro **omnirouter** (decisão 7) — contrato a confirmar (pendência 1). O
  mp3 é salvo/servido pelo PRÓPRIO serviço; a Evolution busca pela URL LAN do serviço.
- Rewrite de mídia `_to_lan`: mantém, par (base pública → base LAN) no `.env` do serviço.

### API v1 (Ninja; auth por api-key da conta; staff = chave de admin do serviço)
| Rota | Uso |
|---|---|
| `POST /v1/send` | args do `send()` atual + `external_id` opcional do cliente + `number` opcional (default da conta). 202 → `{external_id, statuses}` |
| `POST /v1/send-event` | evento + destinatário (phone/email/nome/gender já resolvidos pelo caller) + ctx + overrides (`body_md_override` incluso) — o Template DA CONTA resolve teor/canais/is_tts/mídia; `Trigger.active=False` → no-op |
| `GET /v1/notifications[/{external_id}]` | status por canal + histórico com filtros |
| `POST /v1/phone/check` | resolve 9º dígito + existe-no-zap (substitui o uso direto no register) |
| `POST /v1/phone/avatar` | `{number}` → `{number, photo}` (URL da foto de perfil, ou null). Rota SEPARADA do check de propósito: o check roda dentro do request do `auth.check` (latência conta); a foto é buscada em task async do backend (`lead.tasks.fetch_whatsapp_avatar`) só quando a conta nasce — alimenta o pergaminho do funil v2 (tela 3-4). Foto é ILUSTRAÇÃO (pública, definida pelo usuário, URL do CDN expira), nunca prova de identidade |
| Staff: CRUD `/v1/templates[/{event}]` + trigger, `POST /v1/adhoc`, preview, stats, seed | porte do `api/staff_notify.py` — o painel do staff passa a comandar AQUI (decisão 2) |
| `GET /v1/health` | saúde (padrão da casa) |
| Webhook Evolution (por instância) | persiste `InboundEvent`; 200 na hora |

Onboarding de número (criar instância + QR + status) começa **pelo admin do Django**; virar
painel é fase 3 — no v1 a única instância nova é a da conta default (número padrão da casa).

### Deploy e validação
- LXC própria (padrão `backend-v7m`): Caddy interno/VPN → nginx → gunicorn + qcluster; database
  própria no Postgres geral CT 2100; `.env` gitignored.
- Validação REAL (§8): porte do `notify_send` + prova dos 3 canais no aparelho/e-mail do Victor
  (TTS via omnirouter!) + `send-event` com Template por conta + `phone/check` + idempotência,
  ANTES da fase 2.

## Fase 2 — conectar o backend (cutover)

> **Estado 2026-07-18: itens 1, 4 e 6 IMPLEMENTADOS atrás da flag** (`NOTIFY_MODE`, default
> `local` = zero mudança). Código: `notify/remote.py` (cliente HTTP + retry Django-Q),
> `notify/interface/send.py` (`_send_remote`), checks `notify.E002/E003`, migração `users/0033`
> (OTP FK→UUID, espelho reverso da 0012), register remoto em `users/auth/service.py`. Testes:
> `tests/test_notify_remote.py`. **⚠️ Antes do corte: validar os nomes de campo do payload contra
> o notify-server REAL** (o contrato aqui é o do plano; o repo notify-server é a fonte).

1. **SDK no lugar do miolo** — `notify/interface/send.py` mantém as MESMAS assinaturas (63
   callsites intocados): `send()` → POST `/v1/send` com UUID gerado no cliente; falha de rede →
   retry via Django-Q com o MESMO UUID; devolve o handle na hora (§12). `run_sync=True` → POST
   síncrono. **Transição (implementado): `send_event()` NÃO muda** — segue resolvendo teor
   localmente (Template local + catálogo + storytelling) e desemboca no `send()`, que roteia pela
   flag. O `POST /v1/send-event` (teor resolvido no server) ativa DEPOIS, junto com a migração do
   painel — aí o corte de teor é um passo separado e reversível do corte de entrega.
2. **Seed dos templates** — migração one-time: rows de `Template`/`Trigger` do backend (+ catálogo
   `users/roles/notifications.py` como fonte) viram os templates da CONTA supletivo no serviço.
   O catálogo permanece no backend só como origem histórica do seed.
3. **Storytelling** — CASO A CASO (decisão 7). Caminho de menor risco no corte: o backend segue
   gerando o teor rico (IA local) e usa `body_md_override`; mover a geração pro serviço (via
   omnirouter, usando `story_prompt` do Template) fica como evolução (pendência 3).
4. **OTP** — troca a FK `otp.notification` por `notification_external_id` (CharField); status via
   `GET /v1/notifications/{id}`.
5. **Painel staff** — o front do staff (VPN) passa a falar com o notify-server direto (histórico,
   templates, adhoc, preview). `api/staff_notify.py` sai do backend após o corte.
6. **Register** — `users/auth/service.py` troca o client Evolution por `POST /v1/phone/check`.
7. **Histórico antigo** — migração one-time das `Notification` do backend pra conta supletivo.
8. **Rollout com flag** — `NOTIFY_MODE=local|remote` no backend; produção corta pra `remote` só
   após E2E real (3 canais + OTP login + send-event + history); rollback = voltar a flag.
9. **Descomissionar após o corte** — `notify/` inteiro (models, dispatch, seed), `api/
   staff_notify.py`, `integrations/communication/{whatsapp,mail}`, TTS do `integrations/ai`, e as
   envs `WHATSAPP_*`/`MAIL_*`/`ELEVENLABS_*` do backend. (O `bot/` importa `_br_phone_variants`
   em runtime — já quebrado e de saída; não segura o corte.)

## Fase 3 — futuros (fora deste ciclo)
- **Novo app cliente** = criar `Account` + api-key + e-mail no mailcow + número (instância via QR).
- **Relay inbound por conta** (webhook-URL + HMAC): o novo lar do bot pluga aqui.
- **Painel de onboarding** self-service (instância/QR), métricas, rate-limit por conta.

## Pendências (estado 2026-07-17)
1. ~~**Contrato do omnirouter**~~ ✅ CRAVADO pela sessão local — `POST http://10.1.30.35/v1/audio/
   speech` (OpenAI-compatible), devolve bytes do mp3, MiniMax via OmniRoute, sem auth pro TTS.
   Detalhes no [[wiki/notify/HANDOFF-desmembramento]].
2. ~~**evolution-go**~~ ✅ **DESCARTADO** (Victor: "fique no v2 mesmo") — licença/heartbeat não
   compensam. Evolution v2 definitiva (decisão 9).
3. **Storytelling no serviço** (via omnirouter) ou permanece no backend com `body_md_override`?
   Plano assume backend no corte (fase 2 item 3). ÚNICA pendência aberta.
4. ~~**Nome do repo**~~ ✅ `notify-server` — https://github.com/maestri33/notify-server

## Riscos / pontos de atenção
- **Um hop de rede a mais** no caminho do OTP (login). Mitigação: LAN + timeout curto + retry
  enfileirado no SDK; o OTP já tolera envio assíncrono.
- **Omnirouter fora do ar** → fallback já existente no dispatch: voice-note falhou → TEXTO no
  WhatsApp (o marco nunca fica mudo). Porta como está.
- **Serviço fora do ar** → SDK enfileira e re-tenta com o mesmo UUID; nenhum caller quebra (§12).
  O fallback in-memory de teor (catálogo) DEIXA de existir no caminho remoto — aceito: a fila
  cobre a janela.
- **Segredo por conta no DB do serviço** (SMTP mailcow): criptografia at-rest simples (Fernet,
  chave no `.env`) na implementação.
- **`idempotency_key` e `event` passam a ser únicos POR CONTA** (hoje globais) — sem impacto.
- Sanitização TTS e conversão md→WhatsApp/HTML continuam no serviço (são de ENTREGA, não de teor).

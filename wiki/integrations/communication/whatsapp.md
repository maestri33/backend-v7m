# whatsapp — integrations/communication/whatsapp (Evolution GO)

> **ESTADO:** cliente do Evolution GO. O contrato antigo da Evolution API v2 foi removido em
> 2026-07-23 porque a instância ficou inacessível.

## Contrato adotado

O token enviado no header `apikey` identifica a instância; nome de instância não vai na URL.

| Operação interna | Evolution GO |
|---|---|
| `health()` | `GET /instance/status` |
| `check_numbers()` | `POST /user/check` com `{"number": [...]}` |
| `send_text()` | `POST /send/text` |
| `send_media()` | `POST /send/media` com `number`, `url`, `type`, `caption`, `filename` |
| `send_whatsapp_audio()` | `POST /send/media` com `type=audio` (o GO converte para Opus/PTT) |

O cliente normaliza `Query`/`IsInWhatsapp`/`JID`/`RemoteJID` de `data.Users` para o formato interno
já consumido pelo auth e pelo `notify`. Mídia deve ser uma URL HTTP(S) alcançável pelo servidor GO;
base64 e caminhos locais pertenciam ao contrato antigo e não são aceitos.

## Configuração

| Chave | Uso |
|---|---|
| `WHATSAPP_API_BASE_URL` | URL do Evolution GO, sem barra final |
| `WHATSAPP_API_KEY` | token da instância, armazenado fora do Git |
| `MEDIA_LAN_BASE` | base pela qual o GO alcança mídia gerada pelo backend |

Para desenvolvimento, o gateway validado está em `http://10.3.20.200:4000`, instância `default`
identificada pelo token.

## Validação

```bash
python manage.py test integrations.communication.whatsapp
python manage.py whatsapp_health
python manage.py whatsapp_send 55... "texto"
python manage.py whatsapp_send_media 55... image http://backend/media/imagem.png
python manage.py whatsapp_send_media 55... audio http://backend/media/audio.mp3
```

`whatsapp_health` é somente leitura. Os dois últimos comandos enviam mensagens reais.

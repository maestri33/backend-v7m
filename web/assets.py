"""Assets do funil servidos PELO DJANGO em /app/asset/<nome>.

Motivo (2026-07-28): o nginx do LXC intercepta `/static/` com um root desatualizado e
404-eia tudo (inclusive o CSS do /admin — pré-existente). Até o nginx ser corrigido, o
funil não pode depender de /static/: htmx e logo saem daqui, com cache forte. WhiteNoise
continua no lugar — quando o nginx for consertado, dá pra voltar pro {% static %}.
"""

from __future__ import annotations

from pathlib import Path

from django.http import FileResponse, Http404

_ASSET_DIR = Path(__file__).resolve().parent / "static" / "web"
_ALLOWED = {
    "htmx.min.js": "application/javascript",
    "logo.svg": "image/svg+xml",
}


def asset(request, name: str):
    ctype = _ALLOWED.get(name)
    if ctype is None:
        raise Http404
    resp = FileResponse(open(_ASSET_DIR / name, "rb"), content_type=ctype)
    resp["Cache-Control"] = "public, max-age=86400"
    return resp

"""Gravação padronizada de mídia sob o MEDIA_ROOT, via `default_storage`.

Um writer único pra TODOS os funis (documentos, selfie, aluno, auditoria): o caminho é
`"<prefix>/<token>.<ext>"` com **token aleatório** — NUNCA `external_id`/slot no path (pedido do
Victor 2026-06-21: o `external_id` vaza pro frontend, então caminho por id é enumerável; a mídia é
sempre referenciada pelo campo salvo no DB, jamais reconstruída por id). Trocar `default_storage`
um dia (S3/objeto) passa a valer pra todos os writers de uma vez.
"""

from __future__ import annotations

import secrets

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage


def media_token() -> str:
    """Token aleatório, URL-safe e não-enumerável, pro nome do arquivo/pasta de mídia."""
    return secrets.token_urlsafe(16)


def save_media(*, prefix: str, data: bytes, ext: str, token: str | None = None) -> str:
    """Salva `data` em `"<prefix>/<token>.<ext>"` e devolve o caminho RELATIVO (pro campo do DB)."""
    tok = token or media_token()
    path = f"{prefix.strip('/')}/{tok}.{ext.lstrip('.')}"
    if default_storage.exists(path):
        default_storage.delete(path)
    default_storage.save(path, ContentFile(data))
    return path


def save_media_at(*, path: str, data: bytes) -> str:
    """Salva em um caminho relativo EXPLÍCITO (ex.: os arquivos de uma pasta de auditoria por token)."""
    if default_storage.exists(path):
        default_storage.delete(path)
    default_storage.save(path, ContentFile(data))
    return path

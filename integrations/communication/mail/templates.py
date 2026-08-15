"""Render de template de email — arquivos HTML em disco, troca de placeholders (sem DB).

Cada template é um arquivo `templates/<slug>.html` no app. `render()` troca `{{title}}`,
`{{content}}` e `{{service_name}}`, portando a higienização do legado (escape + bold markdown +
nl2br). Sem model/migração/CRUD — alinhado à CONVENTION §12 (a mensagem mora no app emissor) e §8
(integration fino). Quem decide qual slug usar é o futuro notify.
"""

from __future__ import annotations

import html as _html
import re
from functools import lru_cache
from pathlib import Path

from django.conf import settings

DEFAULT_SLUG = "default"
_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}[a-z0-9]$")
_SLUG_ALIASES = {
    "checkout": "supletivo",
    "parabens": "supletivo",
    "receipt": "supletivo",
    "welcome": "supletivo",
}


class TemplateNotFound(Exception):
    """Slug inexistente e sem o fallback `default` em disco (estado inválido)."""


MEDIA_TYPES = {"image", "video", "audio", "document"}


# schemes permitidos em links markdown (defesa-in-depth: o texto já é escaped; mesmo assim,
# rejeita javascript:/data: etc.). O resto vira texto plano.
_SAFE_URL_SCHEMES = {"http", "https", "mailto"}
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _md_inline(escaped: str) -> str:
    """Transforms inline markdown (JÁ escapado): bold, italic, code, links. Ordem importa."""
    # code `x` → <code> (antes de italic/bold pra não comer o `*` dentro)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)

    # links [text](url) — só schemes seguros; senão emite texto plano (text + url visível).
    def _link(m: re.Match) -> str:
        label, url = m.group(1), m.group(2).strip()
        scheme = url.split(":", 1)[0].lower() if ":" in url else ""
        if scheme and scheme not in _SAFE_URL_SCHEMES:
            return f"{label} ({url})"
        return f'<a href="{url}" target="_blank" rel="noopener">{label}</a>'

    escaped = _LINK_RE.sub(_link, escaped)
    # bold **x** (específico) antes do italic *x* / _x_
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped, flags=re.DOTALL)
    escaped = re.sub(
        r"(?<![*\w])\*([^*\s](?:.*?[^*\s])?)\*(?![*\w])",
        r"<em>\1</em>",
        escaped,
        flags=re.DOTALL,
    )
    escaped = re.sub(
        r"(?<![\w])_([^_\s](?:.*?[^_\s])?)_(?![\w])",
        r"<em>\1</em>",
        escaped,
        flags=re.DOTALL,
    )
    return escaped


def md_to_html(md: str) -> str:
    """Markdown → HTML seguro pro e-mail. Escape primeiro, depois transforms (XSS-safe).

    Suporta: headings (#/##/###), listas (-/*/1.), parágrafos (\\n\\n), quebra de linha (\\n),
    bold **x**, italic *x* / _x_, code `x`, links [text](url) (scheme-check). Conteúdo é de autor
    (Victor), mas a defesa-in-depth escape-antes-tudo + scheme-check protege contra descuido.
    """
    if not md:
        return ""
    escaped = _html.escape(md)
    lines = escaped.split("\n")
    out: list[str] = []
    list_buf: list[str] = []
    list_type: str | None = None  # "ul" | "ol"

    def _flush_list() -> None:
        nonlocal list_buf, list_type
        if list_type:
            tag = "ul" if list_type == "ul" else "ol"
            out.append(
                f"<{tag}>"
                + "".join(f"<li>{_md_inline(it)}</li>" for it in list_buf)
                + f"</{tag}>"
            )
            list_buf, list_type = [], None

    for raw in lines:
        line = raw.rstrip()
        if not line:
            _flush_list()
            out.append("")  # separador de parágrafo
            continue
        # heading
        m = re.match(r"^(#{1,3})\s+(.*)$", line)
        if m:
            _flush_list()
            level = len(m.group(1))
            out.append(f"<h{level}>{_md_inline(m.group(2))}</h{level}>")
            continue
        # lista ordenada
        mo = re.match(r"^\d+\.\s+(.*)$", line)
        if mo:
            if list_type != "ol":
                _flush_list()
                list_type = "ol"
            list_buf.append(mo.group(1))
            continue
        # lista não-ordenada
        mu = re.match(r"^[-*]\s+(.*)$", line)
        if mu:
            if list_type != "ul":
                _flush_list()
                list_type = "ul"
            list_buf.append(mu.group(1))
            continue
        _flush_list()
        out.append(_md_inline(line))

    _flush_list()
    # junta parágrafos: blocos separados por string vazia viram <p>; quebra de linha simples <br>.
    html_parts: list[str] = []
    para: list[str] = []
    for blk in out:
        if blk == "":
            if para:
                html_parts.append("<p>" + "<br>".join(para) + "</p>")
                para = []
        elif blk.startswith(("<h1", "<h2", "<h3", "<ul", "<ol")):
            if para:
                html_parts.append("<p>" + "<br>".join(para) + "</p>")
                para = []
            html_parts.append(blk)
        else:
            para.append(blk)
    if para:
        html_parts.append("<p>" + "<br>".join(para) + "</p>")
    return "\n".join(html_parts)


def text_to_html(text: str) -> str:
    """Compat: markdown → HTML seguro (alias de `md_to_html`). Mantido p/ callers antigos."""
    return md_to_html(text)


def media_html(media_url: str, media_type: str, caption: str = "") -> str:
    """Snippet HTML pra embutir mídia no email por URL (porte do legado `_email_media_html`).

    image → `<img src=URL>` inline; video/audio/document → ícone + link clicável. O cliente de email
    (ex.: Gmail) busca a URL pública. Usado como `content` com `render(..., content_is_html=True)`.
    """
    if media_type not in MEDIA_TYPES:
        media_type = "document"
    safe_url = _html.escape(media_url)
    safe_caption = _html.escape(caption)

    if media_type == "image":
        return (
            '<div style="margin:20px 0;text-align:center">'
            f'<img src="{safe_url}" alt="{safe_caption}" '
            'style="max-width:100%;height:auto;border-radius:4px">'
            '<p style="margin:8px 0 0;color:#666;font-size:14px;font-family:Arial,sans-serif">'
            f"{safe_caption}</p>"
            "</div>"
        )
    if media_type == "video":
        return (
            '<div style="margin:20px 0;text-align:center">'
            '<p style="font-size:40px;margin:0">&#9654;&#65039;</p>'
            f'<p style="margin:8px 0;font-family:Arial,sans-serif;font-size:15px;color:#333">'
            f"{safe_caption}</p>"
            f'<a href="{safe_url}" target="_blank" '
            'style="color:#1a73e8;font-family:Arial,sans-serif;font-size:14px">'
            "Assistir v&iacute;deo</a>"
            "</div>"
        )
    if media_type == "audio":
        return (
            '<div style="margin:20px 0;text-align:center">'
            '<p style="font-size:36px;margin:0">&#127911;</p>'
            f'<p style="margin:8px 0;font-family:Arial,sans-serif;font-size:15px;color:#333">'
            f"{safe_caption}</p>"
            f'<a href="{safe_url}" target="_blank" '
            'style="color:#1a73e8;font-family:Arial,sans-serif;font-size:14px">'
            "Ouvir &aacute;udio</a>"
            "</div>"
        )
    safe_name = _html.escape(
        media_url.rsplit("/", 1)[-1] if "/" in media_url else "arquivo"
    )
    return (
        '<div style="margin:20px 0;text-align:center">'
        '<p style="font-size:36px;margin:0">&#128206;</p>'
        f'<p style="margin:4px 0;font-family:Arial,sans-serif;font-size:13px;color:#666">'
        f"{safe_name}</p>"
        f'<p style="margin:8px 0;font-family:Arial,sans-serif;font-size:15px;color:#333">'
        f"{safe_caption}</p>"
        f'<a href="{safe_url}" target="_blank" '
        'style="color:#1a73e8;font-family:Arial,sans-serif;font-size:14px">Baixar arquivo</a>'
        "</div>"
    )


@lru_cache(maxsize=32)
def _load(slug: str) -> str:
    return (_TEMPLATES_DIR / f"{slug}.html").read_text(encoding="utf-8")


def render(
    slug: str | None,
    *,
    title: str,
    content: str,
    content_is_html: bool = False,
) -> str:
    """Renderiza o template do slug (fallback `default`), trocando os placeholders.

    title/content são humanos → escapados (XSS-safe). content em texto recebe bold markdown →
    <strong> e `\\n` → `<br>`. content_is_html=True quando o caller já montou HTML.
    `{{service_name}}` = settings.MAIL_FROM_NAME.
    """
    resolved = slug if (slug and _SLUG_RE.match(slug)) else DEFAULT_SLUG
    resolved = _SLUG_ALIASES.get(resolved, resolved)
    try:
        template = _load(resolved)
    except FileNotFoundError:
        if resolved == DEFAULT_SLUG:
            raise TemplateNotFound("template 'default' ausente em templates/") from None
        try:
            template = _load(DEFAULT_SLUG)
        except FileNotFoundError:
            raise TemplateNotFound("template 'default' ausente em templates/") from None

    safe_title = _html.escape(title)
    if content_is_html:
        safe_content = content
    else:
        safe_content = md_to_html(
            content
        )  # full markdown, já escapa internamente (XSS-safe)
    return (
        template.replace("{{title}}", safe_title)
        .replace("{{content}}", safe_content)
        .replace("{{service_name}}", _html.escape(settings.MAIL_FROM_NAME))
    )

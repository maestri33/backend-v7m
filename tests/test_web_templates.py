"""Guardas dos templates do funil web.

Nasceram de erros que chegaram a ser vistos na tela: comentário `{# #}` multilinha (que o
Django NÃO fecha e renderiza como texto) e código de erro do backend sem mensagem humana.
"""

from __future__ import annotations

import re
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"
TEMPLATES = sorted(WEB.glob("templates/**/*.html"))


def test_sem_comentario_django_multilinha():
    """`{# ... #}` é SINGLE-LINE. Aberto numa linha e fechado noutra, o Django imprime tudo
    como texto — aconteceu duas vezes e apareceu pro usuário. Multilinha exige {% comment %}."""
    problemas = []
    for tpl in TEMPLATES:
        for n, linha in enumerate(tpl.read_text(encoding="utf-8").splitlines(), 1):
            if "{#" in linha and "#}" not in linha:
                problemas.append(f"{tpl.relative_to(WEB)}:{n}")
    assert not problemas, (
        "comentário {# #} aberto sem fechar na mesma linha (use {% comment %}): "
        + ", ".join(problemas)
    )


def test_todo_codigo_de_erro_do_candidato_tem_mensagem():
    """Todo `code=` que o funil pode devolver precisa de texto humano em `_ERROR_TEXT` ou
    `_ERROR_MODAL` — senão o usuário recebe o genérico 'Não deu certo' (visto no E2E do CEP)."""
    from web import views

    conhecidos = set(views._ERROR_TEXT) | set(views._ERROR_MODAL)
    fontes = [
        "users/roles/candidate/service.py",
        "users/address/service.py",
    ]
    raiz = Path(__file__).resolve().parent.parent
    achados: set[str] = set()
    for rel in fontes:
        achados |= set(
            re.findall(r'code="([A-Z_]+)"', (raiz / rel).read_text(encoding="utf-8"))
        )

    # códigos internos: nunca chegam à tela do candidato (gate de etapa / recurso ausente)
    internos = {
        "WRONG_STATUS",
        "CANDIDATE_NOT_FOUND",
        "PROMOTER_NOT_FOUND",
        "USER_NOT_FOUND",
        "NOT_HUB_COORDINATOR",
        "SELFIE_NOT_IN_REVIEW",
        "DOC_NOT_IN_REVIEW",
        "MILITARY_NOT_APPLICABLE",
        "EDUCATION_QUALIFICATION_INVALID",
        "EDUCATION_LAST_COMPLETED_QUALIFICATION_INVALID",
        "DOC_TYPE_LOCKED",
    }
    faltando = sorted(achados - conhecidos - internos)
    assert not faltando, f"sem mensagem humana no funil web: {faltando}"

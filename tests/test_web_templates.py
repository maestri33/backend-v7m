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


def test_pergaminho_substitui_o_card_do_cpf():
    """Protótipo: `cpfNormal` e `cpfDiscovery` são exclusivos — o pergaminho ocupa o lugar do
    card, não fica pendurado embaixo. Quem garante isso é o swap out-of-band em `#cpf-step`."""
    base = WEB / "templates" / "web"
    cpf = (base / "cpf.html").read_text(encoding="utf-8")
    perg = (base / "partials/pergaminho.html").read_text(encoding="utf-8")
    assert 'id="cpf-step"' in cpf, "cpf.html precisa marcar o bloco que sai de cena"
    assert 'id="cpf-step"' in perg and "hx-swap-oob" in perg, (
        "pergaminho tem que remover o #cpf-step por out-of-band swap"
    )


def test_documento_oferece_foto_E_arquivo():
    """`capture` no celular abre a câmera DIRETO e esconde o seletor — quem tem a CNH digital em
    PDF ficava sem caminho. Os dois métodos têm que existir, e o PDF do gov só na CNH."""
    base = WEB / "templates" / "web"
    painel = (base / "partials/document_panel.html").read_text(encoding="utf-8")
    assert "metodo = 'foto'" in painel and "metodo = 'arquivo'" in painel
    assert "gov.br" in painel, "o caminho do PDF do gov precisa estar dito na tela"

    slot = (base / "partials/_doc_slot.html").read_text(encoding="utf-8")
    assert "s.capture" in slot and "s.accept" in slot, (
        "o accept/capture tem de vir do slot — hardcodado, todo slot vira câmera"
    )


def test_botao_falar_com_o_aluno_leva_o_numero():
    """Apontava pra `wa.me/` puro: abria o WhatsApp em branco e o promotor não tinha como falar
    com quem ele acabou de convidar."""
    tpl = (WEB / "templates" / "web" / "panel" / "_invite_ok.html").read_text(
        encoding="utf-8"
    )
    assert 'href="{{ wa_url }}"' in tpl
    assert 'https://wa.me/"' not in tpl


def test_kanban_da_home_e_da_semana():
    """O card diz "Leads desta semana" — então a consulta tem de ser da semana, com a MESMA
    janela do fechamento. Trazia todos os leads de sempre sob esse título."""
    src = (WEB / "panel_data.py").read_text(encoding="utf-8")
    trecho = src[src.index("def leads(") : src.index("def leads(") + 900]
    assert "week_window" in trecho and "created_at__gte" in trecho

"""LMS igual ao protótipo: responder NÃO espera nota (a IA corrige em segundo plano) e o
painel nunca pode redirecionar pra si mesmo (loop que derrubava a tela em 2026-07-29)."""

import pytest

from users.roles.training import service as training_iface
from users.roles.training.models import MaterialAssignment, Submission

pytestmark = pytest.mark.django_db


def _material(**kw):
    from users.roles.training.models import Material

    base = {
        "title": "Aula",
        "kind": "lesson",
        "blocking": True,
        "order": 1,
        "text_content": "conteudo",
        "question": "pergunta?",
    }
    base.update(kw)
    return Material.objects.create(**base)


def _user():
    from users.auth.models import User
    from users.profiles.models import Profile

    u = User.objects.create(is_active=True)
    Profile.objects.create(user=u, phone="5543999990002", name="Teste")
    return u


def test_aula_respondida_nao_trava_o_painel():
    """Respondeu → a aula sai da frente na hora. Quem trava é aula ABERTA, não aula em correção."""
    u, m = _user(), _material()
    MaterialAssignment.objects.create(
        user=u, material=m, status=MaterialAssignment.Status.PENDING
    )
    assert training_iface.is_locked(u) is True

    Submission.objects.create(
        user=u,
        material=m,
        answer="resposta comprida o suficiente pra valer",
        status=Submission.Status.PENDING,
    )
    assert training_iface.is_locked(u) is False, (
        "aula em correção não pode segurar o promotor no LMS"
    )


def test_reprovada_volta_a_travar():
    """A IA reprovou → a aula volta pra fila e trava de novo, sem ninguém precisar avisar."""
    u, m = _user(), _material()
    MaterialAssignment.objects.create(
        user=u, material=m, status=MaterialAssignment.Status.PENDING
    )
    sub = Submission.objects.create(
        user=u, material=m, answer="curta", status=Submission.Status.PENDING
    )
    assert training_iface.is_locked(u) is False
    sub.status = Submission.Status.REJECTED
    sub.save(update_fields=["status"])
    assert training_iface.is_locked(u) is True


def test_painel_nunca_redireciona_pra_si_mesmo():
    """Guard do loop: nenhuma rota do painel pode responder 302 apontando pra própria URL."""
    import inspect

    from web import views

    src = inspect.getsource(views.panel_route)
    assert "redirect(flow.step_url(current, request))" in src
    # o ramo do promoter ausente TEM que sair do laço (render de erro), não redirecionar
    depois = src.split("promoter is None:")[1]
    assert "redirect(" not in depois.split("return fn(")[0], (
        "promoter ausente redirecionando pro próprio painel = loop infinito"
    )


def test_candidato_sem_endereco_nao_explode():
    """Endereço nulo é o estado NORMAL no início do funil. Estourava AttributeError dentro do
    próprio gate (`current_step`), então a pessoa nem era redirecionada: 500 na cara dela."""
    from users.roles.candidate import service as cand_svc
    from users.roles.candidate.models import Candidate
    from users.documents import service as docs
    from users.roles import service as roles_svc
    from hub.models import Hub
    from users.address.models import Address

    u = _user()
    roles_svc.grant(u, "candidate")
    docs.create_empty(u)
    endereco = Address.objects.create(
        zipcode="84010000",
        street="R",
        number="1",
        neighborhood="C",
        city="Ponta Grossa",
        state="PR",
    )
    hub = Hub.objects.create(brand="V7M", address=endereco)
    cand = Candidate.objects.create(user=u, hub=hub)
    assert u.profile.address is None

    me = cand_svc.me_dict(cand)
    assert me["address"]["street"] is None
    assert set(me["address"]["missing_fields"]) >= {"street", "city", "state"}

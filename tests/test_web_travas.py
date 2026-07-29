"""Travas do funil (Victor 2026-07-29): reprovou ou falta o parentesco → a pessoa NÃO entra no
painel, volta pra etapa. É o `rejectedSteps[]` do protótipo, que o backend não tinha.
"""

import datetime

import pytest

from web import flow

pytestmark = pytest.mark.django_db


def _promotor():
    from hub.models import Hub
    from users.address.models import Address
    from users.auth.models import User
    from users.documents import service as docs
    from users.profiles.models import Profile
    from users.roles import service as roles_svc
    from users.roles.candidate.models import Candidate
    from users.roles.promoter import service as prom

    u = User.objects.create(is_active=True)
    a = Address.objects.create(
        zipcode="84010000",
        street="R",
        number="1",
        neighborhood="C",
        city="Ponta Grossa",
        state="PR",
    )
    Profile.objects.create(
        user=u,
        phone="5543999990088",
        cpf="39053344705",
        email="t@t.com",
        name="CARLOS",
        gender="M",
        birth_date=datetime.date(1985, 2, 2),
        address=a,
    )
    roles_svc.grant(u, "promoter")
    docs.create_empty(u)
    hub = Hub.objects.create(brand="V7M", address=a)
    cand = Candidate.objects.create(user=u, hub=hub)
    cand.status = "approved"
    cand.save()
    prom.create_promoter(user=u, hub=hub)
    return u


@pytest.mark.parametrize(
    ("source_type", "passo"),
    [
        ("rg", "document"),
        ("cnh", "document"),
        ("address_proof", "address"),
        ("selfie", "selfie"),
    ],
)
def test_etapa_reprovada_tem_prioridade_sobre_o_painel(source_type, passo):
    from users.blocks import service as blocks

    u = _promotor()
    assert flow._passo_reprovado(u) is None

    blocks.create_block(
        user=u,
        source_type=source_type,
        title="reprovado",
        description="refaz",
        action_label="Corrigir",
        action_route="/x",
    )
    assert flow._passo_reprovado(u) == passo


def test_bloco_de_aula_nao_vira_etapa_do_funil():
    """Aula pendente é trava do LMS, não do wizard — quem cuida dela é o `is_locked`."""
    from users.blocks import service as blocks

    u = _promotor()
    blocks.create_block(
        user=u,
        source_type="training_7",
        title="Atividade reprovada",
        description="refaz",
        action_label="Corrigir",
        action_route="/x",
    )
    assert flow._passo_reprovado(u) is None


def test_parentesco_pendente_trava():
    from users.documents import service as docs

    u = _promotor()
    assert flow._precisa_parentesco(u) is False

    ap = docs.get_address_proof(str(u.external_id))
    ap.photo = "address/x.jpg"
    ap.validation_status = "needs_kinship"
    ap.validation_result = {"kinship_kind": "justify"}
    ap.save()
    assert flow._precisa_parentesco(u) is True

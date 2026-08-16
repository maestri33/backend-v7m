"""Contagem de queries (auditoria API C2/C3): as listagens do coordenador e o painel de treino
faziam 1 query por item (N+1). Estes testes fixam o teto — não pode voltar a escalar com o polo.

`locked_user_ids` também é testado por CORREÇÃO (bate com a versão per-user `is_locked`)."""

import uuid

import pytest
from django.test.utils import CaptureQueriesContext
from django.db import connection

pytestmark = pytest.mark.django_db


def _promoter_in_hub(hub, *, locked_material=None):
    from users.auth.models import User
    from users.roles.promoter.models import Promoter
    from users.roles.training.models import MaterialAssignment

    u = User.objects.create_user(external_id=uuid.uuid4(), is_active=True)
    from users.profiles.models import Profile

    # phone único por promotor (o model tem unique em phone) — 11 dígitos derivados do pk.
    Profile.objects.create(
        user=u, name=f"Promotor {u.pk}", phone=f"11{u.pk:09d}"
    )
    pr = Promoter.objects.create(user=u, hub=hub, status=Promoter.Status.ACTIVE)
    if locked_material is not None:
        MaterialAssignment.objects.create(
            user=u,
            material=locked_material,
            status=MaterialAssignment.Status.PENDING,
        )
    return pr


def _hub():
    from hub.models import Hub
    from users.address.models import Address

    return Hub.objects.create(brand="standard", address=Address.objects.create())


def test_list_for_hub_nao_faz_n_mais_1(django_assert_num_queries=None):
    """O painel de promotores do coordenador tem contagem de queries CONSTANTE — não cresce com o
    número de promotores (era 1 get_map ok + 1 is_locked POR promotor)."""
    from users.roles.promoter import service as promoter_service
    from users.roles.training.models import Material

    hub = _hub()
    blocking = Material.objects.create(
        title="Obrigatória",
        question="q",
        expected_answer="a",
        blocking=True,
        active=True,
    )
    for _ in range(5):
        _promoter_in_hub(hub, locked_material=blocking)

    with CaptureQueriesContext(connection) as ctx:
        out = promoter_service.list_for_hub(hub)
    assert len(out) == 5
    assert all(item["locked"] for item in out), "trava não detectada em lote"
    # teto folgado mas CONSTANTE: promoters + profiles(get_map) + locked(2 queries). Sem N+1.
    assert len(ctx) <= 6, f"N+1 no painel de promotores: {len(ctx)} queries pra 5 promotores"


def test_locked_user_ids_bate_com_is_locked():
    """Correção: o lote `locked_user_ids` dá o MESMO resultado do `is_locked` per-user."""
    from users.roles.training import service as training
    from users.roles.training.models import Material, Submission

    hub = _hub()
    m = Material.objects.create(
        title="M", question="q", expected_answer="a", blocking=True, active=True
    )
    travado = _promoter_in_hub(hub, locked_material=m)
    # respondeu (submissão pending) → NÃO trava, mesmo com a atribuição pendente
    respondeu = _promoter_in_hub(hub, locked_material=m)
    Submission.objects.create(
        user=respondeu.user, material=m, answer="x", status=Submission.Status.PENDING
    )
    livre = _promoter_in_hub(hub)  # sem atribuição obrigatória

    ids = training.locked_user_ids(
        [travado.user_id, respondeu.user_id, livre.user_id]
    )
    assert ids == {travado.user_id}
    # espelha o per-user
    assert training.is_locked(travado.user) is True
    assert training.is_locked(respondeu.user) is False
    assert training.is_locked(livre.user) is False

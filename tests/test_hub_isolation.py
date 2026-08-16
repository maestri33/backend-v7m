"""Isolamento de polo (auditoria API P0): a negação cross-polo NÃO pode vazar existência.

Antes, um coordenador que agisse sobre objeto de OUTRO polo tomava 422 NOT_HUB_COORDINATOR —
distinguível do 404 de "não existe", virando oráculo de enumeração da base do outro polo. Agora
objeto de outro polo devolve o MESMO 404 (…_NOT_FOUND) de inexistente. Este é o primeiro teste do
repo que cria DOIS hubs (a auditoria notou que o isolamento não tinha regressão nenhuma).
"""

import uuid

import pytest

pytestmark = pytest.mark.django_db


def _mk_coord_and_hub():
    """Um coordenador + o hub que ele coordena."""
    from hub.models import Hub
    from users.address.models import Address
    from users.auth.models import User

    coord = User.objects.create_user(external_id=uuid.uuid4(), is_active=True)
    hub = Hub.objects.create(
        brand="standard", address=Address.objects.create(), coordinator=coord
    )
    return coord, hub


def _mk_candidate(hub):
    from users.auth.models import User
    from users.roles.candidate.models import Candidate

    u = User.objects.create_user(external_id=uuid.uuid4(), is_active=True)
    return Candidate.objects.create(user=u, hub=hub, doc_type="rg")


def test_candidato_de_outro_polo_da_404_igual_a_inexistente():
    """Coordenador do polo B tenta decidir doc de candidato do polo A → 404 CANDIDATE_NOT_FOUND,
    com o MESMO status e code que um external_id inexistente. Antes: 422 vs 404 = oráculo."""
    from users.exceptions import DomainError, NotFound
    from users.roles.candidate import service as cs

    coord_b, _hub_b = _mk_coord_and_hub()
    _coord_a, hub_a = _mk_coord_and_hub()
    cand_a = _mk_candidate(hub_a)

    # objeto REAL de outro polo
    with pytest.raises((NotFound, DomainError)) as real:
        cs.decide_document(
            candidate_external_id=str(cand_a.external_id),
            coordinator=coord_b,
            approve=True,
        )
    # objeto que NÃO existe
    with pytest.raises((NotFound, DomainError)) as ghost:
        cs.decide_document(
            candidate_external_id=str(uuid.uuid4()),
            coordinator=coord_b,
            approve=True,
        )

    # indistinguíveis: mesmo status HTTP, mesmo code, mesma mensagem → sem oráculo
    assert real.value.code == ghost.value.code == "CANDIDATE_NOT_FOUND"
    assert real.value.status == ghost.value.status == 404
    assert str(real.value) == str(ghost.value)

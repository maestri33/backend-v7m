"""Router de Diagnóstico de Sistema, Integrações e Logs (Staff)."""

from __future__ import annotations

from django.conf import settings
from ninja import Router

from api.auth import require_superuser
from integrations import status as integ_status
from users.exceptions import NotFound

router = Router(tags=["staff"])


@router.get("/integrations", summary="Listagem de integrações")
def list_integrations(request):
    """Saúde e configuração das integrações."""
    require_superuser(request.auth)
    return integ_status.list_integrations()


@router.get("/integrations/{name}", summary="Detalhe de integração")
def integration_detail(request, name: str):
    """Detalhe de integração específica."""
    require_superuser(request.auth)
    data = integ_status.integration_detail(name)
    if data is None:
        raise NotFound("Integração não encontrada.", code="INTEGRATION_NOT_FOUND")
    return data


@router.post("/integrations/asaas/setup", summary="Configurar webhook Asaas")
def integration_setup(request):
    """Cadastra ou atualiza webhook do Asaas."""
    require_superuser(request.auth)
    from integrations.bank.asaas import onboarding

    return onboarding.setup()


@router.post("/integrations/asaas/test", summary="Testar integração Asaas")
def integration_test(request):
    """Executa testes de conectividade do Asaas."""
    require_superuser(request.auth)
    from integrations.bank.asaas import onboarding

    return onboarding.run_checks(record=True)


@router.get("/system", summary="Status do servidor e infraestrutura")
def system_status(request):
    """Saúde do servidor: banco, migrations, Django-Q e filas."""
    require_superuser(request.auth)
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    db_ok = True
    try:
        with connection.cursor() as c:
            c.execute("SELECT 1")
    except Exception:
        db_ok = False
    executor = MigrationExecutor(connection)
    pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
    clusters: list = []
    queued = None
    try:
        from django_q.models import OrmQ
        from django_q.status import Stat

        clusters = [s.cluster_id for s in Stat.get_all()]
        queued = OrmQ.objects.count()
    except Exception:
        pass
    return {
        "db_ok": db_ok,
        "migrations_pending": [f"{m.app_label}.{m.name}" for m, _ in pending],
        "qcluster_alive": bool(clusters),
        "qcluster_count": len(clusters),
        "queued_tasks": queued,
        "debug": settings.DEBUG,
        "external_url": settings.EXTERNAL_URL,
    }


@router.get("/logs/ai-calls", summary="Logs de chamadas de IA")
def logs_ai_calls(request, status: str | None = None, limit: int = 100):
    """Histórico de chamadas de modelos de IA."""
    require_superuser(request.auth)
    from integrations.ai.models import AiCall

    qs = AiCall.objects.order_by("-created_at")
    if status:
        qs = qs.filter(status=status)
    return [
        {
            "provider": a.provider,
            "model": a.model,
            "operation": a.operation,
            "caller": a.caller,
            "status": a.status,
            "cost": str(a.cost) if a.cost is not None else None,
            "latency_ms": a.latency_ms,
            "error": a.error,
            "created_at": a.created_at.isoformat(),
        }
        for a in qs[:limit]
    ]


@router.get("/logs/checks", summary="Logs de verificações de validação")
def logs_checks(request, scope: str | None = None, limit: int = 100):
    """Histórico do ledger de validações."""
    require_superuser(request.auth)
    from core.models import ValidationCheck

    qs = ValidationCheck.objects.order_by("-checked_at")
    if scope:
        qs = qs.filter(scope=scope)
    return [
        {
            "scope": c.scope,
            "name": c.name,
            "passed": c.passed,
            "mode": c.mode,
            "detail": c.detail,
            "checked_at": c.checked_at.isoformat(),
        }
        for c in qs[:limit]
    ]

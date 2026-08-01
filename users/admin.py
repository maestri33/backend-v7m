"""Django admin do funil de matrícula — o fluxo de CONCLUSÃO vive no backend (Victor 2026-07-25).

Desde 2026-07-13 nenhum front tem a área do coordenador com as ações de dinheiro; a decisão é que
elas moram AQUI, no /admin do monólito. As 3 ações (1ª parcela à vista, 2ª agendada, conclusão →
student) chamam a MESMA camada de serviço da API leadership (`users.roles.enrollment.service`) —
idempotência, locks, validação de QR e notificações inclusos, nada é reimplementado.

O serviço exige o coordenador do polo (gate `NOT_HUB_COORDINATOR`); a ação do admin é executada EM
NOME do coordenador do hub da matrícula, e o ator real do /admin fica no log estruturado. Só
superuser opera (mexe em R$ real e promove identidade).
"""

from __future__ import annotations

import structlog
from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse

from users.exceptions import DomainError
from users.roles.enrollment import service as enrollment_iface
from users.roles.enrollment.models import Enrollment

logger = structlog.get_logger(__name__)


class FeeForm(forms.Form):
    qr_code = forms.CharField(
        label="QR code PIX (copia e cola)",
        widget=forms.Textarea(attrs={"rows": 4, "style": "width: 100%"}),
        help_text="Cole o código da cobrança gerada na plataforma do credenciador.",
    )
    amount = forms.CharField(
        label="Valor (opcional)",
        required=False,
        help_text="Sem ele, usa o valor de dentro do QR.",
    )


class ConcludeForm(forms.Form):
    platform_login = forms.CharField(label="Login da plataforma")
    platform_password = forms.CharField(label="Senha da plataforma")
    platform_url = forms.CharField(label="URL da plataforma (opcional)", required=False)
    platform_notes = forms.CharField(
        label="Observações (opcional)",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "style": "width: 100%"}),
    )


# (kind, título, form, rótulo do botão) — o miolo de cada ação é um handler abaixo.
_ACTIONS = {
    "fee-pay": ("Pagar 1ª parcela da taxa (à vista)", FeeForm, "Pagar agora"),
    "fee-schedule": ("Agendar 2ª parcela da taxa", FeeForm, "Agendar"),
    "conclude": ("Concluir matrícula → aluno", ConcludeForm, "Concluir matrícula"),
}


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    """Matrículas SOMENTE LEITURA + as 3 ações do fluxo da taxa via botões (object-tools).

    Nada de editar campo na mão: status/fees andam pela camada de serviço, nunca por save do admin.
    """

    change_form_template = "admin/users/enrollment/change_form.html"
    list_display = ("external_id", "student_name", "hub", "status", "updated_at")
    list_filter = ("status", "hub")
    search_fields = ("user__profile__name", "user__profile__cpf")
    ordering = ("-updated_at",)

    @admin.display(description="aluno")
    def student_name(self, obj: Enrollment) -> str:
        profile = getattr(obj.user, "profile", None)
        return (profile.name if profile else None) or str(obj.user.external_id)

    @admin.display(description="taxa (parcelas)")
    def fees_summary(self, obj: Enrollment) -> str:
        facts = enrollment_iface.fee_facts(obj)
        first = "1ª PAGA" if facts.get("first_paid") else "1ª pendente"
        second = "2ª AGENDADA" if facts.get("second_scheduled") else "2ª pendente"
        return f"{first} · {second}"

    def get_readonly_fields(self, request, obj=None):
        fields = [f.name for f in self.model._meta.fields]
        return ["fees_summary", *fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        # a página de "change" é o painel da matrícula (read-only + botões de ação).
        return request.user.is_superuser

    def get_urls(self):
        wrap = self.admin_site.admin_view
        custom = [
            path(
                "<path:object_id>/action/<str:kind>/",
                wrap(self.enrollment_action_view),
                name="users_enrollment_action",
            ),
        ]
        return custom + super().get_urls()

    def enrollment_action_view(self, request, object_id, kind):
        if not request.user.is_superuser:
            raise PermissionDenied
        if kind not in _ACTIONS:
            raise PermissionDenied
        enr = self.get_object(request, object_id)
        if enr is None:
            messages.error(request, "Matrícula não encontrada.")
            return redirect("admin:users_enrollment_changelist")

        back_url = reverse("admin:users_enrollment_change", args=[enr.pk])
        coordinator = enr.hub.coordinator
        title, form_class, submit_label = _ACTIONS[kind]

        warning = None
        if coordinator is None:
            warning = (
                "O polo desta matrícula não tem coordenador — defina o coordenador do hub antes "
                "(o serviço registra a ação em nome dele)."
            )

        form = form_class(request.POST or None)
        if request.method == "POST" and coordinator is not None and form.is_valid():
            data = form.cleaned_data
            try:
                if kind == "fee-pay":
                    enrollment_iface.pay_fee(
                        enrollment_external_id=str(enr.external_id),
                        coordinator=coordinator,
                        qr_code=data["qr_code"],
                        amount=data["amount"] or None,
                    )
                    done = "1ª parcela enfileirada — o status muda quando o PIX confirmar pago."
                elif kind == "fee-schedule":
                    enrollment_iface.schedule_fee(
                        enrollment_external_id=str(enr.external_id),
                        coordinator=coordinator,
                        qr_code=data["qr_code"],
                        amount=data["amount"] or None,
                    )
                    done = "2ª parcela agendada pelo vencimento do QR."
                else:
                    enrollment_iface.conclude(
                        enrollment_external_id=str(enr.external_id),
                        coordinator=coordinator,
                        platform_login=data["platform_login"],
                        platform_password=data["platform_password"],
                        platform_url=data["platform_url"] or None,
                        platform_notes=data["platform_notes"] or None,
                    )
                    done = "Matrícula concluída — o aluno virou student e recebeu as credenciais."
            except DomainError as exc:
                messages.error(request, f"{exc.detail} [{exc.code}]")
            else:
                logger.info(
                    "enrollment.admin_action",
                    action=kind,
                    external_id=str(enr.external_id),
                    admin_user=str(request.user.external_id),
                    as_coordinator=str(coordinator.external_id),
                )
                messages.success(request, done)
                return redirect(back_url)

        context = {
            **self.admin_site.each_context(request),
            "title": title,
            "enrollment": enr,
            "coordinator": coordinator,
            "warning": warning,
            "form": form,
            "submit_label": submit_label,
            "back_url": back_url,
            "opts": self.model._meta,
        }
        return TemplateResponse(
            request, "admin/users/enrollment/action_form.html", context
        )

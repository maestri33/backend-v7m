from django.contrib import admin, messages
from django.utils import timezone

from notifications.models import NotificationTemplate
from notifications.seed import seed_notification_templates


@admin.register(NotificationTemplate)
class NotificationTemplateAdmin(admin.ModelAdmin):
    list_display = ("event", "channels", "active", "storytelling", "is_customized", "updated_at")
    list_filter = ("active", "is_tts", "storytelling", "channels")
    search_fields = ("event", "title", "subject", "body")
    actions = ("restore_defaults",)
    fieldsets = (
        ("Evento fixo", {"fields": ("event", "active", "context_keys")}),
        ("Conteúdo", {"fields": ("title", "subject", "body")}),
        ("Storytelling", {"fields": ("storytelling", "story_prompt")}),
        ("Entrega", {"fields": ("channels", "is_tts", "mail_template", "media_url", "media_type")}),
        ("Auditoria", {"fields": ("customized_at", "customized_by", "created_at", "updated_at")}),
    )

    @admin.display(boolean=True, description="Personalizado")
    def is_customized(self, obj):
        return obj.customized_at is not None

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return (
            "event",
            "context_keys",
            "customized_at",
            "customized_by",
            "created_at",
            "updated_at",
        )

    def save_model(self, request, obj, form, change):
        if change and form.changed_data:
            obj.customized_at = timezone.now()
            obj.customized_by = request.user
        super().save_model(request, obj, form, change)

    @admin.action(description="Restaurar padrão do código")
    def restore_defaults(self, request, queryset):
        events = set(queryset.values_list("event", flat=True))
        result = seed_notification_templates(force_events=events)
        self.message_user(
            request,
            f"{result['updated']} conteúdo(s) restaurado(s).",
            level=messages.SUCCESS,
        )

from __future__ import annotations

import hashlib
import json

from django.db import transaction

from notifications.defaults import NOTIFICATION_DEFAULTS


EDITABLE_FIELDS = (
    "title",
    "subject",
    "body",
    "channels",
    "is_tts",
    "storytelling",
    "story_prompt",
    "media_url",
    "media_type",
    "mail_template",
    "active",
    "context_keys",
)


def default_hash(data: dict) -> str:
    serializable = {field: data.get(field) for field in EDITABLE_FIELDS}
    raw = json.dumps(serializable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@transaction.atomic
def seed_notification_templates(*, force_events: set[str] | None = None) -> dict[str, int]:
    from notifications.models import NotificationTemplate

    force_events = force_events or set()
    created = 0
    updated = 0
    preserved = 0
    for event, source in NOTIFICATION_DEFAULTS.items():
        data = {field: source.get(field) for field in EDITABLE_FIELDS}
        digest = default_hash(data)
        template, was_created = NotificationTemplate.objects.get_or_create(
            event=event,
            defaults={**data, "default_hash": digest},
        )
        if was_created:
            created += 1
            continue
        if template.customized_at is not None and event not in force_events:
            preserved += 1
            continue
        current = {field: getattr(template, field) for field in EDITABLE_FIELDS}
        if default_hash(current) == digest and event not in force_events:
            continue
        for field, value in data.items():
            setattr(template, field, value)
        template.default_hash = digest
        if event in force_events:
            template.customized_at = None
            template.customized_by = None
        template.save()
        updated += 1
    return {"created": created, "updated": updated, "preserved": preserved}


def seed_after_migrate(**_kwargs):
    seed_notification_templates()

"""Superfície pública in-process do `student` (CONVENTION §3): o que a API e o enrollment chamam.

Fina de propósito — só reexporta o `service`. A borda (api/) traduz `external_id` ↔ User/objeto e
trata os erros (`StudentError`); a regra mora no service.
"""

from users.roles.student.service import (
    StudentError,
    clear_documentation,
    create_from_enrollment,
    decide_document,
    detail_for_coordinator,
    get_for_user_external_id,
    grade_exam,
    issue_diploma,
    list_document_reviews_for_hub,
    list_for_hub,
    list_for_staff,
    list_pendencies,
    open_pendency,
    register_pickup,
    resolve_pendency,
    schedule_exam,
    set_blood_type,
    to_dict,
    upload_document,
)

__all__ = [
    "StudentError",
    "clear_documentation",
    "create_from_enrollment",
    "decide_document",
    "detail_for_coordinator",
    "get_for_user_external_id",
    "grade_exam",
    "issue_diploma",
    "list_document_reviews_for_hub",
    "list_for_hub",
    "list_for_staff",
    "list_pendencies",
    "open_pendency",
    "register_pickup",
    "resolve_pendency",
    "schedule_exam",
    "set_blood_type",
    "to_dict",
    "upload_document",
]

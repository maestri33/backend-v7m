from __future__ import annotations

from datetime import date

from django.conf import settings

from notifications.models import NotificationTemplate

_MONTHS = (
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
)


def age_from(birth_date) -> int | None:
    if not birth_date:
        return None
    today = date.today()
    return today.year - birth_date.year - (
        (today.month, today.day) < (birth_date.month, birth_date.day)
    )


def _age_context(age: int | None) -> str:
    if age is None:
        return ""
    if age >= 50:
        return (
            f"A pessoa tem cerca de {age} anos: honre, com respeito e sem espanto, "
            "a coragem de retomar os estudos mais tarde na vida."
        )
    if age >= 30:
        return (
            f"A pessoa tem cerca de {age} anos: reconheça a determinação de estudar "
            "conciliando com o trabalho e a vida adulta."
        )
    return (
        f"A pessoa tem cerca de {age} anos: celebre, com entusiasmo, que está "
        "garantindo o futuro cedo."
    )


def generate_story(
    template: NotificationTemplate,
    *,
    name: str,
    birth_date,
    fallback: str,
) -> str:
    """Personaliza marcos; qualquer falha preserva o conteúdo fixo do banco."""
    if (
        not getattr(settings, "NOTIFICATION_STORYTELLING_ENABLED", False)
        or not template.storytelling
        or not template.story_prompt
        or not name.strip()
    ):
        return fallback
    try:
        today = date.today()
        instruction = template.story_prompt.format_map(
            {
                "name": name,
                "nome": name,
                "data_hoje": f"{today.day} de {_MONTHS[today.month - 1]} de {today.year}",
                "faixa_etaria": _age_context(age_from(birth_date)),
            }
        )
        from integrations.ai import service as ai

        output = ai.generate_text(
            f"Escreva a mensagem para {name}.",
            caller=f"story.{template.event}",
            instruction=instruction,
            temperature=0.6,
            max_tokens=1000,
            model="deepseek-v4-pro",
        )
        clean = (output or "").strip().replace("**", "")
        if len(clean) < 20 or name.lower() not in clean.lower():
            return fallback
        return clean
    except Exception:  # noqa: BLE001 - IA nunca pode impedir uma notificação de negócio
        return fallback

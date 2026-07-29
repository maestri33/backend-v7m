"""Assistente do painel — responde com os DADOS REAIS do promotor.

O contrato (A5.5) prevê CopilotKit em produção; enquanto o runtime não existe, esta versão
determinística cobre o que o protótipo mostra: pagamento, meta, leads, chave Pix, link e
situação do treino. Nada de inventar — toda resposta sai de uma leitura do banco, e o que
sai da lista de assuntos vira o fallback com as sugestões.

Regra dura: a chave Pix só aparece MASCARADA (a mesma de `panel_data.pix_account`).
"""

from __future__ import annotations

import unicodedata

from finance import config as money_config
from users.roles.promoter import service as promoter_iface
from users.roles.training import service as training_iface

from web import panel_data

SUGGESTIONS = (
    "Quando cai meu pagamento?",
    "Como está minha meta?",
    "Quantos leads eu tenho?",
    "Qual é meu link?",
)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", (s or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def answer(promoter, question: str) -> str:
    q = _norm(question)
    user = promoter.user
    summary = promoter_iface.summary(user)

    if any(
        w in q
        for w in (
            "pagamento",
            "pagar",
            "cai",
            "deposito",
            "receb",
            "dinheiro",
            "pix quando",
        )
    ):
        total = panel_data.money(summary["week_commission_total"])
        state = panel_data.cycle_state(user)
        when = _closing_label(summary["next_closing_at"])
        if state["state"] == "processing":
            return (
                f"Seu ciclo fechou e o Pix de {total} já foi solicitado ao banco — "
                "pode cair na sua chave a qualquer momento."
            )
        if summary["week_paid_leads"] == 0:
            return (
                f"Esta semana ainda não teve matrícula paga. O ciclo fecha {when} e o Pix "
                f"sai automático — a primeira matrícula já garante "
                f"{panel_data.money(money_config.direct_amount())}."
            )
        return (
            f"Você tem {total} pra receber no fechamento de {when}. "
            f"São {summary['week_paid_leads']} matrícula(s) paga(s) nesta semana, e o Pix "
            "cai automático na sua chave cadastrada."
        )

    if any(w in q for w in ("meta", "bonus", "estrela", "500")):
        falta = max(0, summary["week_goal"] - summary["week_paid_leads"])
        if summary["goal_reached"]:
            return (
                f"Meta batida! {summary['week_paid_leads']}/{summary['week_goal']} — o bônus de "
                f"{panel_data.money(summary['bonus_amount'])} entra no fechamento desta semana."
            )
        return (
            f"Você está em {summary['week_paid_leads']}/{summary['week_goal']}. "
            f"Faltam {falta} matrícula(s) paga(s) para o bônus de "
            f"{panel_data.money(summary['bonus_amount'])}."
        )

    if any(w in q for w in ("lead", "indica", "aluno", "kanban", "semana")):
        data = panel_data.leads(user)
        by = {c["key"]: len(c["items"]) for c in data["columns"]}
        if not data["total"]:
            return (
                "Você ainda não tem indicados. Compartilhe seu link em Matricular — quem "
                "entrar por ele já fica vinculado a você."
            )
        return (
            f"Você tem {data['total']} indicado(s): {by.get('whats', 0)} só com WhatsApp, "
            f"{by.get('identified', 0)} identificado(s), {by.get('payment', 0)} escolheu "
            f"pagamento e {by.get('done', 0)} matriculado(s)."
        )

    if any(w in q for w in ("pix", "chave", "banco", "conta")):
        pix = panel_data.pix_account(user)
        if pix is None:
            return "Você ainda não tem chave Pix cadastrada. Cadastre em Finanças para receber."
        return (
            f"Sua chave está no {pix['bank']} — {pix['type']} {pix['masked']}, no nome de "
            f"{pix['holder']}. É nela que o Pix cai no fechamento."
        )

    if any(w in q for w in ("link", "convite", "convidar", "compartilh", "indicar")):
        return (
            f"Seu link é {promoter_iface.to_dict(promoter)['ref_url']} — está em Matricular, "
            "com botão de copiar e os canais (WhatsApp, Telegram, Facebook, Instagram)."
        )

    if any(w in q for w in ("aula", "treino", "curso", "lms", "bloquead")):
        if training_iface.is_locked(user):
            return "Você tem aula pendente no treino. Conclua para liberar o painel."
        return "Seu treino está em dia — nenhuma aula pendente."

    if any(w in q for w in ("cadastro", "documento", "selfie", "aprovad")):
        return (
            "Seu cadastro está aprovado — você já é promotor. Os arquivos enviados ficam em "
            "Dados, somente leitura."
        )

    return (
        "Eu vejo seus dados de promotor. Pode perguntar sobre: pagamento e quando cai, "
        "sua meta e o bônus, seus leads da semana, sua chave Pix, seu link de indicação e "
        "o treino."
    )


_DIAS = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")
_MESES = (
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


def _closing_label(iso: str) -> str:
    from datetime import datetime

    try:
        dt = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return "sexta 18h"
    return f"{_DIAS[dt.weekday()]}, {dt.day} de {_MESES[dt.month - 1]} · {dt.hour}h"

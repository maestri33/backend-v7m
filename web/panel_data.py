"""Leituras do painel do promotor — derivadas dos models que JÁ existem.

O checklist pede `Cycle`, `Referral` e um endpoint de arquivos que o backend ainda não tem.
Em vez de esperar por eles, este módulo monta as mesmas telas a partir do que existe hoje:

- ciclos            ← agrega `finance.Commission` por semana (a mesma janela do fechamento)
- estágio do lead   ← `Lead.status` + `Checkout` + nome no `Profile`
- chave Pix         ← `Profile.pix_key` + `asaas.PixKey` (banco/titular), sempre MASCARADA
- arquivos          ← `users.documents` + `validation_result["extracted"]`

Quando os models canônicos existirem, troca-se a fonte aqui dentro sem tocar nas telas.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from finance import config as money_config
from finance.models import Commission, PaymentRequest
from users.documents import service as documents_iface
from users.profiles import interface as profiles

_MES = (
    "jan",
    "fev",
    "mar",
    "abr",
    "mai",
    "jun",
    "jul",
    "ago",
    "set",
    "out",
    "nov",
    "dez",
)


def _short(d) -> str:
    return f"{d.day} {_MES[d.month - 1]}"


def money(value) -> str:
    """'1234.5' → 'R$ 1.234,50' (regra A6: pt-BR com milhar em todo lugar)."""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "R$ 0,00"
    inteiro, cents = divmod(round(abs(n) * 100), 100)
    return "R$ " + f"{inteiro:,}".replace(",", ".") + f",{cents:02d}"


def initials(name: str | None) -> str:
    parts = [p for p in (name or "").split() if p]
    if not parts:
        return "?"
    return (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper()


# ── ciclos ────────────────────────────────────────────────────────────────────
def cycles(user, limit: int = 12) -> dict:
    """Histórico semanal do promotor + total recebido + qual foi o recorde.

    Agrupa as comissões pela semana do `created_at` usando a MESMA janela do fechamento
    (`finance.commissions.week_window`), para o histórico bater com o que foi pago."""
    from finance.interface.commissions import week_window

    rows = list(
        Commission.objects.filter(payee=user).order_by("-created_at")[: limit * 12]
    )
    if not rows:
        # `total` sai SEMPRE formatado — o template compara com "R$ 0,00" pra decidir se mostra
        # o bloco "Já recebido no total" (contrato: nenhuma tela exibe zero cru).
        return {"cycles": [], "total": money(0), "record_id": None}

    buckets: dict[str, dict] = defaultdict(
        lambda: {
            "total": Decimal("0"),
            "students": [],
            "bonus": Decimal("0"),
            "status": set(),
        }
    )
    for c in rows:
        start, end = week_window(c.created_at)
        key = start.date().isoformat()
        b = buckets[key]
        b["start"], b["end"] = start.date(), (end - timedelta(seconds=1)).date()
        b["total"] += c.amount
        b["status"].add(c.status)
        if c.source_type == Commission.Source.BONUS:
            b["bonus"] += c.amount
        else:
            b["students"].append(
                {"name": _commission_student(c), "amount": money(c.amount)}
            )

    out = []
    for key, b in sorted(buckets.items(), reverse=True)[:limit]:
        st = b["status"]
        status = (
            "paid"
            if st == {Commission.Status.PAID}
            else ("processing" if Commission.Status.PENDING in st else "paid")
        )
        out.append(
            {
                "id": key,
                "range": f"{_short(b['start'])} – {_short(b['end'])}",
                "total": money(b["total"]),
                "total_raw": b["total"],
                "students": b["students"],
                "count": len(b["students"]),
                "bonus": money(b["bonus"]) if b["bonus"] else None,
                "status": status,
            }
        )
    paid_total = sum(
        (c.amount for c in rows if c.status == Commission.Status.PAID), Decimal("0")
    )
    record = max(out, key=lambda c: c["total_raw"], default=None)
    return {
        "cycles": out,
        "total": money(paid_total),
        "record_id": record["id"] if record and record["total_raw"] > 0 else None,
    }


def _commission_student(c: Commission) -> str:
    """Nome de quem gerou a comissão (o lead), via `source_external_id`."""
    from users.roles.lead.models import Lead

    lead = (
        Lead.objects.filter(external_id=c.source_external_id)
        .select_related("user")
        .first()
    )
    if lead is None:
        return "Aluno"
    p = profiles.get(lead.user)
    return (p.name if p and p.name else None) or "Aluno"


def cycle_detail(user, cycle_id: str) -> dict | None:
    for c in cycles(user)["cycles"]:
        if c["id"] == cycle_id:
            return c
    return None


# ── leads / kanban ────────────────────────────────────────────────────────────
_STAGES = (
    ("whats", "Só WhatsApp", "Entrou pelo seu link"),
    ("identified", "Identificado", "Já se identificou"),
    ("payment", "Escolheu pagamento", "Falta pagar"),
    ("done", "Matriculado", "Comissão garantida"),
)


def leads(user) -> dict:
    """Leads do promotor com o estágio derivado (whats → identified → payment → done)."""
    from users.roles.lead.models import Checkout, Lead

    # "Leads desta semana" tem de ser DESTA SEMANA: o kanban trazia todos os leads de sempre
    # sob esse título, então o número na tela não batia com nada (protótipo: `weekLeads`).
    from finance.interface.commissions import week_window

    inicio, fim = week_window()
    rows = (
        Lead.objects.filter(
            promoter=user, self_study=False, created_at__gte=inicio, created_at__lt=fim
        )
        .select_related("user")
        .order_by("-created_at")[:200]
    )
    paid_ids = set(
        Checkout.objects.filter(lead__in=rows).values_list("lead_id", flat=True)
    )
    items = []
    for lead in rows:
        p = profiles.get(lead.user)
        name = (p.name if p else None) or None
        if lead.status == Lead.Status.PAID:
            stage = "done"
        elif lead.id in paid_ids:
            stage = "payment"
        elif name:
            stage = "identified"
        else:
            stage = "whats"
        items.append(
            {
                "name": name,
                "phone": _fmt_phone(p.phone if p else None),
                "stage": stage,
                "initials": initials(name),
                "amount": money(money_config.direct_amount())
                if stage == "done"
                else None,
            }
        )
    columns = [
        {
            "key": key,
            "label": label,
            "hint": hint,
            "items": [i for i in items if i["stage"] == key],
        }
        for key, label, hint in _STAGES
    ]
    return {"items": items, "columns": columns, "total": len(items)}


def _fmt_phone(phone: str | None) -> str:
    d = "".join(c for c in (phone or "") if c.isdigit())
    d = d[2:] if d.startswith("55") and len(d) > 11 else d
    if len(d) == 11:
        return f"({d[:2]}) {d[2:7]}-{d[7:]}"
    if len(d) == 10:
        return f"({d[:2]}) {d[2:6]}-{d[6:]}"
    return phone or ""


# ── chave Pix (sempre mascarada) ──────────────────────────────────────────────
def pix_account(user) -> dict | None:
    """Chave do promotor MASCARADA — a chave completa nunca sai daqui (contrato B6)."""
    from integrations.bank.asaas.models import PixKey

    p = profiles.get(user)
    key = getattr(p, "pix_key", None)
    if not key:
        return None
    row = PixKey.objects.filter(key=key).first()
    return {
        "bank": (getattr(row, "bank_name", "") or "Banco não informado"),
        # 2 primeiras letras do banco, como no protótipo ("Nubank" → "NU"). `initials()` pega
        # 1ª+última palavra e devolvia só "N" pra banco de nome único.
        "bank_initials": (getattr(row, "bank_name", "") or "Banco")[:2].upper(),
        "type": _pix_type_label(
            getattr(p, "pix_key_type", "") or getattr(row, "key_type", "")
        ),
        "masked": _mask_key(key),
        "holder": (getattr(row, "holder_name", "") or (p.name if p else "") or "—"),
        "verified_at": getattr(row, "created_at", None),
    }


_PIX_LABELS = {
    "CPF": "Chave CPF",
    "CNPJ": "Chave CNPJ",
    "EMAIL": "Chave e-mail",
    "PHONE": "Chave celular",
    "EVP": "Chave aleatória",
}


def _pix_type_label(t: str) -> str:
    return _PIX_LABELS.get((t or "").upper(), "Chave Pix")


def _mask_key(key: str) -> str:
    key = (key or "").strip()
    if "@" in key:
        user_part, _, dom = key.partition("@")
        return f"{user_part[:2]}•••@{dom}"
    d = "".join(c for c in key if c.isdigit())
    if len(d) == 11:  # CPF/celular
        return f"•••.{d[3:6]}.{d[6:9]}-{d[9:]}"
    return f"•••{key[-6:]}" if len(key) > 6 else "•••"


# ── arquivos do cadastro (Dados pessoais) ─────────────────────────────────────
_EXTRACT_LABELS = {
    "name": "Nome",
    "number": "RG",
    "rg": "RG",
    "birth_date": "Nascimento",
    "date_of_birth": "Nascimento",
    "mother_name": "Mãe",
    "father_name": "Pai",
    "issuing_agency": "Órgão emissor",
    "cep": "CEP",
    "zipcode": "CEP",
    "street": "Logradouro",
    "neighborhood": "Bairro",
    "city": "Cidade",
    "state": "UF",
}


def files(user) -> list[dict]:
    """Cards de arquivo do cadastro + os campos que a IA extraiu (somente leitura)."""
    ext = str(user.external_id)
    out = []
    cand = getattr(user, "candidate", None)
    doc_type = getattr(cand, "doc_type", None)
    if doc_type:
        sub = documents_iface.get_doc_sub(ext, doc_type)
        if sub is not None:
            out.append(
                {
                    "key": "documento",
                    "title": "Documento com foto",
                    "filename": f"{doc_type}-frente-verso.jpg",
                    "kind": "doc",
                    "status": getattr(sub, "validation_status", None),
                    "extracted": _extracted(
                        getattr(sub, "validation_result", None), sub
                    ),
                }
            )
    proof = documents_iface.get_address_proof(ext)
    if proof is not None and getattr(proof, "photo", None):
        out.append(
            {
                "key": "comprovante",
                "title": "Comprovante de endereço",
                "filename": "comprovante.pdf",
                "kind": "pdf",
                "status": getattr(proof, "validation_status", None),
                "extracted": _address_extracted(user),
            }
        )
    if cand is not None and getattr(cand, "selfie_image", None):
        out.append(
            {
                "key": "selfie",
                "title": "Selfie de verificação",
                "filename": "selfie.jpg",
                "kind": "selfie",
                "status": getattr(cand, "selfie_status", None),
                "extracted": [{"label": "Prova de vida", "value": "Aprovada"}]
                if getattr(cand, "selfie_verified", False)
                else [],
            }
        )
    return out


def _extracted(result, sub) -> list[dict]:
    data = {}
    if isinstance(result, dict):
        data = result.get("extracted") or {}
    pairs = []
    for k, label in _EXTRACT_LABELS.items():
        v = data.get(k) or getattr(sub, k, None)
        if v and not any(p["label"] == label for p in pairs):
            pairs.append({"label": label, "value": str(v)})
    return pairs[:6]


def _address_extracted(user) -> list[dict]:
    from users.address import interface as address_iface

    a = address_iface.as_public_dict(
        address_iface.get_by_external_id(str(user.external_id))
    )
    pairs = []
    for k in ("zipcode", "street", "neighborhood", "city", "state"):
        if a.get(k):
            pairs.append({"label": _EXTRACT_LABELS.get(k, k), "value": a[k]})
    return pairs


# ── estado do ciclo corrente (chip do hero) ───────────────────────────────────
_CYCLE_LABEL = {
    "open": ("ciclo aberto", "b-warn"),
    "processing": ("enviando o Pix", "b-info"),
    "paid": ("pago", "b-ok"),
    "failed": ("falhou — o polo já foi avisado", "b-danger"),
}


def cycle_state(user) -> dict:
    state = "open"
    try:
        st = (
            PaymentRequest.objects.filter(payee=user)
            .order_by("-created_at")
            .values_list("status", flat=True)
            .first()
        )
        if st == PaymentRequest.Status.PAID:
            state = "paid"
        elif st == PaymentRequest.Status.FAILED:
            state = "failed"
        elif st:
            state = "processing"
    except Exception:  # noqa: BLE001 — o painel nunca cai por causa do chip
        state = "open"
    label, badge = _CYCLE_LABEL[state]
    return {"state": state, "label": label, "badge": badge}

"""Testes diretos dos helpers PUROS de `users.roles._analysis` — a régua de TTL/staleness que os 3
funis (enrollment RG, candidate doc, selfie) compartilham pra decidir "pending estourado → review".

O explorador de arquitetura apontou: esses helpers foram extraídos "pra testabilidade" mas NÃO tinham
teste direto — os bugs de verdade moravam em COMO os callers os chamavam (ex.: GET que mutava/notificava,
corrigido em c7729d1/2301e0f). Aqui a régua fica travada por contrato, independente dos callers.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from django.test import override_settings
from django.utils import timezone

from users.roles import _analysis

# ── started_at_from: parse do `analysis_started_at` (ISO gravado no validation_result) ──


def test_started_at_from_none_e_lixo_viram_none():
    assert _analysis.started_at_from(None) is None
    assert _analysis.started_at_from("") is None
    assert _analysis.started_at_from("não-é-data") is None


def test_started_at_from_datetime_passa_direto():
    dt = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)  # aware → coerce não toca
    assert _analysis.started_at_from(dt) == dt


def test_started_at_from_iso_naive_coerce_vira_utc():
    got = _analysis.started_at_from("2026-01-01T12:00:00")  # coerce_tz=True (default)
    assert got == datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def test_started_at_from_iso_naive_sem_coerce_fica_naive():
    got = _analysis.started_at_from("2026-01-01T12:00:00", coerce_tz=False)
    assert got == datetime.fromisoformat("2026-01-01T12:00:00")  # naive, sem coerção
    assert got.tzinfo is None


# ── ttl_seconds ──


@override_settings(ANALYSIS_TTL_SECONDS=300)
def test_ttl_seconds_le_do_settings():
    assert _analysis.ttl_seconds() == 300


# ── is_stale: o coração da régua (só `pending` cujo prazo estourou vira review) ──


@override_settings(ANALYSIS_TTL_SECONDS=120)
def test_is_stale_pending_estourado():
    started = timezone.now() - timedelta(seconds=200)  # > 120
    assert _analysis.is_stale(_analysis.PENDING, started) is True


@override_settings(ANALYSIS_TTL_SECONDS=120)
def test_is_stale_pending_dentro_do_prazo():
    started = timezone.now() - timedelta(seconds=30)  # < 120
    assert _analysis.is_stale(_analysis.PENDING, started) is False


def test_is_stale_so_pega_pending():
    old = timezone.now() - timedelta(days=1)
    assert _analysis.is_stale(_analysis.APPROVED, old) is False
    assert _analysis.is_stale(_analysis.REVIEW, old) is False
    assert _analysis.is_stale(None, old) is False


def test_is_stale_sem_started_at():
    assert _analysis.is_stale(_analysis.PENDING, None) is False


# ── expires_at ──


@override_settings(ANALYSIS_TTL_SECONDS=120)
def test_expires_at_soma_ttl():
    started = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    assert _analysis.expires_at(started) == started + timedelta(seconds=120)


def test_expires_at_none():
    assert _analysis.expires_at(None) is None


# ── ack: o que o front recebe numa mutação que dispara análise (já reflete o TTL) ──


@override_settings(ANALYSIS_TTL_SECONDS=120, ANALYSIS_POLL_MS=2500)
def test_ack_pending_no_prazo():
    started = timezone.now() - timedelta(seconds=10)
    ack = _analysis.ack(_analysis.PENDING, started)
    assert ack["analysis_status"] == _analysis.PENDING
    assert ack["poll_after_ms"] == 2500
    assert ack["expires_at"] is not None


@override_settings(ANALYSIS_TTL_SECONDS=120)
def test_ack_pending_estourado_ja_reflete_review():
    started = timezone.now() - timedelta(seconds=500)
    ack = _analysis.ack(_analysis.PENDING, started)
    assert (
        ack["analysis_status"] == _analysis.REVIEW
    )  # o TTL já virou, sem tocar no banco


def test_ack_status_none_vira_pending():
    ack = _analysis.ack(None, None)
    assert ack["analysis_status"] == _analysis.PENDING
    assert ack["expires_at"] is None

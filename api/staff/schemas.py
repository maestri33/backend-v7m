"""Schemas Pydantic v2 do grupo Staff (Administração / Superuser)."""

from __future__ import annotations

from ninja import Schema


class StaffCheckIn(Schema):
    cpf: str | None = None
    phone: str | None = None
    external_id: str | None = None


class StaffCheckOut(Schema):
    found: bool
    external_id: str | None = None
    otp_sent: bool
    otp_wait: int | None = None


class StaffLoginIn(Schema):
    external_id: str
    otp: str


class HubCreateIn(Schema):
    brand: str
    coordinator_external_id: str | None = None


class SetCoordinatorIn(Schema):
    coordinator_external_id: str


class HubAddressIn(Schema):
    cep: str
    number: str | None = None
    complement: str | None = None


class HubOut(Schema):
    external_id: str
    brand: str
    coordinator_external_id: str | None
    is_default: bool


class PromoterOut(Schema):
    external_id: str
    name: str | None


class PlatformCredentialsIn(Schema):
    platform_login: str
    platform_password: str
    platform_url: str | None = None
    platform_notes: str | None = None


class PhoneIn(Schema):
    phone: str

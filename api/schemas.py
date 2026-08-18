"""Schemas compartilhados entre grupos da API Ninja (compatibilidade)."""

from __future__ import annotations

from api.schemas import (
    AddressCepIn,
    AddressDataIn,
    CheckIn,
    CheckOut,
    LoginIn,
    MaterialIn,
    MaterialUpdateIn,
    PublicAddressOut,
    RefreshIn,
    StudentPlatformFields,
    TokenOut,
)

__all__ = [
    "AddressCepIn",
    "AddressDataIn",
    "PublicAddressOut",
    "CheckIn",
    "CheckOut",
    "LoginIn",
    "RefreshIn",
    "TokenOut",
    "StudentPlatformFields",
    "MaterialIn",
    "MaterialUpdateIn",
]

# ponytail: fixtures mínimos — db + client. SQLite em memória por default.
import os

# Default SQLite ANTES do Django ler settings — mas RESPEITA um TEST_DATABASE_URL exportado:
# no SQLite todo select_for_update é NO-OP, então os locks de concorrência (KYC, mark_paid,
# checkout) só são exercitados de verdade quando o CI aponta pra um Postgres de serviço.
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", "sqlite:///:memory:")

import pytest
from django.test import Client


@pytest.fixture(autouse=True)
def test_settings():
    from django.conf import settings
    from django.core.cache import cache

    settings.TEST_MODE = True
    settings.APP_ENV = "test"
    settings.BOT_SERVICE_SECRET = "test_bot_secret"
    settings.BOT_SERVICE_HEADER = "x-bot-service-token"
    # zera contadores de throttle/cota entre testes (o cache LocMem persiste no processo, senão
    # testes que batem em /auth/check várias vezes tripariam o rate-limit uns dos outros).
    cache.clear()


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def bot_headers():
    return {"HTTP_X_BOT_SERVICE_TOKEN": "test_bot_secret"}

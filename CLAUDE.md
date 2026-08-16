@AGENTS.md

## Convenções LOCAIS deste repo (têm precedência sobre o genérico acima)

- API django-ninja em **grupos por público** fabricados por `api/base.py::build_group`
  (clients/collaborators/leadership/staff), montados em `core/urls.py` sob `/api/v1/`.
  Auth default JWT (django-ninja-jwt); staff usa `require_superuser` DENTRO do endpoint.
- Contrato de erro único: TODO 4xx/5xx sai `{detail, code, …extra}` com `code` UPPER_SNAKE
  estável — o front faz `switch(code)`, nunca parseia texto. Endpoints deste repo **não**
  declaram `response=` (o envelope e os testes de contrato fazem esse papel); siga o padrão
  do grupo em que estiver mexendo.
- Regra de negócio em `service.py` por domínio (`users/roles/*`); consultas em `interface/`;
  `integrations/` nunca contém regra de negócio (fronteira defendida por
  `tests/test_import_cycles.py`).
- Fila django-q2 broker ORM: default = fast (OTP/notify/checkout); tasks pesadas
  (visão/OCR/biometria/LLM) roteiam com `cluster=settings.Q_SLOW_CLUSTER`.
- Config 100% via `.env` (django-environ) com system checks que TRAVAM o boot.
- Testes: pytest em `tests/`, SQLite por default; o CI roda também em Postgres via
  `TEST_DATABASE_URL` (locks só são reais lá). Rode como o CI:
  `APP_ENV=test TEST_MODE_ALLOWED_HOSTS="$(hostname)" uv run pytest tests/`.
- Deps via **uv** (`uv.lock` pinado); psycopg fica fora do lock (instalado por fora).

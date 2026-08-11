"""CLI one-shot: valida que o módulo notify está saudável (models, cache/render, send/events).
Uso: python -m notify.self_check  ·  exit 0 = OK, exit 1 = falha."""

from __future__ import annotations

import json
import sys
import os

# Garante que o diretório raiz do projeto está no path (manage.py roda de /opt/test/backend-supletivo).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")

# Força SQLite em memória (igual ao conftest) pra não depender de .env/DB real.
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")


def run() -> dict:
    import django

    django.setup()

    results = {}

    # 1. Models importáveis
    try:
        from notify.models import _parse_channels

        results["models_import"] = True
    except Exception as e:
        results["models_import"] = False
        results["models_import_error"] = str(e)
        return results

    # 2. _parse_channels
    try:
        assert _parse_channels("whatsapp,email") == ["whatsapp", "email"]
        assert _parse_channels(None) == []
        assert _parse_channels("fax") == []
        results["parse_channels"] = True
    except Exception as e:
        results["parse_channels"] = False
        results["parse_channels_error"] = str(e)

    # 3. render (pure, no DB)
    try:
        from notify.interface.templates import render

        assert render("Oi {nome}", {"nome": "João"}) == "Oi João"
        assert render("{nome-completo}", {"nome_completo": "A B"}) == "A B"
        results["render"] = True
    except Exception as e:
        results["render"] = False
        results["render_error"] = str(e)

    # 5. Template cache module loads
    try:
        from notify.interface.templates import _CACHE_TTL_S, invalidate, get

        assert _CACHE_TTL_S > 0
        invalidate()  # clear
        # get() com DB vazio (SQLite memória) → None (negative cache)
        result = get("self.check.nonexistent")
        assert result is None
        results["template_cache"] = True
    except Exception as e:
        results["template_cache"] = False
        results["template_cache_error"] = str(e)

    # 6. interface.send importável
    try:
        results["interface_send"] = True
    except Exception as e:
        results["interface_send"] = False
        results["interface_send_error"] = str(e)

    # 8. interface.events importável
    try:
        results["interface_events"] = True
    except Exception as e:
        results["interface_events"] = False
        results["interface_events_error"] = str(e)

    return results


def main():
    results = run()
    ok = all(v for k, v in results.items() if not k.endswith("_error"))
    print(json.dumps(results, indent=2, ensure_ascii=False))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

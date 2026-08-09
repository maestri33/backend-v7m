# backend-supletivo

API Django (5.2 + django-ninja) da plataforma V7M. Monólito: regras de negócio, persistência e
integrações. O frontend, o bot e o serviço de notificações são projetos externos.

O glossário do domínio está em **`CONTEXT.md`** — leia antes de nomear qualquer coisa.

## Verificação

```bash
set -a; source .env.ci; set +a
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py check
uv run --extra dev python -m pytest
```

Rode os três antes de propor que uma mudança está pronta. O CI roda o mesmo conjunto, mais
`pip-audit --strict` — dependência com CVE conhecida derruba o gate.

O `pytest` vive no extra `dev`, então precisa do `--extra dev`. O `uv run pytest` que o README
documenta falha com `ModuleNotFoundError: No module named 'django'`, porque resolve num ambiente
efêmero sem as dependências do projeto.

## Vocabulário de arquitetura

Termos de estrutura, não de domínio (o domínio vive no `CONTEXT.md`):

- **Casca fina** — as rotas em `api/` só validam a borda e chamam o `interface/` do app in-process.
  Zero regra de negócio em `api/`.
- **`interface/`** — o ponto de entrada in-process de um app (`hub/interface/`, `finance/interface/`).
  É por onde outro app fala com ele; nunca importe o `service`/`models` de outro app direto.
- **`external_id`** — UUID imutável, é o único id que aparece na borda da API. A PK interna nunca
  sai. Relações internas são FK de verdade.
- **Grupo** — uma das cinco fatias da API em `/api/v1/<grupo>/`: `clients`, `collaborators`,
  `leadership`, `staff`, `tools`.
- **Erro de domínio** — `DomainError` e filhos borbulham pro handler central em `api/base.py`, que
  serializa `{detail, code, ...extra}`. Não capture pra reformatar na rota.

As docstrings de módulo dos models são densas e são fonte de verdade — leia a do arquivo antes de
mexer nele. Várias citam um documento `CONVENTION §N` que **não está versionado neste repo**; trate
as citações como referência histórica, não vá procurar o arquivo.

## Agent skills

### Issue tracker

GitHub Issues em `maestri33/backend-supletivo`, via ferramentas MCP (não há `gh` CLI neste
ambiente). Veja `docs/agents/issue-tracker.md`.

### Domain docs

Contexto único: `CONTEXT.md` na raiz + `docs/adr/`. Veja `docs/agents/domain.md`.

### Skills instaladas

Em `.claude/skills/`. As de terceiros vêm de [mattpocock/skills](https://github.com/mattpocock/skills)
(MIT) — cópias editáveis, não um plugin gerenciado; edite à vontade.

| Skill | Invocação | Para quê |
|---|---|---|
| `grill-with-docs` | usuário | Entrevista de alinhamento antes de construir, alimentando o glossário e as ADRs |
| `grilling` | modelo | A primitiva de entrevista por trás do `grill-with-docs` |
| `domain-modeling` | modelo | Afiar termos e escrever `CONTEXT.md` / ADR conforme resolvem |
| `codebase-design` | modelo | Vocabulário de módulo profundo, interface, seam |
| `tdd` | modelo | Loop red-green-refactor |
| `diagnosing-bugs` | modelo | Diagnóstico disciplinado, fase a fase |
| `two-axis-review` | modelo | Review em dois eixos: padrões do repo × fidelidade ao spec |
| `resolving-merge-conflicts` | modelo | Resolver conflito por intenção, hunk a hunk |
| `handoff` | usuário | Compactar a conversa pra outra sessão continuar |
| `writing-for-agents` | modelo | Padrão de escrita de doc que agente lê |
| `ci-cd-backend-v7m` | usuário | Vigiar o CD até subir em produção e devolver o handoff de API |

`two-axis-review` é o `code-review` do mattpocock, renomeado pra não colidir com o `/code-review`
embutido do Claude Code. São coisas diferentes: o embutido caça bug de correção no diff; este
confere o diff contra os padrões do repo e contra o spec de origem.

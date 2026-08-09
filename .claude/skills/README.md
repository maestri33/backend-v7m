# Skills do repo

Skills de projeto que o Claude Code carrega automaticamente a partir de `.claude/skills/<nome>/SKILL.md`.
São arquivos versionados: viajam com o clone, valem pra sessão local e pra sessão na web, e podem ser
editados à vontade.

## Próprias

- **`ci-cd-backend-v7m`** — vigia o CD até subir em produção e devolve o handoff de API pro frontend.
  Morava em `.cursor/skills/` (convenção do Cursor) e por isso o Claude Code nunca a enxergou.

## De terceiros

O restante é um subconjunto curado de [mattpocock/skills](https://github.com/mattpocock/skills),
copiado do commit `84fdeff` (2026-08-06).

Copiado, não instalado como plugin gerenciado — a rota `claude plugins install mattpocock-skills`
grava no nível da máquina, e as sessões na web clonam do zero a cada vez, então nada persistiria.

Adaptações feitas na cópia:

- **Layout plano.** Upstream agrupa em `skills/engineering/` e `skills/productivity/`; aqui cada
  skill é um diretório direto sob `.claude/skills/`.
- **`code-review` → `two-axis-review`.** Evita colisão com o `/code-review` embutido do Claude Code.
  A referência a ele no `tdd` foi atualizada junto.
- **`agents/openai.yaml` removidos.** São metadados do instalador `npx skills` pra Codex.
- **Referências a skills não instaladas religadas.** O `diagnosing-bugs` apontava pro
  `/improve-codebase-architecture`; agora aponta pro `/codebase-design`, que está aqui.
- **`/setup-matt-pocock-skills` não foi instalado.** O que ele geraria foi escrito à mão, adaptado
  a este repo: `docs/agents/issue-tracker.md` (GitHub via MCP — não há `gh` CLI nas sessões web),
  `docs/agents/domain.md` e o bloco `## Agent skills` do `CLAUDE.md`.

Skills upstream deixadas de fora por dependerem de uma disciplina de issue tracker que o repo não
pratica: `triage`, `wayfinder`, `to-tickets`, `to-spec`, `implement`, `ask-matt`,
`improve-codebase-architecture`, `research`, `prototype`, `wizard`, `grill-me`, `teach`,
`to-questionnaire`, `wait-what`.

Pra atualizar, compare com o upstream no commit fixado acima e reaplique as adaptações.

---

Os arquivos de terceiros são MIT — Copyright (c) 2026 Matt Pocock.
Texto integral: https://github.com/mattpocock/skills/blob/main/LICENSE

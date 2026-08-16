# Domain Docs

Como as skills de engenharia devem consumir a documentação de domínio deste repo.

## Antes de explorar, leia

- **`CONTEXT.md`** na raiz — o glossário canônico do domínio.
- **`docs/adr/`** — as ADRs que tocam a área em que você vai mexer.
- **`wiki/`** — notas de integração e de app (`wiki/users/`, `wiki/finance/`, `wiki/hub/`,
  `wiki/integrations/`). É documentação de apoio, não glossário: quando a wiki e o `CONTEXT.md`
  divergirem no nome de um conceito, o `CONTEXT.md` ganha.

Se `docs/adr/` ainda não existir, **siga em frente em silêncio**. Não sinalize a ausência e não
proponha criar preventivamente — o `/domain-modeling` cria sob demanda, quando um termo ou uma
decisão de fato se resolve.

## Layout

Contexto único (é o caso deste repo):

```
/
├── CONTEXT.md
├── docs/
│   ├── adr/
│   └── agents/
├── wiki/
└── users/ hub/ finance/ integrations/ core/ api/
```

Este repo é um monólito Django com um só contexto de domínio. Se um dia ele se partir em contextos
de verdade, o gatilho é criar um `CONTEXT-MAP.md` na raiz apontando pra um `CONTEXT.md` por contexto.

## Use o vocabulário do glossário

Quando o que você produz nomeia um conceito de domínio — título de issue, nome de teste, proposta de
refactor, hipótese de bug — use o termo como o `CONTEXT.md` define. Não derive pros sinônimos que o
glossário lista em `_Avoid_`.

Boa parte do vocabulário deste projeto já está nas docstrings dos models (`users/roles/*/models.py`,
`hub/models.py`, `finance/models.py`) e são fonte de verdade rica. Se um conceito que você precisa
não está no glossário, é sinal: ou você está inventando linguagem que o projeto não usa (reconsidere),
ou existe uma lacuna real (anote pro `/domain-modeling`).

## Sinalize conflito com ADR

Se o que você propõe contradiz uma ADR existente, diga isso explicitamente em vez de atropelar em
silêncio:

> _Contradiz a ADR-0003 (…) — mas vale reabrir porque…_

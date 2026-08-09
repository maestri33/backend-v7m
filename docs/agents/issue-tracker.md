# Issue tracker: GitHub

Issues e specs deste repo vivem como **GitHub Issues** em `maestri33/backend-supletivo`.

> **Não existe `gh` CLI neste ambiente.** As sessões do Claude Code na web rodam sem o `gh` e sem
> acesso direto à API do GitHub — toda operação passa pelas ferramentas MCP `mcp__github__*`.
> Carregue o schema delas com `ToolSearch` antes de chamar (ex.: `select:mcp__github__issue_read`).
> Se você estiver num ambiente local que tenha `gh`, pode usar o equivalente da CLI.

## Convenções

Owner/repo é sempre `maestri33` / `backend-supletivo`.

- **Criar issue**: `mcp__github__issue_write` com `method: "create"`.
- **Ler issue**: `mcp__github__issue_read` com `method: "get"` (corpo) e `method: "get_comments"`
  (comentários). Labels vêm no `get`.
- **Listar issues**: `mcp__github__list_issues` pra varredura simples com filtro de estado/label;
  `mcp__github__search_issues` quando o filtro for por texto ou critério composto.
- **Comentar**: `mcp__github__add_issue_comment`.
- **Aplicar/remover label**: `mcp__github__issue_write` com `method: "update"`, passando o conjunto
  de labels desejado.
- **Fechar**: `mcp__github__issue_write` com `method: "update"`, `state: "closed"` — sempre com
  `state_reason` preenchido.

Paginação: peça lotes de 5–10 itens. Use `minimal_output` quando não precisar do corpo inteiro —
o output completo destas ferramentas estoura o limite de tokens com facilidade.

## Ruído automático na fila

O workflow `deploy.yml` abre issue com label `deploy-failure` sozinho quando o CD falha, e o
`auto-fix.yml` também mexe na fila. Ao varrer issues em busca de spec ou trabalho humano, **filtre
essas fora** — elas são telemetria de CI, não pedido de feature.

## Pull requests como superfície de triagem

**PRs como superfície de pedido: não.** _(Vire pra `sim` se este repo passar a tratar PR externo
como pedido de feature.)_

GitHub compartilha o mesmo espaço de numeração entre issue e PR, então um `#42` solto pode ser
qualquer um dos dois: resolva com `mcp__github__pull_request_read` e caia pra
`mcp__github__issue_read` se não for PR.

## Quando uma skill disser "publique no issue tracker"

Crie uma GitHub issue.

## Quando uma skill disser "busque o ticket relevante"

`mcp__github__issue_read` com `method: "get"`, mais `method: "get_comments"` se a discussão importar.

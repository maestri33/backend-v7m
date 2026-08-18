# AGENTS.md — desenvolvimento de APIs com Django Ninja

## Escopo

Estas instruções se aplicam a APIs Django Ninja neste diretório e em seus descendentes. Preserve as convenções, a arquitetura e os comandos já definidos pelo projeto; este arquivo complementa o repositório, não substitui suas configurações.

Para decisões dependentes de versão ou comportamento do framework, leia [`references/django-ninja-research.md`](references/django-ninja-research.md) e confirme na documentação oficial e na versão instalada. O manifesto de dependências, os testes e o código local são a fonte de verdade do projeto.

## Processo obrigatório

1. Antes de editar, confirme as versões instaladas de Django Ninja, Django e Pydantic; depois localize `NinjaAPI`, routers, schemas, autenticação, serviços, testes, configurações, dependências e comandos de qualidade. Identifique também limites de app, tenant e autorização. A inspeção termina quando o caminho completo da requisição e as convenções afetadas estão claros.
2. Defina o contrato da mudança: método e rota, entradas, respostas por status, autenticação, autorização, efeitos transacionais, paginação e compatibilidade OpenAPI. Em correções, escreva primeiro um teste que reproduza o defeito. O contrato termina quando cada caminho observável possui resultado esperado.
3. Faça a menor alteração coerente com a arquitetura existente. Mantenha endpoints como orquestradores finos; coloque regras de negócio reutilizáveis em serviços e consultas reutilizáveis em selectors/managers, conforme a convenção local.
4. Teste o comportamento modificado, depois execute as verificações proporcionais ao risco. A tarefa termina somente quando os testes relevantes passam, o contrato OpenAPI continua válido e os riscos de segurança, acesso a dados e desempenho foram revisados.

## Organização da API

- Mantenha uma instância principal de `NinjaAPI` como raiz de composição. Divida funcionalidades por app ou domínio com `Router` e registre os routers nessa raiz.
- Aplique prefixos, `tags`, autenticação, throttling e decorators no nível mais alto que preserve o mesmo comportamento. Exceções por endpoint devem ser explícitas e testadas.
- Siga a estrutura existente. Em um projeto novo, prefira uma separação equivalente a `project/api.py`, `app/api/router.py`, `app/api/schemas.py`, `app/services.py`, `app/selectors.py` e `app/tests/test_api.py`.
- Use nomes de recursos consistentes, parâmetros de caminho tipados e uma política única para barras finais. Preserve `operation_id` quando houver clientes gerados.
- Introduza uma nova versão de API para mudanças incompatíveis. Mantenha a versão antiga durante a janela de migração e marque operações em retirada com `deprecated=True`.

## Contratos e schemas

- Tipifique toda entrada e declare `response=` em toda operação. Modele também erros esperados e respostas vazias; use `Status(code, body)` para selecionar o schema correto. A sintaxe de tupla de status está obsoleta.
- Separe schemas de entrada, atualização e saída. O schema de saída é a fronteira de exposição de dados e deve conter somente campos públicos.
- Em `ModelSchema`, liste `Meta.fields` explicitamente. `fields = "__all__"` não é aceitável em contratos públicos porque pode expor campos adicionados ao model, credenciais ou dados internos.
- Para `PATCH`, diferencie campo ausente de valor nulo com `PatchDict[...]` ou `fields_optional` e `model_dump(exclude_unset=True)`. Atualize apenas campos permitidos pelo contrato.
- Use `Query[FilterSchema]` para filtros compostos e `Annotated[..., Field(...)]`/validadores Pydantic para limites, formatos e invariantes locais. Preserve filtros obrigatórios de tenant, visibilidade e estado ao aplicar filtros fornecidos pelo cliente.
- Em uploads, declare `File` e `Form` corretamente e valide tamanho, tipo real, nome, destino e permissão antes de persistir. Trate o `Content-Type` informado pelo cliente como dado não confiável.
- Para multipart em PUT/PATCH, verifique se a versão exige `ninja.compatibility.files.fix_request_files_middleware`; quando necessário, configure-o e cubra o fluxo com teste de integração Django.
- Não aceite do cliente campos de ownership, tenant, papel, privilégio ou estado protegido; derive-os da identidade autenticada e das regras de negócio.

## Autenticação, autorização e segurança

- Proteja por padrão no nível de `NinjaAPI` ou `Router`. Use `auth=None` somente em uma rota deliberadamente pública, com justificativa legível e teste de acesso anônimo.
- Trate autenticação e autorização como etapas distintas. Depois de autenticar, restrinja o QuerySet ao tenant/usuário e valide permissão de ação e de objeto antes de ler, alterar ou revelar sua existência.
- Trate `auth=[auth_a, auth_b]` como alternativas em ordem (OR): o primeiro autenticador válido vence. Implemente requisitos adicionais como autorização separada e teste a ordem quando misturar cookie/session com headers.
- Prefira tokens no cabeçalho `Authorization` ou chaves em cabeçalhos. Segredos em query strings podem aparecer em histórico, logs e métricas.
- Para sessão ou autenticação baseada em cookie, mantenha a proteção CSRF do Django e teste o fluxo com middleware real. Configure CORS de forma explícita; CORS não substitui CSRF.
- Retorne `401` para credenciais ausentes/inválidas e `403` para identidade válida sem permissão. Não revele se um objeto de outro tenant existe quando isso quebrar o modelo de ameaça.
- Nunca registre senhas, tokens, cookies, chaves, corpos sensíveis ou headers de autorização. Mensagens de erro públicas não devem incluir tracebacks, SQL nem detalhes internos; produção usa `DEBUG=False`.
- Use throttling para uso justo e proteção operacional. Ele não substitui controles contra força bruta, limites no proxy/gateway ou mitigação de negação de serviço.
- Proteja ou desative a UI de documentação em produção conforme a política do projeto; decida separadamente se `/openapi.json` deve permanecer público.

## ORM, transações e desempenho

- Comece toda consulta com o escopo de acesso correto. Aplique filtros do cliente somente depois das restrições invariantes de tenant, ownership e visibilidade.
- Paginação é obrigatória para coleções potencialmente grandes. Defina limite máximo e ordenação determinística; prefira cursor para conjuntos grandes e mutáveis quando o produto exigir navegação estável.
- Compare o schema de saída com o plano de consulta. Use `select_related` para relações singulares e `prefetch_related` para coleções; cubra endpoints de lista críticos com teste de contagem de queries.
- Evite materializar coleções sem limite e evite trabalho de banco em resolvers de schema, pois isso esconde N+1. Faça anotações, joins ou prefetch antes da serialização.
- Envolva alterações relacionadas em `transaction.atomic()`. Para concorrência, combine constraints do banco com a estratégia adequada, como operações condicionais, `select_for_update` ou idempotency keys.
- Validações de aplicação melhoram a mensagem; constraints e índices no banco preservam integridade. Trate `IntegrityError` esperado e converta-o para um erro estável do contrato.

## Sync e async

- Use endpoint síncrono para fluxos predominantemente ORM quando não existir ganho claro de concorrência. Use `async def` somente quando a cadeia de I/O for assíncrona de ponta a ponta.
- Em código async, use clientes async, `await`, métodos assíncronos do ORM (`aget`, `acreate`, `aupdate`, `async for`, conforme a versão) e servidor ASGI. Chamadas bloqueantes em endpoint async precisam ser substituídas ou isoladas deliberadamente.
- Não devolva um QuerySet lazy criado em contexto async para ser avaliado depois. Avalie-o com a interface async ou, como fallback explícito, dentro de `sync_to_async`.
- Mantenha blocos transacionais que dependem de APIs síncronas do Django em uma função síncrona única e adapte essa função na fronteira async. Teste endpoints async com `TestAsyncClient`.

## Erros e respostas

- Use um schema de erro estável, por exemplo com `code`, `message` e detalhes seguros. Declare no decorator todos os status de domínio que o endpoint pode retornar.
- Use `HttpError` para falhas HTTP simples e handlers de exceção para erros de domínio repetidos. Centralize o mapeamento sem esconder falhas inesperadas do monitoramento.
- Use `get_object_or_404` ou equivalente somente depois de aplicar o escopo de acesso. Normalize validação, conflito, não encontrado e indisponibilidade sem vazar exceções internas.
- Respostas `204` devem declarar `response={204: None}` e não conter corpo. Preserve a semântica e a idempotência esperadas de GET, PUT, PATCH e DELETE.

## OpenAPI e compatibilidade

- Trate o OpenAPI como parte do produto. Mantenha schemas, status, autenticação, tags, summary/description e `operation_id` coerentes com a implementação.
- Gere ou inspecione o schema após alterar uma rota. Quando `ninja` estiver em `INSTALLED_APPS`, use `python manage.py export_openapi_schema --api <caminho.da.api>` conforme a configuração local.
- Mudanças em nomes/tipos de campos, status, autenticação, paginação, nulabilidade ou `operation_id` são mudanças de contrato. Atualize testes, clientes, documentação e versão quando necessário.

## Testes mínimos por endpoint alterado

- Cubra o caminho de sucesso e valide status e corpo serializado.
- Cubra entrada inválida (`422`) e cada erro de domínio declarado.
- Cubra anônimo (`401`), autenticado sem permissão (`403`) e acesso a objeto/tenant alheio.
- Cubra ausência (`404`), conflito ou idempotência quando aplicável.
- Cubra filtros, limites, ordenação, paginação e ausência de campos sensíveis em respostas.
- Use `ninja.testing.TestClient` para testes rápidos do router. Use o cliente Django para middleware, resolução de URL, sessão, cookie e CSRF. Use `TestAsyncClient` para operações async.
- Para correções de segurança e isolamento, mantenha um teste de regressão que falhe sem a correção.

## Verificação e conclusão

Descubra e use primeiro os comandos do próprio projeto (`pyproject.toml`, `Makefile`, `tox.ini`, scripts de CI). Na ausência de comandos equivalentes, execute o subconjunto aplicável:

```text
python manage.py check
python manage.py makemigrations --check --dry-run
pytest <testes afetados>
pytest
ruff format --check .
ruff check .
mypy .
python manage.py export_openapi_schema --api <caminho.da.api>
```

Antes de concluir, confirme todos os itens:

- testes relevantes, lint e type-check passam;
- migrations são intencionais e estão incluídas quando necessárias;
- schemas não expõem campos sensíveis;
- autenticação, autorização por objeto e isolamento de tenant estão cobertos;
- listas têm paginação, ordenação e plano de queries adequados;
- código async não executa I/O bloqueante nem avalia QuerySet lazy fora do contexto correto;
- OpenAPI representa respostas e segurança reais;
- qualquer quebra de contrato foi versionada e documentada.

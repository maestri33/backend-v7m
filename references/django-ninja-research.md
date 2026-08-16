# Pesquisa primária: práticas recomendadas para APIs com Django Ninja

Data da pesquisa: 2026-08-16  
Escopo: repositório oficial, documentação oficial, código-fonte, testes e releases do Django Ninja.  
Snapshot de código inspecionado: `master` no commit [`134869b74b6cba214284faa9f13d54b7247362c0`](https://github.com/vitalik/django-ninja/commit/134869b74b6cba214284faa9f13d54b7247362c0).

## Estado da versão analisada

- O snapshot declara Django Ninja `1.6.2`; antes de aplicar estas regras, confirme a versão instalada no projeto, pois comportamento e deprecações podem variar. [Fonte: `ninja/__init__.py` no commit analisado](https://github.com/vitalik/django-ninja/blob/134869b74b6cba214284faa9f13d54b7247362c0/ninja/__init__.py)
- O pacote analisado declara Python `>=3.7`, Django `>=3.1,<6.2` e Pydantic `>=2,<3`; use uma matriz de versões compatível e fixe dependências de produção. [Fonte: `pyproject.toml`](https://github.com/vitalik/django-ninja/blob/134869b74b6cba214284faa9f13d54b7247362c0/pyproject.toml)
- A série 1.x migrou para Pydantic 2; `Schema.Meta` antigo não é a forma de configuração geral, enquanto `ModelSchema.Meta` é a API específica para gerar schemas de modelos. [Fonte: novidades da v1](https://django-ninja.dev/whatsnew_v1/)
- Releases recentes adicionaram, entre outros itens, routers reutilizáveis/idempotentes, paginação por cursor, `Status(...)` e serialização sem revalidação redundante quando a resposta já é uma instância do schema esperado; consulte o changelog antes de usar um recurso recente. [Fonte: releases oficiais](https://github.com/vitalik/django-ninja/releases)

## Conclusões executivas

1. Trate schemas de entrada e saída como contratos separados: valide toda entrada na borda e declare `response=` em toda operação relevante para validar, documentar e limitar os dados expostos. [Fonte: request body](https://django-ninja.dev/guides/input/body/) e [response schema](https://django-ninja.dev/guides/response/)
2. Organize a API por domínio/app com `Router`, mantendo uma única composição explícita das rotas no `NinjaAPI`; aplique tags, autenticação e throttling no nível mais abrangente que preserve clareza. [Fonte: routers](https://django-ninja.dev/guides/routers/)
3. Adote autenticação global por padrão e abra rotas públicas explicitamente; autenticação só identifica o principal, portanto cada operação ainda deve impor autorização funcional e por objeto. [Fonte: autenticação global, router e `request.auth`](https://django-ninja.dev/guides/authentication/) e [exceções 401/403 no código](https://github.com/vitalik/django-ninja/blob/134869b74b6cba214284faa9f13d54b7247362c0/ninja/errors.py)
4. Use `async def` somente quando toda a cadeia de I/O for assíncrona ou corretamente adaptada; nunca deixe um `QuerySet` lazy escapar de um contexto seguro. [Fonte: suporte async e ORM](https://django-ninja.dev/guides/async-support/)
5. Paginação, eager loading, filtros permitidos e schemas mínimos devem ser decisões explícitas para evitar respostas ilimitadas, N+1 e exposição acidental. [Fonte: paginação](https://django-ninja.dev/guides/response/pagination/), [respostas ORM](https://django-ninja.dev/guides/response/) e [FilterSchema](https://django-ninja.dev/guides/input/filtering/)
6. Combine testes rápidos de operação com testes de integração Django e valide o OpenAPI exportado em CI; o cliente Ninja deliberadamente ignora middleware e resolução de URL. [Fonte: testes](https://django-ninja.dev/guides/testing/) e [comando de exportação OpenAPI](https://github.com/vitalik/django-ninja/blob/134869b74b6cba214284faa9f13d54b7247362c0/ninja/management/commands/export_openapi_schema.py)

## 1. Organização de `NinjaAPI` e routers

### Estrutura recomendada

```text
project/
  api.py                 # cria NinjaAPI e registra routers
orders/
  api.py                 # Router e handlers HTTP do domínio
  schemas.py             # contratos de entrada/saída
  services.py            # regras de negócio/transações, quando necessárias
  models.py
```

- A documentação recomenda um `api.py` por app Django, cada um expondo um `Router`, e um `api.py` no projeto que cria o `NinjaAPI` e agrega os routers. [Fonte: routers](https://django-ninja.dev/guides/routers/)
- A separação adicional de schemas e serviços é uma recomendação arquitetural derivada: mantenha handlers finos, parsing/serialização nos schemas e regras de negócio reutilizáveis fora da camada HTTP; a motivação oficial para routers é impedir que aplicações reais concentrem toda a lógica em um arquivo. [Fonte: routers](https://django-ninja.dev/guides/routers/)
- Use prefixos estáveis por recurso/domínio e tags no router; tags são herdadas pelas operações e organizam o OpenAPI/Swagger. [Fonte: router tags](https://django-ninja.dev/guides/routers/) e [parâmetros de operação](https://django-ninja.dev/reference/operations-parameters/)
- Aplique `auth` e `throttle` no `NinjaAPI` ou router quando forem políticas comuns, deixando overrides por operação apenas para exceções explícitas. [Fonte: router auth](https://django-ninja.dev/guides/routers/) e [throttling por níveis](https://django-ninja.dev/guides/throttling/)
- Routers podem ser aninhados; isso é útil para refletir hierarquias de URL, mas evite profundidade desnecessária que esconda o caminho final e a política efetiva. [Fonte: nested routers](https://django-ninja.dev/guides/routers/)
- Na linha 1.6.x, routers podem ser reutilizados em mais de uma montagem com isolamento de decorators, auth, tags e throttle; se o projeto suporta versões anteriores, não presuma essa propriedade sem checar a versão. [Fonte: release 1.6.0 beta](https://github.com/vitalik/django-ninja/releases)

## 2. Schemas, Pydantic e contrato de dados

- Defina schemas distintos para entrada e saída, por exemplo `UserCreateIn` e `UserOut`; o schema de saída limita os campos serializados e é a barreira que impede retornar senha ou campos internos por acidente. [Fonte: response schema](https://django-ninja.dev/guides/response/)
- Declare `response=` para sucesso e para erros esperados; o Django Ninja usa esses schemas para conversão, validação, OpenAPI e documentação automática. [Fonte: response schema e múltiplas respostas](https://django-ninja.dev/guides/response/)
- Prefira retornar `Status(codigo, corpo)` ao selecionar um schema por status; a sintaxe de tupla `(status, body)` está depreciada no snapshot atual. [Fonte: múltiplos response schemas](https://django-ninja.dev/guides/response/)
- Em `ModelSchema`, liste `fields` explicitamente. A própria documentação desaconselha `fields = "__all__"` porque ele pode expor dados como hashes de senha. [Fonte: ModelSchema](https://django-ninja.dev/guides/response/django-pydantic/)
- Para PATCH parcial, use `fields_optional` com `model_dump(exclude_unset=True)`/equivalente da versão instalada, ou `PatchDict[Schema]`, para distinguir campo ausente de campo enviado com valor nulo. [Fonte: campos opcionais e `PatchDict`](https://django-ninja.dev/guides/response/django-pydantic/)
- Use tipos Python/Pydantic precisos em path, query, body, form, header e cookie; parâmetros sem annotation são tratados como `str`, enquanto parâmetros tipados são convertidos, validados e documentados. [Fonte: query params](https://django-ninja.dev/guides/input/query-params/) e [path params](https://django-ninja.dev/guides/input/path-params/)
- Agrupe conjuntos de parâmetros em `Schema` com a origem explícita (`Query[...]`, `Path[...]`, `Form[...]` etc.) quando isso melhorar validação e legibilidade. [Fonte: schema em query](https://django-ninja.dev/guides/input/query-params/) e [schema em path](https://django-ninja.dev/guides/input/path-params/)
- Use `model_config` de Pydantic 2 para aliases e outras opções; para produzir aliases na saída, a documentação exige combinar `populate_by_name=True` no schema com `by_alias=True` na operação. [Fonte: configuração Pydantic](https://django-ninja.dev/guides/response/config-pydantic/)
- Use aliases e resolvers com parcimônia: eles suportam objetos relacionados, callables e contexto da request, mas devem permanecer determinísticos e não iniciar consultas ocultas por item. O suporte oficial está documentado; evitar I/O oculto é uma recomendação de desempenho derivada. [Fonte: aliases, resolvers e contexto](https://django-ninja.dev/guides/response/)
- `FilterSchema` transforma somente os campos declarados em expressões `Q`; prefira essa lista permitida a aceitar nomes de lookup arbitrários do cliente. Para código novo, use `FilterLookup` com `Annotated`, pois a sintaxe `Field(q=...)` está depreciada. [Fonte: filtering](https://django-ninja.dev/guides/input/filtering/)

## 3. Validação

- Faça validação estrutural e de formato nos schemas de entrada; o Ninja lê JSON, converte tipos, valida e retorna a localização do erro, além de incluir o contrato no JSON Schema/OpenAPI. [Fonte: request body](https://django-ninja.dev/guides/input/body/)
- Mantenha invariantes que dependem de banco, permissões ou múltiplas entidades na camada de serviço/domínio depois da validação estrutural. Esta é uma recomendação de separação de responsabilidades derivada do fato de que schemas validam o payload, não a autorização nem o estado transacional. [Fonte: comportamento dos schemas](https://django-ninja.dev/guides/input/body/) e [autenticação](https://django-ninja.dev/guides/authentication/)
- Requisições inválidas levantam `ninja.errors.ValidationError` e recebem `422` com `{"detail": [...]}` por padrão; se customizar, preserve um envelope estável e não confunda essa classe com `pydantic.ValidationError`. [Fonte: validation errors](https://django-ninja.dev/guides/errors/)
- Para PATCH, atualize exclusivamente campos presentes no payload, nunca todos os atributos opcionais indiscriminadamente. [Fonte: `exclude_unset` e `PatchDict`](https://django-ninja.dev/guides/response/django-pydantic/)

## 4. Autenticação, autorização e segurança

- Configure autenticação global em `NinjaAPI(auth=...)` para uma API privada por padrão e use `auth=None` apenas nas operações públicas deliberadas, como login/health check. [Fonte: autenticação global](https://django-ninja.dev/guides/authentication/)
- Atenção à herança: auth configurada no router substitui a auth do `NinjaAPI`; uma operação também pode sobrescrever a política. Revise a política efetiva ao montar cada router. [Fonte: router authentication](https://django-ninja.dev/guides/authentication/)
- O valor truthy retornado pelo autenticador é colocado em `request.auth`; use-o para checar papel, tenant e propriedade do recurso. Falha de credencial deve ser `401 AuthenticationError`; credencial válida sem permissão deve ser `403 AuthorizationError`. [Fonte: `request.auth`](https://django-ninja.dev/guides/authentication/) e [implementação das exceções](https://github.com/vitalik/django-ninja/blob/134869b74b6cba214284faa9f13d54b7247362c0/ninja/errors.py)
- Uma lista `auth=[auth_a, auth_b]` representa alternativas em ordem: o primeiro autenticador que retornar valor truthy concede autenticação. Não a use para expressar “autenticação A **e** permissão B”; faça autorização separadamente. [Fonte: multiple authenticators](https://django-ninja.dev/guides/authentication/#multiple-authenticators) e [implementação v1.6.2](https://github.com/vitalik/django-ninja/blob/v1.6.2/ninja/operation.py#L280-L294)
- O Ninja fornece autenticação, não uma política completa de autorização por objeto; portanto filtre querysets pelo principal/tenant antes de buscar ou alterar o recurso e nunca confie apenas no identificador da URL. Esta conclusão é derivada da API oficial, na qual o autenticador apenas decide acesso inicial e popula `request.auth`. [Fonte: autenticação customizada](https://django-ninja.dev/guides/authentication/)
- Prefira credenciais em `Authorization: Bearer` ou header dedicado. API keys em query são suportadas, mas usar a URL para segredo amplia sua exposição operacional; esta preferência é uma recomendação de segurança, enquanto os mecanismos disponíveis estão descritos na documentação. [Fonte: opções de API key e bearer](https://django-ninja.dev/guides/authentication/)
- Com autenticação por cookie ou sessão, mantenha CSRF: as operações Ninja são isentas no middleware Django, e a autenticação baseada em `APIKeyCookie` executa a checagem CSRF própria; o comportamento é automático na v1+. [Fonte: CSRF](https://django-ninja.dev/reference/csrf/) e [implementação `APIKeyCookie`](https://github.com/vitalik/django-ninja/blob/134869b74b6cba214284faa9f13d54b7247362c0/ninja/security/apikey.py)
- Não desabilite CSRF em endpoints autenticados por cookie. A exceção documentada para emitir o cookie CSRF requer ordem específica dos decorators, `auth=None` e retorno de `HttpResponse`; limite essa rota exatamente a esse propósito. [Fonte: `ensure_csrf_cookie`](https://django-ninja.dev/reference/csrf/)
- CORS controla quais origens podem ler/fazer certas requisições no navegador, mas não substitui proteção CSRF; configure ambos conforme a topologia do frontend. [Fonte: nota oficial sobre CORS](https://django-ninja.dev/reference/csrf/#a-word-about-cors)
- Proteja a UI de documentação com `docs_decorator`, oculte-a com `docs_url=None` ou desative também o schema com `openapi_url=None`, conforme a política do ambiente. [Fonte: API docs](https://django-ninja.dev/guides/api-docs/)
- Use throttling do Ninja para política de uso/fairness, não como defesa contra brute force ou DoS: a documentação alerta que ele usa cache Django com operações não atômicas e IPs falsificáveis. Combine-o com controles na borda/proxy. [Fonte: throttling](https://django-ninja.dev/guides/throttling/)
- Se `AuthRateThrottle` usa um objeto customizado em `request.auth`, implemente `__str__` com valor único e não sensível; a chave interna deriva de `sha256(str(request.auth))`. [Fonte: throttling autenticado](https://django-ninja.dev/guides/throttling/)

## 5. Tratamento de erros

- Centralize exceções de domínio com `@api.exception_handler`, retornando um envelope consistente e status apropriado; handlers recebem `request` e a exceção e devem retornar uma resposta HTTP. [Fonte: handling errors](https://django-ninja.dev/guides/errors/)
- Use `HttpError` para erros HTTP esperados simples e `Http404`/`get_object_or_404` para ausência; para erros de domínio reutilizados, prefira exceções próprias mapeadas em handlers. [Fonte: handlers padrão e `HttpError`](https://django-ninja.dev/guides/errors/)
- Modele no `response={...}` os códigos esperados, incluindo respostas vazias como `{204: None}`; isso mantém runtime e OpenAPI alinhados. [Fonte: múltiplos schemas e respostas vazias](https://django-ninja.dev/guides/response/)
- Em produção, mantenha `DEBUG=False`: com debug ativo o handler padrão devolve traceback em texto; com debug desativado, a exceção não tratada segue o mecanismo padrão do Django. [Fonte: comportamento de exceções não tratadas](https://django-ninja.dev/guides/errors/)
- Não devolva detalhes internos, stack traces, SQL, tokens ou dados pessoais no corpo de erro. Esta é a consequência operacional do comportamento de debug documentado e do uso de handlers customizados. [Fonte: handling errors](https://django-ninja.dev/guides/errors/)

## 6. Sync, async e ORM

- Use handlers síncronos como padrão quando dependências e fluxo forem síncronos; escolha `async def` para concorrência em I/O e use bibliotecas async em todas as chamadas bloqueantes. O Ninja permite misturar operações sync e async. [Fonte: async support](https://django-ninja.dev/guides/async-support/)
- Execute a aplicação async sob ASGI; a documentação exemplifica Uvicorn/Daphne e alerta para não usar `--reload` em produção. [Fonte: execução async](https://django-ninja.dev/guides/async-support/)
- Em Django 4.1+, prefira métodos async do ORM (`aget`, `acreate`, `adelete` etc.) e `async for` para querysets. Quando não houver API async, adapte a operação síncrona com `sync_to_async`. [Fonte: ORM async](https://django-ninja.dev/guides/async-support/)
- Querysets são lazy: `await sync_to_async(Model.objects.all)()` ainda devolve um queryset não avaliado e pode falhar ao iterar; avalie dentro da fronteira sync, por exemplo `await sync_to_async(list)(queryset)`. [Fonte: gotcha de queryset lazy](https://django-ninja.dev/guides/async-support/)
- Em operações sync, o Ninja pode avaliar automaticamente um queryset declarado como `response=list[Schema]`; em operações async isso não é seguro sem avaliação async/correta. [Fonte: returning querysets](https://django-ninja.dev/guides/response/)
- Para schemas aninhados, carregue relações de modo explícito com `select_related`/`prefetch_related` antes da serialização. A documentação demonstra `select_related("owner")`; a recomendação evita consultas implícitas em cascata. [Fonte: nested response objects](https://django-ninja.dev/guides/response/)
- Autenticadores async são suportados; no snapshot atual, uma operação async aguarda autenticadores async e adapta callables sync com `sync_to_async`. Ainda assim, um autenticador que faz I/O deve usar uma implementação nativamente async quando disponível. [Fonte: async authentication](https://django-ninja.dev/guides/authentication/) e [implementação da operação async](https://github.com/vitalik/django-ninja/blob/134869b74b6cba214284faa9f13d54b7247362c0/ninja/operation.py#L543-L560)
- Decorators próprios aplicados a operações/routers que misturam sync e async devem detectar os dois tipos, preservar metadata com `functools.wraps` e ter testes para ambos os caminhos. [Fonte: decorators e async support](https://django-ninja.dev/guides/decorators/#async-support)

## 7. Paginação e consultas de coleção

- Paginação deve ser obrigatória para coleções potencialmente grandes; `@paginate` aplica `LimitOffsetPagination` por padrão, e `RouterPaginated` pode aplicar paginação a todas as operações do router com `response=list[Schema]`. [Fonte: pagination](https://django-ninja.dev/guides/response/pagination/)
- Defina limites máximos de página/offset e valide ordenação. A paginação por cursor é indicada para conjuntos que mudam com frequência e recomenda um primeiro campo de ordenação único, com `-pk` como default atual. [Fonte: cursor pagination](https://django-ninja.dev/guides/response/pagination/)
- Paginação padrão suporta async; para paginação customizada async, derive de `AsyncPaginationBase` e implemente `apaginate_queryset`. [Fonte: async pagination](https://django-ninja.dev/guides/response/pagination/)
- Filtros devem ser declarados em `FilterSchema` e compostos com as restrições obrigatórias do servidor, como tenant/ativo; a documentação mostra combinar a expressão do cliente com filtros internos antes da consulta. [Fonte: filtering](https://django-ninja.dev/guides/input/filtering/)

### Discrepância encontrada na configuração de paginação

- A página de paginação cita `PAGINATION_MAX_PER_PAGE_SIZE` e `NINJA_PAGINATION_MAX_PER_PAGE_SIZE` em trechos distintos, mas o código do snapshot lê a setting Django `NINJA_MAX_PER_PAGE_SIZE` para o campo interno `PAGINATION_MAX_PER_PAGE_SIZE`. Ao configurar esse limite, confirme o nome no código da versão instalada e cubra-o com teste; para `1.6.2` no commit analisado, o alias efetivo é `NINJA_MAX_PER_PAGE_SIZE`. [Fonte: documentação de paginação](https://django-ninja.dev/guides/response/pagination/) e [fonte `ninja/conf.py`](https://github.com/vitalik/django-ninja/blob/134869b74b6cba214284faa9f13d54b7247362c0/ninja/conf.py#L8-L21)

## 8. Uploads

- Declare upload como `File[UploadedFile]`; para múltiplos arquivos, use `File[list[UploadedFile]]`; para combinar arquivos e campos, use `multipart/form-data` com `Form[Schema]` ou envie o schema como JSON em um campo multipart. [Fonte: file uploads](https://django-ninja.dev/guides/input/file-params/)
- Valide tamanho, tipo permitido e regras de negócio antes de persistir. `UploadedFile` expõe `size`, `content_type`, `multiple_chunks()` e `chunks()`; prefira processamento em chunks para arquivos grandes em vez de `read()` integral. [Fonte: atributos de upload no Django Ninja](https://django-ninja.dev/guides/input/file-params/) e [UploadedFile do Django](https://docs.djangoproject.com/en/stable/ref/files/uploads/)
- Para uploads multipart via PUT/PATCH/DELETE, Django não preenche `request.FILES` por padrão; instale `ninja.compatibility.files.fix_request_files_middleware` quando esses métodos fizerem parte do contrato. [Fonte: compatibilidade de request files](https://django-ninja.dev/guides/input/file-params/)
- Teste uploads opcionais, vazios, múltiplos, acima do limite e com conteúdo inválido; o tipo opcional é expresso com default `None`. O suporte de API está documentado; a matriz de casos é uma recomendação de robustez derivada. [Fonte: optional file input](https://django-ninja.dev/guides/input/file-params/)

## 9. OpenAPI, documentação e versionamento

- Preencha `title`, `description`, `version`, tags e summaries; defina `operation_id` estável e único quando clientes gerados dependem dele. [Fonte: parâmetros de operação](https://django-ninja.dev/reference/operations-parameters/) e [API docs](https://django-ninja.dev/guides/api-docs/)
- Docstrings podem alimentar descrições longas e `deprecated=True` marca uma operação como depreciada sem removê-la imediatamente do contrato. [Fonte: descriptions e deprecated](https://django-ninja.dev/reference/operations-parameters/)
- Use `openapi_extra` somente para extensões que o schema tipado não expressa; prefira `response=` e parâmetros tipados como fonte principal do contrato para evitar divergência manual. [Fonte: `openapi_extra`](https://django-ninja.dev/reference/operations-parameters/)
- Exporte o contrato com `python manage.py export_openapi_schema --api pacote.api.api --output openapi.json --indent 2 --sorted`; o comando suporta saída em arquivo/stdout e requer `ninja` em `INSTALLED_APPS`. [Fonte: management commands](https://django-ninja.dev/reference/management-commands/) e [implementação do exportador](https://github.com/vitalik/django-ninja/blob/134869b74b6cba214284faa9f13d54b7247362c0/ninja/management/commands/export_openapi_schema.py)
- Versione/diff o OpenAPI na CI para detectar quebras involuntárias. Essa é uma recomendação de contrato derivada do exportador oficial e da geração automática a partir dos schemas. [Fonte: exportador](https://github.com/vitalik/django-ninja/blob/134869b74b6cba214284faa9f13d54b7247362c0/ninja/management/commands/export_openapi_schema.py) e [request body/OpenAPI](https://django-ninja.dev/guides/input/body/)
- Para versões simultâneas, crie instâncias `NinjaAPI` distintas com `version` e caminhos próprios; instâncias múltiplas precisam de `version` ou `urls_namespace` diferentes e geram páginas OpenAPI separadas. [Fonte: versioning](https://django-ninja.dev/guides/versioning/)
- Preserve v1/v2 como contratos independentes durante a migração e marque endpoints antigos como `deprecated=True` antes da remoção; a separação é suportada oficialmente, e o período de compatibilidade é uma decisão de produto. [Fonte: versioning](https://django-ninja.dev/guides/versioning/) e [deprecated operation](https://django-ninja.dev/reference/operations-parameters/)

## 10. Testes

- Use `ninja.testing.TestClient` para testes rápidos de routers/APIs sync e `TestAsyncClient` para operações async; ambos permitem injetar headers, cookies, usuário e atributos da request. [Fonte: testing](https://django-ninja.dev/guides/testing/)
- Como o `TestClient` Ninja não passa por middleware nem URL resolver, complemente-o com o cliente de teste Django para autenticação de sessão, CSRF, middleware de upload, URLs e comportamento de integração. [Fonte: escopo do TestClient](https://django-ninja.dev/guides/testing/) e [CSRF](https://django-ninja.dev/reference/csrf/)
- Cubra ao menos: sucesso; validação 422; não autenticado 401; autenticado sem permissão 403; recurso ausente 404; conflitos/regras de domínio; paginação e limites; filtros; upload; e schemas de erro. Os códigos e mecanismos são oficiais; a matriz é uma recomendação de cobertura. [Fonte: errors](https://django-ninja.dev/guides/errors/), [pagination](https://django-ninja.dev/guides/response/pagination/) e [file uploads](https://django-ninja.dev/guides/input/file-params/)
- Faça assert do corpo serializado além do status para detectar exposição acidental de campos e mudanças no contrato. O `response` schema limita a saída, e `response.data`/`response.json()` estão disponíveis nos testes. [Fonte: response schema](https://django-ninja.dev/guides/response/) e [testing](https://django-ninja.dev/guides/testing/)
- Em CI, execute testes e cobertura e gere/valide o OpenAPI; o próprio projeto usa pytest, pytest-django, pytest-asyncio e pytest-cov. [Fonte: dependências e coverage](https://github.com/vitalik/django-ninja/blob/134869b74b6cba214284faa9f13d54b7247362c0/pyproject.toml) e [workflow oficial](https://github.com/vitalik/django-ninja/blob/134869b74b6cba214284faa9f13d54b7247362c0/.github/workflows/test_full.yml)

## 11. Comandos de qualidade recomendados

O projeto upstream fornece estes gates; adapte os caminhos ao projeto consumidor:

```bash
pytest .
pytest --cov=<pacote> --cov-report=term-missing
ruff format --check .
ruff check .
mypy <pacote>
python manage.py export_openapi_schema --api pacote.api.api --output openapi.json --indent 2 --sorted
```

- Upstream define `make test`, `make test-cov`, `make lint` e `make fmt`; `make lint` combina `ruff format --check`, `ruff check` e `mypy`. [Fonte: Makefile oficial](https://github.com/vitalik/django-ninja/blob/134869b74b6cba214284faa9f13d54b7247362c0/Makefile)
- Upstream recomenda `pre-commit install` e usa hooks de Ruff, mypy e validação YAML. [Fonte: CONTRIBUTING](https://github.com/vitalik/django-ninja/blob/134869b74b6cba214284faa9f13d54b7247362c0/CONTRIBUTING.md) e [configuração pre-commit](https://github.com/vitalik/django-ninja/blob/134869b74b6cba214284faa9f13d54b7247362c0/.pre-commit-config.yaml)
- O upstream configura cobertura de branch e limiar de 100% para a própria biblioteca; projetos consumidores devem definir limiar explícito apropriado ao seu risco em vez de herdar esse número sem análise. [Fonte: configuração coverage](https://github.com/vitalik/django-ninja/blob/134869b74b6cba214284faa9f13d54b7247362c0/pyproject.toml#L106-L112)

## Checklist condensado para um futuro `AGENTS.md`

- [ ] Confirmar versões de Django Ninja, Django, Pydantic e Python antes de editar APIs. [Fonte: compatibilidade do pacote](https://github.com/vitalik/django-ninja/blob/134869b74b6cba214284faa9f13d54b7247362c0/pyproject.toml)
- [ ] Localizar o `NinjaAPI`, routers, schemas, auth, handlers de exceção e settings efetivos antes de implementar. [Fonte: routers](https://django-ninja.dev/guides/routers/) e [autenticação](https://django-ninja.dev/guides/authentication/)
- [ ] Definir schemas separados de entrada/saída e `response=` por status; nunca usar `ModelSchema.fields="__all__"`. [Fonte: response schema](https://django-ninja.dev/guides/response/) e [ModelSchema](https://django-ninja.dev/guides/response/django-pydantic/)
- [ ] Aplicar autenticação e autorização por objeto/tenant; distinguir 401 de 403. [Fonte: auth](https://django-ninja.dev/guides/authentication/) e [exceções](https://github.com/vitalik/django-ninja/blob/134869b74b6cba214284faa9f13d54b7247362c0/ninja/errors.py)
- [ ] Preservar CSRF em autenticação baseada em cookie e proteger/ocultar docs conforme o ambiente. [Fonte: CSRF](https://django-ninja.dev/reference/csrf/) e [API docs](https://django-ninja.dev/guides/api-docs/)
- [ ] Evitar I/O bloqueante em async e avaliar querysets no contexto correto. [Fonte: async ORM](https://django-ninja.dev/guides/async-support/)
- [ ] Otimizar relações, filtrar por campos permitidos e paginar coleções com limites. [Fonte: responses](https://django-ninja.dev/guides/response/), [filtering](https://django-ninja.dev/guides/input/filtering/) e [pagination](https://django-ninja.dev/guides/response/pagination/)
- [ ] Validar e processar uploads em chunks; instalar middleware para multipart PUT/PATCH se necessário. [Fonte: uploads](https://django-ninja.dev/guides/input/file-params/)
- [ ] Rodar Ruff, mypy, testes sync/async, integração Django, cobertura e export/diff OpenAPI antes de concluir. [Fonte: CONTRIBUTING](https://github.com/vitalik/django-ninja/blob/134869b74b6cba214284faa9f13d54b7247362c0/CONTRIBUTING.md), [testing](https://django-ninja.dev/guides/testing/) e [exportador OpenAPI](https://github.com/vitalik/django-ninja/blob/134869b74b6cba214284faa9f13d54b7247362c0/ninja/management/commands/export_openapi_schema.py)

## Fontes primárias principais

- [Repositório oficial](https://github.com/vitalik/django-ninja)
- [Documentação oficial](https://django-ninja.dev/)
- [Releases/changelog oficial](https://github.com/vitalik/django-ninja/releases)
- [Código no commit analisado](https://github.com/vitalik/django-ninja/tree/134869b74b6cba214284faa9f13d54b7247362c0)

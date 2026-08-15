# backend-supletivo — Project CLAUDE.md

> Guia operacional para agentes que trabalham neste repositório. Foi derivado do
> código, da configuração e do CI atuais; não é um template genérico de Django.

## Visão geral

O `backend-supletivo` é o monólito Django que concentra identidade, funis de
captação e matrícula, operação do aluno, polos, comissões, pagamentos,
notificações e integrações externas da plataforma V7M/Supletivo.

**Stack confirmada:** Python 3.12+, Django 5.2, Django Ninja, django-ninja-jwt,
Django-Q2, SQLite em desenvolvimento, PostgreSQL via `DATABASE_URL` em produção,
pytest/pytest-django, uv e ruff.

**Arquitetura:** API JSON in-process, organizada por público em grupos Django
Ninja. As rotas chamam serviços de domínio; tarefas demoradas são enviadas ao
Django-Q2 usando o próprio banco como broker. Não há DRF, Celery ou Redis neste
repositório.

## Fontes de verdade

Ao encontrar divergência entre documentos antigos e o código, use esta ordem:

1. Models, services e migrations atuais.
2. Rotas e schemas em `api/`.
3. `core/settings.py`, `core/environment.py` e `core/urls.py`.
4. Testes em `tests/` e o workflow `.github/workflows/ci.yml`.
5. `docs/` e `wiki/` como contexto de fluxo, nunca como prova isolada.

O `README.md` ainda contém alguns estados históricos de implementação. Confirme
qualquer afirmação de “falta implementar” diretamente no código antes de agir.

Este guia foi produzido sobre o working tree da branch
`agent/hotfix-document-classifier`. Partes observadas podem ainda estar sem
commit; antes de mudar código, confira branch, `HEAD` e `git status` e não trate
mudança local como comportamento já publicado.

## Regras críticas

### Limites arquiteturais

- A API pública vive no Django Ninja sob `/api/v1/`; não crie um serviço HTTP
  paralelo para reutilizar lógica que já pode ser chamada in-process.
- Rotas ficam finas: validam a borda, aplicam auth/gates e delegam para
  `service.py` ou `interface/`.
- Regras de negócio, transições de estado e persistência pertencem ao domínio,
  não ao arquivo de rota.
- Integrações externas ficam sob `integrations/`; domínios não devem espalhar
  chamadas HTTP diretas.
- `users` é um único Django app. Seus subdomínios (`auth`, `profiles`, `roles`,
  `documents`, `address`, `blocks`) compartilham o conjunto de migrations em
  `users/migrations/`.
- Use `ExternalIdModel` para entidades expostas na borda. IDs internos do banco
  não devem virar identificadores públicos por conveniência.

### Segurança e configuração

- Nunca versione `.env`, chaves JWT, credenciais, tokens, mídia privada ou dados
  reais. Esses caminhos já são ignorados pelo Git.
- Configuração e segredos entram por `core/settings.py`; não leia variáveis de
  ambiente de forma dispersa nos módulos de negócio.
- `APP_ENV` aceita somente `prod`, `staging`, `preview` ou `test`.
- `APP_ENV != prod` só sobe quando o hostname está em
  `TEST_MODE_ALLOWED_HOSTS`.
- `TEST_EXTERNAL_ADAPTERS=1` é proibido em `APP_ENV=prod`.
- Não enfraqueça os gates de JWT, role, superuser, segredo de serviço, IP interno
  ou autenticação de webhook para facilitar testes.
- Uploads privados devem respeitar `MEDIA_PRIVATE_PREFIXES` e a proteção de
  `core/media_views.py`.

### Dinheiro e idempotência

- Toda operação financeira deve ter referência externa isolada, proteção contra
  duplicidade e reconciliação do estado final.
- Preserve locks, `transaction.atomic()`, `select_for_update()` e chaves de
  idempotência existentes.
- Nunca trate “requisição aceita pelo gateway” como pagamento concluído.
  Confirme por webhook ou consulta de reconciliação.
- Payout, PIX, boleto e cobrança real não são testes comuns. Não execute ações
  financeiras reais sem autorização explícita e escopo definido.
- Fora de produção, payout permanece sintético. Não remova essa trava.

### Assíncrono

- O broker do Django-Q2 é o banco (`Q_CLUSTER["orm"] = "default"`); não suponha
  Redis.
- Enfileire efeitos assíncronos somente após o commit com
  `transaction.on_commit(...)`.
- Tasks precisam ser idempotentes e tolerar retry. O timeout deve cobrir o pior
  caso real e o retry deve ser maior que o timeout.
- Não mantenha transação de banco aberta enquanto aguarda rede externa.

### Erros e logs

- Erros de domínio herdam de `users.exceptions.DomainError`.
- Toda resposta de erro da API deve manter o envelope
  `{"detail": ..., "code": ..., ...extras}`.
- O frontend decide pelo `code`; nunca obrigue consumidores a interpretar
  `detail`.
- Não exponha traceback, segredo, payload sensível ou resposta crua de provider.
- Use `structlog`; não adicione `print()` em código de aplicação.
- Preserve `request_id`, contexto e eventos estruturados do middleware de log.

## Superfície HTTP

As APIs são montadas em `core/urls.py`. Cada grupo tem OpenAPI e documentação
próprios fornecidos pelo Django Ninja.

| Base | Público | Responsabilidade |
|---|---|---|
| `/api/v1/clients/` | leads, matriculados, alunos e veteranos | preço, captação, checkout, matrícula, documentos e jornada do aluno |
| `/api/v1/collaborators/` | candidatos e promotores | candidatura, documentos, contrato, treinamento, leads e resumo do promotor |
| `/api/v1/leadership/` | coordenadores | operação do polo, revisões, matrículas, candidatos, promotores e alunos |
| `/api/v1/staff/` | superuser | hubs, materiais, finanças, notificações, integrações, logs e usuários |
| `/api/v1/tools/` | serviços internos | ferramentas protegidas por segredo de serviço e allowlist de IP |
| `/api/v1/health/` | infraestrutura | liveness público |

Superfícies Django fora do Ninja:

- `/admin/`: administração nativa do Django.
- `/lead/checkout/<token>`: redirecionamento curto para checkout.
- `/integrations/asaas/`: webhooks e callbacks do Asaas.
- `/integrations/infinitepay/`: webhook da InfinitePay.
- `/integrations/whatsapp/`: webhook inbound da Evolution/WhatsApp.
- `/media/`: mídia pública ou protegida conforme o prefixo.

### Auth e autorização

- A API usa Bearer JWT RS256 via `django-ninja-jwt`.
- O token contém `external_id`, `roles` e `token_version`.
- `assign` e `promote` invalidam tokens antigos por `token_version`.
- `grant` e `revoke` de roles overlay, como `training`, não incrementam
  `token_version`; o estado vivo dessas travas deve ser lido do banco ou de
  `/me` após refresh normal.
- Login é passwordless por OTP, normalmente entregue por WhatsApp.
- Os grupos de funil expõem `/auth/check`, `/auth/login` e `/auth/refresh`.
- Gates comuns vivem em `api/auth.py`: `JWTAuth`, `require_roles` e
  `require_superuser`.
- Staff é `is_superuser` do Django; não invente uma role `staff` para contornar
  esse gate.

### Padrão de rota

```python
@api.post("/resource", response=ResourceOut, tags=["domain"])
def create_resource(request, payload: ResourceIn):
    require_roles(request.auth, "expected_role")
    return domain_service.create(
        user_external_id=request.auth.external_id,
        value=payload.value,
    )
```

Schemas de entrada e saída ficam próximos da API em `api/*.py` ou em
`api/schemas.py`. Validação estrutural pertence ao schema; invariantes e
transições pertencem ao service.

## Domínios e dependências

| Caminho | Responsabilidade |
|---|---|
| `core/` | settings, ambiente, URLs, modelos base, logs, mídia, rede e auditoria de integrações |
| `api/` | grupos Ninja, schemas, auth e adaptação HTTP |
| `users/auth/` | usuário customizado, OTP e JWT |
| `users/profiles/` | CPF, telefone, e-mail e dados pessoais |
| `users/roles/lead/` | captação, preço, checkout e confirmação de pagamento |
| `users/roles/enrollment/` | wizard de matrícula e liberação |
| `users/roles/candidate/` | candidatura de promotor e validações |
| `users/roles/promoter/` | promotor ativo/suspenso e operação comercial |
| `users/roles/training/` | materiais, atribuições e submissões |
| `users/roles/student/` | documentos finais, prova, pendências, diploma e veterano |
| `users/documents/` | RG, CNH, certidões, comprovante e documento militar |
| `users/address/` | endereço e consulta de CEP |
| `users/blocks/` | `ValidationBlock` e resolução accept-first |
| `hub/` | polo, marca, coordenador e fallback de captação |
| `finance/` | comissão, fechamento, despesas e solicitações de pagamento |
| `notify/` | templates, triggers, auditoria e despacho multicanal |
| `integrations/bank/` | Asaas e InfinitePay |
| `integrations/communication/` | WhatsApp/Evolution e SMTP |
| `integrations/tools/` | CEP, CPF e biometria |
| `integrations/ai/` | providers de IA, OCR/visão, TTS e auditoria de chamadas |
| `bot/` | webhook inbound e atendimento WhatsApp com guardrails |

Dependências devem apontar para interfaces públicas do domínio quando elas
existirem. Exemplos: `finance.interface`, `hub.interface`,
`notify.interface` e services de `users.roles.*`.

## Modelo de negócio

### Identidade e papéis

- `users.User` é o `AUTH_USER_MODEL` e usa `external_id` na borda.
- `Profile` armazena os dados pessoais compartilhados.
- `UserRole` mantém papéis ativos e histórico.
- As transições permitidas vêm de `ROLE_RULES` em settings.
- Cadeia principal do aluno: `lead -> enrollment -> student -> veteran`.
- Cadeia do colaborador: candidato aprovado vira `promoter`; `training` é
  overlay de bloqueio, não uma substituição permanente do promotor.

Não atribua, promova, conceda ou revogue roles atualizando tabelas diretamente.
Use `users.roles.interface`/`users.roles.service` para validar o catálogo e
preservar o contrato correto de `token_version`.

### Funis

**Aluno**

1. Lead é registrado e recebe checkout.
2. Pagamento confirmado cria/libera a matrícula.
3. Enrollment percorre RG, endereço, educação e selfie.
4. Após taxas e aprovação, a matrícula é concluída e cria Student.
5. Student percorre documentos, prova, pendências, diploma e retirada.
6. A retirada promove para Veteran e pode gerar comissão do coordenador.

**Promotor**

1. Candidate registra perfil, endereço, documento, PIX, educação e selfie.
2. Validações pesadas rodam de forma assíncrona.
3. Coordenador aprova ou rejeita.
4. A aprovação cria Promoter; materiais obrigatórios podem aplicar o overlay
   `training`.

**Accept-first**

Uploads e etapas elegíveis avançam o wizard antes do término da análise de IA.
Uma rejeição posterior cria `ValidationBlock`; o frontend lê o bloco em `/me` e
o reenvio correto o resolve. Não volte a bloquear o request esperando IA.

## Persistência e concorrência

- Use Django ORM e migrations versionadas.
- Mudança de model exige migration no app correto e
  `makemigrations --check --dry-run` limpo.
- Carregue relacionamentos com `select_related()`/`prefetch_related()` quando a
  serialização atravessar FKs ou coleções.
- Operações concorrentes de pagamento, comissão, OTP e notificação precisam de
  lock ou constraint apropriada.
- `notify.dispatch` usa claim de canal sob `select_for_update()`; preserve o
  protocolo `pending -> sending -> sent/failed/skipped`.
- Webhooks devem registrar/deduplicar o evento antes de aplicar efeitos.

## Integrações externas

Cada integração deve ter:

1. Cliente isolado com timeout e exceção própria.
2. Configuração central em settings.
3. Django system check quando configuração ausente for relevante.
4. Management command ou endpoint staff para health/test quando apropriado.
5. Testes com rede mockada; nenhum teste normal deve depender de provider real.

Integrações atuais incluem Asaas, InfinitePay, Evolution/WhatsApp, SMTP,
ViaCEP, CPFHub, InsightFace, providers OpenAI-compatible, Gemini, ElevenLabs e
MiniMax. Confirme o provider ativo pelos settings; presença de módulo não prova
que ele está configurado.

## Variáveis de ambiente

Não copie valores reais para documentação ou testes. As famílias principais são:

| Família | Exemplos |
|---|---|
| Django | `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `DATABASE_URL` |
| Ambiente de teste | `APP_ENV`, `TEST_MODE_ALLOWED_HOSTS`, `TEST_EXTERNAL_ADAPTERS`, `TEST_KYC_OUTCOME` |
| URLs da aplicação | `EXTERNAL_URL`, `FRONTEND_URL`, `LANDING_BASE_URL`, `MEDIA_LAN_BASE` |
| Auth | `JWT_*`, `OTP_*` |
| Notificação | `NOTIFY_*`, `WHATSAPP_*`, `MAIL_*` |
| Financeiro | `ASAAS_*`, `INFINITEPAY_*`, `COMMISSION_*`, `ENROLLMENT_PRICE_*` |
| IA e biometria | `IA_*`, `GEMINI_*`, `ELEVENLABS_*`, `MINIMAX_*`, `BIOMETRIC_*` |
| Bot e tools | `BOT_*`, `TOOLS_ALLOWED_IPS`, `CPFHUB_*`, `VIACEP_*` |
| Operação | `Q_*`, `SENTRY_*`, `INTEGRATION_*` |

Leia `core/settings.py` antes de adicionar uma variável. Evite duplicar
definições: uma mesma configuração deve ter uma única fonte de verdade.

## Estrutura de arquivos

```text
api/                    # adaptação HTTP Django Ninja por público
bot/                    # inbound WhatsApp e conversação
core/                   # projeto Django e infraestrutura compartilhada
finance/
  interface/            # API pública do domínio financeiro
  management/commands/
hub/
  interface/
integrations/
  ai/
  bank/{asaas,infinitepay}/
  communication/{mail,whatsapp}/
  tools/{biometric,cep,cpf}/
notify/
  interface/            # envio, eventos e templates
  sdk/                  # modo notify-server remoto
  management/commands/
users/
  auth/{jwt,otp}/
  profiles/
  address/
  documents/
  blocks/
  roles/{lead,enrollment,candidate,promoter,training,student}/
tests/                  # testes pytest de integração e fluxo
```

## Desenvolvimento local

```bash
uv sync --extra dev
uv run python manage.py migrate
uv run python manage.py runserver
```

O `.env` é obrigatório porque `SECRET_KEY` e `DEBUG` não têm default. Em
desenvolvimento, o banco padrão é `db.sqlite3`; em produção use
`DATABASE_URL`.

Worker assíncrono:

```bash
uv run python manage.py qcluster
```

Bootstrap e dados sintéticos:

```bash
uv run python manage.py seed_defaults
uv run python manage.py seed_test_collaborator
uv run python manage.py cleanup_test_data
```

Não rode seeds de teste em produção.

## Testes e verificação

Execute em ambiente isolado e sem adapters externos reais:

```bash
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uvx ruff@0.15.14 check .
uvx ruff@0.15.14 format --check .
uv run pytest tests/ -v --tb=short
```

Para `APP_ENV=test`, inclua o hostname atual em `TEST_MODE_ALLOWED_HOSTS`. No
PowerShell:

```powershell
$env:APP_ENV = "test"
$env:TEST_MODE_ALLOWED_HOSTS = [System.Net.Dns]::GetHostName()
$env:TEST_EXTERNAL_ADAPTERS = "0"
uv run pytest tests/ -v --tb=short
```

`TEST_EXTERNAL_ADAPTERS=0` é o padrão para validar contratos próximos de
produção. Ligue adapters sintéticos apenas quando o objetivo do teste exigir
explicitamente os fakes determinísticos.

O CI atual verifica:

1. Django system checks.
2. Models e migrations sincronizados.
3. Ruff lint e format.
4. Toda a suíte `tests/`.
5. `pip-audit --strict`.

O CI não configura relatório de cobertura. Ao adicionar comportamento, inclua
testes unitários e de integração proporcionais ao risco; fluxos críticos devem
ter teste HTTP ponta a ponta.

## Padrão de mudança

### Nova rota

1. Defina schemas de entrada/saída e código de erro estável.
2. Implemente primeiro o comportamento no service/interface do domínio.
3. Adicione a rota ao grupo correto e aplique o gate correto.
4. Teste sucesso, auth, role, validação, estado inválido e idempotência.
5. Atualize `docs/api/<grupo>.md` quando o contrato do frontend mudar.

### Nova integração

1. Crie o módulo sob `integrations/`.
2. Centralize config e timeout em settings.
3. Adicione client, exceções, system check e teste de health.
4. Mocke a rede nos testes.
5. Exponha ao domínio por interface estreita; não vaze resposta do provider.

### Mudança de model

1. Escreva o teste que descreve a regra.
2. Altere o model e gere a migration no app dono.
3. Revise migration, constraints, índices e caminho de rollback.
4. Rode a suíte e o check de migrations.
5. Para backfill, torne a operação reexecutável e segura para dados existentes.

## Management commands úteis

- Saúde: `ai_ping`, `ai_providers`, `biometric_health`, `mail_health`,
  `whatsapp_health`.
- Operação financeira: `commission_credit`, `commission_close`,
  `commission_process`, `finance_schedules`, `fee_request`.
- Notificação: `notify_send`, `notify_seed_templates`, `staff_digest`,
  `payment_reminder`.
- Dados controlados: `seed_defaults`, `seed_test_collaborator`,
  `cleanup_test_data`, `otp_reset_ratelimit`.
- Ferramentas: `cpfhub_lookup`, `viacep_lookup`, `biometric_test`.

Leia `--help` antes de executar comandos com efeitos externos. Comandos de
envio, cobrança, payout ou teste de provider podem produzir efeitos reais.

## Restrições e dívidas conhecidas

- `NOTIFY_MODE`, `NOTIFY_SERVER_URL`, `NOTIFY_API_KEY` e `NOTIFY_TIMEOUT`
  aparecem em dois blocos de `core/settings.py`; hoje a definição posterior
  vence. Não adicione uma terceira fonte. Uma consolidação deve preservar os
  defaults e contratos efetivamente usados.
- O deploy instala `gunicorn`, `uvicorn` e `psycopg` de forma operacional em
  `.github/workflows/deploy.yml`; eles não estão declarados em
  `pyproject.toml`. Verifique o workflow antes de alterar o runtime de produção.
- O rollback automatizado restaura código, mas não desfaz migrations de banco.
  Toda migration de produção precisa de estratégia própria de compatibilidade e
  rollback.
- Há services e arquivos de API grandes, especialmente nos domínios de
  enrollment, candidate, student e leadership. Evite ampliar esses hotspots;
  extraia comportamento coeso para módulos menores sem quebrar as interfaces.
- O gate de mídia privada permite revisão por coordenador, mas o escopo por polo
  ainda merece revisão em `core/media_views.py`. Não amplie o acesso sem teste
  de autorização por proprietário e papel.

## Definition of done

Uma mudança só está pronta quando:

- o comportamento solicitado está implementado no domínio correto;
- auth, permissões, transições e idempotência foram preservadas;
- migrations estão sincronizadas e revisadas;
- testes relevantes passam sem depender de rede real;
- ruff, Django checks e auditoria de dependências passam;
- documentação de contrato foi atualizada quando necessário;
- não há segredo, dado pessoal, mídia privada ou alteração não relacionada no
  diff.

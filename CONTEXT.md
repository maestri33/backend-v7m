# backend-supletivo

Glossário do domínio da plataforma V7M — supletivo (EJA) vendido por promotores em polos físicos.
É a linguagem canônica do projeto: use estes termos em nomes de variável, função, arquivo, teste,
título de issue e mensagem de commit, e não derive pros sinônimos listados em `_Avoid_`.

Só entra aqui o que é específico deste domínio. Conceito geral de programação não entra, mesmo
que o projeto use muito. Isto é um glossário — não é spec, não é scratch pad, e não guarda decisão
de implementação (essas vão pra `docs/adr/`).

## Funil do aluno

**Lead**:
Aspirante a aluno, captado por um promotor. Nasce sempre ligado ao promotor que o captou e paga
antes de virar qualquer outra coisa.
_Avoid_: prospecto, contato, interessado, cliente

**Enrollment** (matrícula):
A role que o lead vira ao pagar. A responsabilidade passa do promotor pro polo, e é aqui que roda
a coleta de documento, endereço, dados escolares e selfie.
_Avoid_: inscrição, cadastro, registro

**Student** (aluno):
Quem o coordenador liberou depois da matrícula concluída. Estuda, faz prova e retira diploma.
_Avoid_: estudante, matriculado

**Veteran** (veterano):
Aluno que fechou o ciclo e retirou o diploma. É o evento que credita comissão ao coordenador do polo.
_Avoid_: formado, egresso, concluinte

**Taxa**:
As duas parcelas que o polo cobra internamente na matrícula, depois da coleta. O aluno nunca vê —
na visão dele a matrícula segue "aguardando liberação".
_Avoid_: mensalidade, anuidade

**Selfie**:
A foto do rosto que assina a matrícula (aluno) ou fecha a coleta (candidato). É ato de assinatura,
não documento.

## Funil do colaborador

**Candidate** (candidato):
Aspirante a promotor. Nasce ligado a um polo e percorre a mesma coleta do aluno, mais chave Pix
validada no DICT.
_Avoid_: aspirante, inscrito, trainee

**Promoter** (promotor):
Colaborador ativo que capta leads pelo próprio link de indicação. Pertence a um polo.
_Avoid_: vendedor, afiliado, indicador, parceiro

**Coordenador**:
O promotor que responde por um polo. Libera matrícula, aprova ou rejeita candidato, corrige prova,
emite diploma e ganha a comissão de veterano.
_Avoid_: gerente, supervisor, admin

**Training** (treino):
A trava do painel do promotor: enquanto houver matéria obrigatória pendente, ele não opera.
_Avoid_: curso, LMS, onboarding, capacitação

**Material** (matéria):
A unidade do treino — conteúdo, uma questão e um gabarito. **Fixa** vai pra todo promotor novo;
**transitória** vai só pros que já existiam quando foi publicada. **Blocking** é a que trava o painel.
_Avoid_: aula, módulo, lição

**Pré-matriculado**:
Promotor sem ensino médio completo. Recebe abordagem diferenciada e, aos 3 leads pagos, entra
sozinho na própria matrícula como bolsista.

**Bolsista**:
A matrícula que nasce de um promotor pré-matriculado. O teste final dele exige um mínimo de leads
pagos, além dos requisitos normais.

**Auto-matrícula**:
Promotor que se matricula pra estudar. Tem preço próprio e não gera comissão pra ninguém.

## Estrutura

**Hub** (polo):
A unidade física. Tem endereço, marca e um coordenador. O **polo padrão** é o fallback de captação
de quem chega sem indicação.
_Avoid_: unidade, filial, franquia, escola

**Marca**:
A bandeira comercial sob a qual um polo opera. O catálogo de marcas válidas é configuração, não
lista fixa no código.

**Role** (papel):
Uma atribuição dada a um usuário, ativa até ser revogada. O catálogo de quais roles existem e como
uma vira outra é configuração.
_Avoid_: perfil, permissão, tipo de usuário

**Digivolver**:
Trocar a role de um usuário por outra: revoga a anterior e cria a nova, preservando o histórico.
É a única forma de avançar no funil.
_Avoid_: promover, migrar, converter

**Grupo**:
Uma das cinco fatias públicas da API, cada uma com o próprio público: `clients` (funil do aluno),
`collaborators` (funil do colaborador), `leadership` (coordenador), `staff` (administração da
plataforma) e `tools` (integrações internas).
_Avoid_: namespace, módulo da API, endpoint group

## Dinheiro

**Comissão**:
Um crédito a um beneficiário, disparado por um evento do funil — lead pago, aluno virando veterano,
ou bônus por bater meta na semana. Fica pendente até o fechamento.
_Avoid_: bonificação, repasse, ganho

**Fechamento**:
A rodada semanal que agrupa as comissões pendentes numa solicitação de pagamento e dispara o Pix.
_Avoid_: payout, batch, liquidação

**Solicitação de pagamento**:
O agrupamento de comissões de um beneficiário que vira um Pix único, com a chave congelada no
momento do fechamento.

## Validação

**Bloqueio**:
A flag que o app lê e exibe como modal bloqueante quando uma validação rejeitou algo em definitivo.
Só sai quando o usuário reenvia o que foi rejeitado.
_Avoid_: pendência, erro, trava

**Pendência**:
O "conferir" do aluno: um documento ou uma taxa que o coordenador apontou como faltando. Diferente
de **bloqueio** — pendência é do fluxo do aluno, bloqueio é rejeição de validação.

"""Catálogo central das mensagens de notificação dos funis (TEOR + regra de TTS).

Regras (Victor 2026-06-05):
- **Toda troca de role notifica os envolvidos.** Cada serviço dispara no seu ponto; o TEOR mora AQUI.
- **O primeiro nome do destinatário aparece pelo menos 2× em cada mensagem** (proximidade/calor).
- **TTS (voz) só em MOMENTOS ESPECIAIS** (acolhimento/conquista do aluno/colaborador). O resto é texto.
- O teor é **editável pelo Victor**; o mapa fica em `wiki/notify/notify.md` (só cita as chaves daqui).

Como usar (serviços):
    from users.roles import notifications as msgs
    name = msgs.first_name(profile.name)
    send(text=msgs.text("lead.paid", name=name), caller="lead.paid",
         tts=msgs.is_tts("lead.paid"), gender=profile.gender if msgs.is_tts("lead.paid") else None, ...)

`{name}` = primeiro nome do DESTINATÁRIO (sempre ≥2×). Placeholders extras conforme o evento
(`{valor}`, `{link}`, `{payload}`, `{lead_name}`, `{detail}`, `{doc_type}`, `{ref_url}`).
"""

from __future__ import annotations

# Eventos que vão por VOZ (TTS). Tudo que não está aqui é só texto (regra "TTS só em momento especial").
# São os marcos de acolhimento/conquista do aluno/colaborador.
_TTS_EVENTS = frozenset(
    {
        # Voz só nos PICOS (enxugado 2026-06-21: voz demais mata o "momento especial").
        "lead.paid",  # recomeço: a matrícula começou (o recibo vai à parte, em texto)
        "enrollment.selfie_approved",  # assinatura da matrícula — com o próprio rosto
        "student.diploma_issued",  # o diploma: o ápice da jornada
        "training.approved",  # virou promotor (já pode captar) — conquista do colaborador
        "training.approved.scholarship",  # virou promotor + ganhou a trilha da bolsa
        "training.cleared",  # concluiu o treino obrigatório → painel liberado
        "promoter.scholarship_enrolled",  # 3 matrículas pagas → bolsa efetivada
    }
)

_FALLBACK_NAME = "tudo bem"  # quando não há nome (raro: CPFHub quase sempre traz)

# Teor por evento. ALUNO/LEAD/colaborador = caloroso e personalizado; promotor/coordenador = direto,
# mas SEMPRE com o primeiro nome do destinatário 2×.
_MESSAGES: dict[str, str] = {
    # ── LEAD (funil do aluno) ────────────────────────────────────────────────
    "lead.captured": (
        "Olá, {name}! 🎉 Que bom ter você com a gente. Seu cadastro está pronto, {name} — "
        "falta só um passo pra garantir sua vaga: concluir o pagamento. Em instantes envio o link. "
        "Bora juntos nessa jornada!"
    ),
    "lead.captured.promoter": (
        "Boa notícia, {name}! {lead_name} acaba de entrar na sua rede pela sua indicação. "
        "Incentive a concluir o pagamento, {name}. 👊"
    ),
    "lead.checkout.pix": (
        "{name}, para concluir sua matrícula pague o PIX de {valor}:\n{link}\n\n"
        "Ou use o PIX copia-e-cola, {name}:\n{payload}"
    ),
    "lead.checkout.card": (
        "{name}, para concluir sua matrícula pague {valor} no cartão:\n{link}\n\n"
        "Qualquer dúvida é só chamar, {name}."
    ),
    "lead.paid": (
        "Parabéns, {name}! 🎉 Seu pagamento foi confirmado e sua matrícula começou. "
        "Você deu um passo importante, {name} — em breve enviamos os próximos passos."
    ),
    "lead.paid.receipt": (
        "{name}, aqui está o comprovante do seu pagamento de {valor}:\n{link}\n"
        "Guarde para referência, {name}."
    ),
    "lead.paid.coordinator": (
        "{name}, uma nova matrícula entrou no seu polo. "
        "Acompanhe quando o aluno preencher os dados, {name}."
    ),
    "lead.paid.promoter": (
        "Boa notícia, {name}: sua indicação virou uma matrícula paga! ✅ "
        "A comissão já entrou no próximo fechamento semanal, {name}."
    ),
    "lead.paid.promoter.scholarship": (
        "Boa notícia, {name}: sua indicação virou uma matrícula paga! ✅ "
        "A comissão já entrou no próximo fechamento semanal. {progress_text} Continue firme, {name}."
    ),
    # ── ENROLLMENT (coleta → liberação) ──────────────────────────────────────
    "enrollment.awaiting_release": (
        "{name}, uma matrícula concluiu o envio de dados e aguarda a sua liberação no painel. "
        "Confira quando puder, {name}."
    ),
    "enrollment.released": (
        "{name}, é oficial: você é nosso aluno! 💚 Sua matrícula foi liberada. "
        "Seja muito bem-vindo(a), {name} — a sua escola estava esperando por você."
    ),
    # dados de acesso à plataforma da instituição parceira — enviados ao virar ALUNO (WhatsApp +
    # e-mail). Sem TTS (não se lê senha em voz). É a única coisa que o aluno recebe daqui pra frente.
    "enrollment.credentials": (
        "{name}, aqui estão seus dados de acesso à plataforma de estudos:\n\n"
        "🔗 {link}\n"
        "👤 Login: {login}\n"
        "🔑 Senha: {password}\n\n"
        "Guarde com você, {name} — é por aqui que você entra nas suas aulas. Bons estudos! 📚"
    ),
    "enrollment.selfie_rejected": (
        "{name}, sua selfie não pôde ser confirmada. Envie uma nova foto pelo aplicativo, "
        "nítida e mostrando bem o rosto, {name}."
    ),
    "enrollment.selfie_approved": (
        "{name}, sua matrícula está assinada. ✍️ E quem assinou foi você, com o seu próprio rosto — "
        "ninguém fez isso por você. Esse passo é seu pra sempre, {name}. Agora é com a gente: "
        "assim que estiver tudo conferido, a gente te avisa por aqui."
    ),
    "enrollment.selfie_in_review": (
        "{name}, a selfie de uma matrícula precisa da sua análise — a IA ficou em dúvida. "
        "Aprove ou reprove no painel, {name}."
    ),
    # RG da matrícula — validação por IA (plan/12). {detail} = motivo dado pela IA/coordenador.
    "enrollment.rg_rejected": (
        "{name}, precisamos de uma nova foto do seu RG: {detail} "
        "Reenvie pelo aplicativo, {name} — é rapidinho. 📄"
    ),
    "enrollment.rg_in_review": (
        "{name}, o RG de uma matrícula precisa da sua análise: {detail} "
        "Aprove ou reprove no painel, {name}."
    ),
    "enrollment.address_proof_rejected": (
        "{name}, o comprovante de endereço precisa de ajuste: {detail} "
        "Reenvie pelo app, {name}. 📄"
    ),
    "training.submission_rejected": (
        "{name}, sua resposta precisa de ajuste: {detail} "
        "Refaça pelo app quando puder. 📝"
    ),
    "enrollment.concluded_referral": (
        "{name}, um aluno que você indicou acabou de virar aluno. ✅ Bônus creditado. "
        "Continue indicando! 🚀"
    ),
    "enrollment.rg_approved": (
        "Tudo certo, {name}! ✅ Seu RG foi aprovado e sua matrícula segue em frente. "
        "Continue o preenchimento, {name}."
    ),
    # Ciclo da TAXA da matrícula (plan/14, Victor 2026-06-12) — TODOS pro COORDENADOR, nunca pro
    # aluno (política interna do polo). Sem TTS (não é momento especial). {student_name} = o aluno.
    "enrollment.fee_paid": (
        "{name}, a 1ª parcela da taxa de {student_name} foi PAGA ({valor}). ✅ "
        "A instituição já pode liberar o login e a senha — conclua a matrícula no painel, {name}."
    ),
    "enrollment.fee_scheduled": (
        "{name}, a 2ª parcela da taxa de {student_name} ({valor}) foi agendada para {due_date}. "
        "O pagamento sai sozinho no vencimento, {name}."
    ),
    "enrollment.fee_due_paid": (
        "{name}, a 2ª parcela da taxa de {student_name} ({valor}) foi PAGA no vencimento. ✅ "
        "Taxa quitada, {name} — nada mais a fazer."
    ),
    "enrollment.fee_problem": (
        "{name}, deu problema na taxa de {student_name}: {detail} "
        "Confira no painel, {name}, e tente de novo se for o caso."
    ),
    # ── CANDIDATE → PROMOTER → (treino overlay) (funil do colaborador, Victor 2026-06-16) ──
    "candidate.selfie_rejected": (
        "{name}, recebemos sua foto, mas ainda não conseguimos confirmar sua identidade com segurança. "
        "Abra o aplicativo, fique de frente para a câmera, em um lugar claro, e tente novamente. "
        "Seu cadastro continua salvo, {name}."
    ),
    "candidate.rejected": (
        "{name}, seu cadastro de colaborador não foi aprovado neste momento. "
        "Fale com o coordenador do seu polo para entender os próximos passos, {name}."
    ),
    "candidate.selfie_approved": (
        "Foto confirmada, {name}! ✅ Você não precisa enviar outra. "
        "Estamos concluindo automaticamente as últimas validações e avisaremos assim que seu acesso estiver pronto, {name}."
    ),
    "candidate.selfie_in_review": (
        "{name}, a foto de confirmação de {candidate_name} precisa da sua análise. "
        "O sistema não conseguiu decidir com segurança; revise no painel e registre a decisão, {name}."
    ),
    # Documento do CANDIDATO (plan/15 B) — espelho do aluno; mesmo catálogo, chaves próprias.
    "candidate.document_rejected": (
        "{name}, recebemos seu documento, mas ele ainda não pôde ser aprovado.\n\n"
        "Motivo: {detail}\n\n"
        "Abra o aplicativo e envie uma nova imagem completa e legível. Seu cadastro continua salvo, {name}. 📄"
    ),
    "candidate.document_in_review": (
        "{name}, o documento de {candidate_name} precisa da sua análise. "
        "O sistema não conseguiu decidir com segurança; revise as imagens e registre a decisão no painel, {name}."
    ),
    "candidate.document_approved": (
        "Documento aprovado, {name}! ✅ Você não precisa reenviá-lo. "
        "O aplicativo liberará automaticamente o próximo passo ou concluirá seu cadastro, {name}."
    ),
    "candidate.address_proof_rejected": (
        "{name}, recebemos o arquivo, mas ele não pôde ser aceito como comprovante de endereço.\n\n"
        "Motivo: {detail}\n\n"
        "Envie pelo aplicativo uma conta ou documento recente que mostre o endereço completo. "
        "O restante do seu cadastro continua salvo, {name}."
    ),
    # coordenador destravou o tipo de documento (candidato escolheu RG/CNH errado). → candidato.
    "candidate.doc_type_reset": (
        "{name}, liberamos o reenvio do seu documento — pode mandar a foto do tipo certo (RG ou CNH). "
        "É só subir de novo pelo aplicativo, {name}. 📄"
    ),
    # candidato concluiu a coleta e aguarda a APROVAÇÃO do coordenador (vira promotor). → coordenador.
    "candidate.awaiting_approval": (
        "{name}, um candidato concluiu o cadastro e aguarda a sua aprovação para virar promotor. "
        "Confira no painel, {name}."
    ),
    # coordenador aprovou → virou PROMOTOR e NÃO há treino obrigatório pendente (já pode captar). TTS.
    "training.approved": (
        "Deu certo, {name}! 🎉 Seu cadastro foi aprovado e seu acesso de promotor está ativo. "
        "Seu link exclusivo já está no painel: compartilhe com quem quer voltar a estudar. "
        "Cada matrícula paga entra nas suas metas e comissões, {name}."
    ),
    "training.approved.scholarship": (
        "Deu certo, {name}! 🎉 Seu acesso de promotor está ativo e você também entrou na trilha da bolsa. "
        "Seu link exclusivo já está no painel. Ao conquistar {enroll_goal} matrículas pagas, sua própria matrícula como aluno é efetivada; "
        "com {exam_goal}, você cumpre o requisito de indicações para a prova final. "
        "Você pode transformar outras vidas e retomar seus estudos, {name}."
    ),
    # virou promotor MAS há treino obrigatório pendente → painel travado até concluir. Sem TTS.
    "training.must_train": (
        "Você foi aprovado, {name}! 🎉 Falta apenas concluir o treinamento obrigatório no aplicativo. "
        "Assim que terminar, o painel e o link de indicação serão liberados automaticamente para você, {name}."
    ),
    "training.must_train.scholarship": (
        "Você foi aprovado, {name}! 🎉 Além do acesso de promotor, você entrou na trilha da bolsa. "
        "Primeiro, conclua o treinamento obrigatório no aplicativo; depois, seu link será liberado. "
        "Com {enroll_goal} matrículas pagas sua matrícula como aluno é efetivada, e com {exam_goal} você cumpre o requisito de indicações da prova final, {name}."
    ),
    # concluiu TODAS as matérias obrigatórias → painel liberado (já pode captar). TTS.
    "training.cleared": (
        "Treinamento concluído, {name}! 🎉 Seu painel está liberado e seu link de captação ativo. "
        "Agora é com você, {name} — comece a indicar e a ganhar!"
    ),
    # staff publicou uma nova matéria obrigatória → o promotor é re-travado até concluí-la. Sem TTS.
    "training.new_material": (
        "{name}, há um novo treinamento obrigatório no aplicativo. "
        "Conclua a atividade para continuar usando o painel, {name}."
    ),
    "promoter.scholarship_enrolled": (
        "Você conseguiu, {name}! 🎓 Suas {enroll_goal} matrículas pagas efetivaram sua bolsa e sua matrícula como aluno começou sem cobrança. "
        "Agora siga o fluxo normal de estudos e documentos. Ao chegar a {exam_goal} matrículas pagas, "
        "você cumpre o requisito de indicações para a prova final. Parabéns por essa conquista, {name}!"
    ),
    # ── STUDENT → VETERAN ────────────────────────────────────────────────────
    "student.document_rejected": (
        "{name}, seu documento ({doc_type}) precisa ser reenviado. "
        "Envie uma nova foto, nítida e legível, {name}."
        "{reason_text}"
    ),
    "student.document_in_review": (
        "{name}, um documento de aluno ({doc_type}) precisa da sua análise — a IA ficou em dúvida. "
        "Aprove ou reprove no painel, {name}."
    ),
    "student.exam_released": (
        "{name}, seus documentos foram aprovados! Você já pode agendar a sua prova quando quiser, {name}."
    ),
    "student.exam_scheduled": (
        "{name}, um aluno do seu polo agendou a prova e aguarda a sua correção. Confira no painel, {name}."
    ),
    "student.exam_passed": (
        "Você foi APROVADO na prova, {name}! 🎉 Estamos finalizando a sua documentação, {name}. Falta pouco!"
    ),
    "student.exam_failed": (
        "{name}, você não atingiu a nota desta vez — mas não desanime. "
        "Reagende para uma nova tentativa, {name}, você consegue!"
    ),
    "student.pendency_opened": (
        "{name}, há uma pendência na sua matrícula: {detail}. "
        "Resolva para seguir com a emissão do diploma, {name}."
    ),
    "student.diploma_issued": (
        "{name}, chegou o grande dia: o seu diploma está pronto! 🎓 Você terminou os seus estudos — "
        "o que um dia ficou para trás, hoje você concluiu. E isso é seu para sempre, {name}. "
        "Parabéns! A gente tem muito orgulho de você."
    ),
    # logística separada (texto), pra não contaminar o momento emocional do diploma acima.
    "student.diploma_pickup": (
        "Para retirar o seu diploma, {name}, é só procurar o coordenador do seu polo. "
        "Ele já está esperando por você, {name}."
    ),
    "student.veteran": (
        "{name}, agora você é veterano da nossa escola. 💚 Você chegou até o fim — e quem chega ao "
        "fim inspira quem ainda está começando. Bem-vindo ao time, {name}!"
    ),
    "student.veteran.coordinator": (
        "{name}, um aluno do seu polo se formou e foi diplomado. ✅ "
        "Sua comissão entra no próximo fechamento, {name}. 💸"
    ),
    # ── HUB / ROLES (designação de coordenador) ──────────────────────────────
    "hub.coordinator_assigned": (
        "Parabéns, {name}! Você agora é COORDENADOR de um polo. "
        "{name}, acompanhe as matrículas e libere os alunos pelo painel."
    ),
    # coordenador suspende / reativa um promotor do polo (WP5).
    "promoter.suspended": (
        "{name}, sua atuação como promotor foi temporariamente suspensa pelo coordenador do polo. "
        "Fale com o coordenador para regularizar, {name}."
    ),
    "promoter.reactivated": (
        "Que bom te ver de volta, {name}! Sua atuação como promotor foi reativada. "
        "{name}, seu link de captação está ativo de novo — bora!"
    ),
}


def first_name(full_name: str | None) -> str:
    """Primeiro nome do destinatário (para personalizar). Sem nome → fallback neutro."""
    if not full_name or not full_name.strip():
        return _FALLBACK_NAME
    return full_name.strip().split()[0]


def full_name(name: str | None) -> str:
    """Nome COMPLETO do destinatário (placeholder `{nome-completo}`). Sem nome → fallback neutro."""
    if not name or not name.strip():
        return _FALLBACK_NAME
    return " ".join(name.strip().split())


def text(event: str, **ctx) -> str:
    """Renderiza o teor do evento. `name` (1º nome do destinatário) deve vir no ctx (≥2× no texto).

    Fonte de verdade = DB (`notify.Template`); catálogo `_MESSAGES` (in-memory) é fallback quando
    a row não existe (bootstrap, DB fora do ar). Renderer por regex: placeholders ausentes ficam
    literais (não quebra o envio); `{nome}`/`{nome-completo}` funcionam (hífen)."""
    ctx.setdefault("name", _FALLBACK_NAME)
    # compat: alguns notifies recebem `reason`; o template pode usar {reason_text}.
    if "reason" in ctx and "reason_text" not in ctx:
        reason = ctx.get("reason")
        ctx["reason_text"] = f" Motivo: {reason}." if reason else ""
    from notify.interface import templates as _db

    body, _data = _db.render_event(event, ctx)
    if body is not None:
        return body
    template = _MESSAGES.get(event)
    if template is None:
        raise KeyError(f"notify event sem teor no catálogo: {event}")
    return _db.render(template, ctx)


def is_tts(event: str) -> bool:
    """True se o evento é um MOMENTO ESPECIAL (vai por voz). Default = texto.

    DB (`notify.Template.is_tts`) é fonte de verdade; `_TTS_EVENTS` (in-memory) é fallback."""
    from notify.interface import templates as _db

    db = _db.is_tts(event)
    if db is not None:
        return db
    return event in _TTS_EVENTS


def age_from(birth_date) -> int | None:
    """Idade em anos a partir da data de nascimento (None se não houver) — pro storytelling adaptar o tom."""
    if not birth_date:
        return None
    from datetime import date

    today = date.today()
    return (
        today.year
        - birth_date.year
        - ((today.month, today.day) < (birth_date.month, birth_date.day))
    )


# ── Storytelling por IA nos marcos (Victor 2026-06-21) ──────────────────────────
# Marcos que ganham TEXTO GERADO por 1 LLM (caloroso, personalizado) — SEMPRE com fallback pro
# teor fixo acima se a IA falhar/vier ruim. Público de EJA: simples, digno, sem erro de português.
_STORY_EVENTS = frozenset({"enrollment.selfie_approved", "student.diploma_issued"})

_STORY_INSTRUCTIONS = {
    "enrollment.selfie_approved": (
        "Você escreve para {name}, um(a) aluno(a) adulto(a) da educação de jovens e adultos (EJA), "
        "público simples e batalhador, que acabou de ASSINAR a matrícula com a própria selfie. "
        "Hoje é {data_hoje} — pode citar a data como o dia em que ele(a) deu esse passo. {faixa_etaria} "
        "Escreva uma mensagem calorosa e curta (no máximo 3 frases) celebrando que foi ELE(A) quem "
        "assinou, com o próprio rosto, e que agora é só aguardar a liberação. Trate por '{name}'. "
        "Português impecável, sem erros, sem gírias, sem emoji, sem inventar outros fatos."
    ),
    "student.diploma_issued": (
        "Você escreve para {name}, um(a) aluno(a) adulto(a) da EJA, público simples e batalhador, "
        "que ACABOU de ter o diploma emitido — muitas vezes um sonho adiado por décadas. Hoje é "
        "{data_hoje} — pode citar a data como o dia em que ele(a) concluiu. {faixa_etaria} "
        "Escreva uma mensagem curta (no máximo 3 frases), emocionante e digna, dizendo que terminou "
        "os estudos e que isso é dele(a) para sempre. Trate por '{name}'. NÃO fale de retirada nem "
        "logística. Português impecável, sem erros, sem gírias, sem emoji, sem inventar outros fatos."
    ),
}


def story_text(
    event: str, *, name: str, fallback: str, age: int | None = None, **_ctx
) -> str:
    """Texto caloroso gerado por 1 LLM (temperatura baixa) nos marcos especiais; cai no `fallback`
    fixo se o evento não for de história, se a IA falhar, ou se o texto vier ruim (curto/sem o nome).
    Enriquece com a DATA de hoje e adapta o tom à IDADE (Victor 2026-06-21). Roda síncrono no caller;
    mantenha o caller fora do request quando possível (o de selfie é async).

    DB (`notify.Template`) é fonte de verdade: `storytelling=False` DESLIGA o story (mesmo se o
    evento estiver no catálogo in-memory); `story_prompt` sobrescreve a instrução. Sem row no DB,
    cai no `_STORY_EVENTS`/`_STORY_INSTRUCTIONS` in-memory."""
    from notify.interface import templates as _db

    data = _db.get(event)
    if data is not None:
        if not data.storytelling:
            return fallback  # DB diz: não é story — honra mesmo se o catálogo in-memory dissesse sim
        instruction_raw = data.story_prompt or _STORY_INSTRUCTIONS.get(event)
        if not instruction_raw:
            return fallback
    else:
        if event not in _STORY_EVENTS:
            return fallback
        instruction_raw = _STORY_INSTRUCTIONS[event]
    try:
        from datetime import date

        from integrations.ai import service as ai

        _meses = (
            "janeiro",
            "fevereiro",
            "março",
            "abril",
            "maio",
            "junho",
            "julho",
            "agosto",
            "setembro",
            "outubro",
            "novembro",
            "dezembro",
        )
        hoje = date.today()
        data_hoje = f"{hoje.day} de {_meses[hoje.month - 1]} de {hoje.year}"
        if age is None:
            faixa = ""
        elif age >= 50:
            faixa = (
                f"A pessoa tem cerca de {age} anos: honre, com respeito e sem nenhum espanto, a "
                "coragem de retomar os estudos mais tarde na vida."
            )
        elif age >= 30:
            faixa = (
                f"A pessoa tem cerca de {age} anos: reconheça a determinação de estudar conciliando "
                "com o trabalho e a vida adulta."
            )
        else:
            faixa = (
                f"A pessoa tem cerca de {age} anos: celebre, com entusiasmo, que está garantindo o "
                "futuro cedo."
            )

        instruction = _db.render(
            instruction_raw,
            {"name": name, "nome": name, "data_hoje": data_hoje, "faixa_etaria": faixa},
        )
        out = ai.generate_text(
            f"Escreva a mensagem para {name}.",
            caller=f"story.{event}",
            instruction=instruction,
            temperature=0.6,
            # DeepSeek (NÃO o MiniMax-M3, que volta vazio aqui) + orçamento alto: o reasoner gasta
            # tokens "pensando" no <think> (que é removido); sem orçamento sobra string vazia, e o
            # guard cai no teor fixo. 1000 dá folga pra pensar E escrever. Falha → teor fixo.
            max_tokens=1000,
            model="deepseek-v4-pro",
        )
        out = (out or "").strip().replace("**", "")
        # guarda-chuva: precisa existir, ser substancial e citar o nome — senão usa o teor fixo.
        if len(out) < 20 or name.lower() not in out.lower():
            return fallback
        return out
    except Exception:  # noqa: BLE001 — IA é enfeite; jamais deixa um marco sem mensagem
        return fallback


# sentinela do story_or_none: contém NUL, impossível de sair da IA — detecta o caminho de fallback.
_STORY_MISS = "\x00sem-story\x00"


def story_or_none(event: str, *, name: str, age: int | None = None) -> str | None:
    """Como `story_text`, mas devolve None quando NÃO há história (evento sem story, storytelling
    desligado no Template, IA falhou ou o texto veio ruim), em vez do fallback fixo.

    Uso do modo remote (Fase 2): None = sem override, o notify-server renderiza o template DELE;
    texto = vai como `body_md_override`. Delega ao `story_text` (mesma decisão e geração — zero
    drift) passando o sentinela como fallback."""
    out = story_text(event, name=name, fallback=_STORY_MISS, age=age)
    return None if out == _STORY_MISS else out

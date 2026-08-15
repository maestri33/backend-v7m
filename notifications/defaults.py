"""Catálogo fixo de eventos e valores iniciais.

O banco é a fonte usada em runtime. Este catálogo só cria eventos ausentes e atualiza
linhas que ainda não foram personalizadas no admin.
"""

from __future__ import annotations

from string import Formatter


def _keys(*values: str | None) -> list[str]:
    result: set[str] = {"nome", "nome-completo", "nome_completo", "name"}
    for value in values:
        for _, field_name, _, _ in Formatter().parse(value or ""):
            if field_name:
                result.add(field_name)
    return sorted(result)


def _entry(
    body: str,
    *,
    title: str | None = None,
    subject: str | None = None,
    channels: str = "whatsapp,email",
    is_tts: bool = False,
    storytelling: bool = False,
    story_prompt: str | None = None,
    media_url: str | None = None,
    media_type: str = "",
    mail_template: str = "default",
    active: bool = True,
) -> dict:
    return {
        "title": title,
        "subject": subject,
        "body": body,
        "channels": channels,
        "is_tts": is_tts,
        "storytelling": storytelling,
        "story_prompt": story_prompt,
        "media_url": media_url,
        "media_type": media_type,
        "mail_template": mail_template,
        "active": active,
        "context_keys": _keys(title, subject, body),
    }


NOTIFICATION_DEFAULTS = {
    'auth.otp': _entry(
        'Seu código de acesso é {codigo}. Ele vale por {ttl_minutos} '
        'minutos.{rodape}',
        channels='whatsapp',
    ),
    'auth.cpf_conflict': _entry(
        'Alerta de segurança: em {data} às {hora}, alguém tentou usar seu CPF com o '
        'número {numero}. O cadastro foi bloqueado e desfeito automaticamente. Se '
        'não foi você, fale com o nosso suporte por este WhatsApp.',
        title='Alerta de segurança',
        channels='whatsapp',
    ),
    'candidate.awaiting_approval': _entry(
        '{name}, um candidato concluiu o cadastro e aguarda a sua aprovação para '
        'virar promotor. Confira no painel, {name}.'
    ),
    'candidate.doc_type_reset': _entry(
        '{name}, liberamos o reenvio do seu documento — pode mandar a foto do tipo '
        'certo (RG ou CNH). É só subir de novo pelo aplicativo, {name}. 📄'
    ),
    'candidate.document_approved': _entry(
        'Pode seguir, {name}! ✅ Seu documento foi aprovado e o cadastro segue em '
        'frente. Continue o preenchimento, {name}.'
    ),
    'candidate.document_in_review': _entry(
        '{name}, o documento de um candidato precisa da sua análise — a IA ficou em '
        'dúvida. Aprove ou reprove no painel, {name}.'
    ),
    'candidate.document_rejected': _entry(
        '{name}, precisamos de uma nova foto do seu documento: {detail} Reenvie '
        'pelo aplicativo, {name} — é rapidinho. 📄'
    ),
    'candidate.rejected': _entry(
        '{name}, seu cadastro de colaborador não foi aprovado neste momento. Fale '
        'com o coordenador do seu polo para entender os próximos passos, {name}.'
    ),
    'candidate.selfie_approved': _entry(
        'Aprovado, {name}! ✅ Sua selfie foi confirmada e o cadastro segue em '
        'frente. Continue o preenchimento, {name}.'
    ),
    'candidate.selfie_in_review': _entry(
        '{name}, a selfie de um candidato precisa da sua análise — a IA ficou em '
        'dúvida. Aprove ou reprove no painel, {name}.'
    ),
    'candidate.selfie_rejected': _entry(
        '{name}, sua selfie não pôde ser confirmada. Envie uma nova foto, nítida e '
        'mostrando o rosto, {name}.'
    ),
    'enrollment.address_proof.approved': _entry(
        'Boa, {name}! ✅ Seu comprovante de residência foi aprovado e sua matrícula '
        'segue em frente. Continue o preenchimento, {name}.',
        subject='Comprovante de endereço aprovado',
    ),
    'enrollment.address_proof.new_proof_needed': _entry(
        '{name}, precisamos de outro comprovante de residência — de preferência no '
        'SEU nome (conta de luz, água, internet ou telefone dos últimos 90 dias). '
        'Envie pelo aplicativo, {name}. 🏠',
        subject='Envie um novo comprovante de endereço',
    ),
    'enrollment.address_proof_rejected': _entry(
        '{name}, o comprovante de endereço precisa de ajuste: {detail} Reenvie pelo '
        'app, {name}. 📄',
        subject='Seu comprovante de endereço precisa de ajuste',
    ),
    'enrollment.awaiting_release': _entry(
        '{name}, uma matrícula concluiu o envio de dados e aguarda a sua liberação '
        'no painel. Confira quando puder, {name}.'
    ),
    'enrollment.concluded_referral': _entry(
        '{name}, um aluno que você indicou acabou de virar aluno. ✅ Bônus '
        'creditado. Continue indicando! 🚀'
    ),
    'enrollment.credentials': _entry(
        '{name}, aqui estão seus dados de acesso à plataforma de estudos:\n\n🔗 {link}\n'
        '👤 Login: {login}\n🔑 Senha: {password}\n\nGuarde com você, {name} — é por aqui '
        'que você entra nas suas aulas. Bons estudos! 📚'
    ),
    'enrollment.fee_due_paid': _entry(
        '{name}, a 2ª parcela da taxa de {student_name} ({valor}) foi PAGA no '
        'vencimento. ✅ Taxa quitada, {name} — nada mais a fazer.'
    ),
    'enrollment.fee_paid': _entry(
        '{name}, a 1ª parcela da taxa de {student_name} foi PAGA ({valor}). ✅ A '
        'instituição já pode liberar o login e a senha — conclua a matrícula no '
        'painel, {name}.'
    ),
    'enrollment.fee_problem': _entry(
        '{name}, deu problema na taxa de {student_name}: {detail} Confira no '
        'painel, {name}, e tente de novo se for o caso.'
    ),
    'enrollment.fee_scheduled': _entry(
        '{name}, a 2ª parcela da taxa de {student_name} ({valor}) foi agendada para '
        '{due_date}. O pagamento sai sozinho no vencimento, {name}.'
    ),
    'enrollment.released': _entry(
        '{name}, é oficial: você é nosso aluno! 💚 Sua matrícula foi liberada. Seja '
        'muito bem-vindo(a), {name} — a sua escola estava esperando por você.'
    ),
    'enrollment.rg_approved': _entry(
        'Tudo certo, {name}! ✅ Seu RG foi aprovado e sua matrícula segue em frente. '
        'Continue o preenchimento, {name}.'
    ),
    'enrollment.rg_in_review': _entry(
        '{name}, o RG de uma matrícula precisa da sua análise: {detail} Aprove ou '
        'reprove no painel, {name}.'
    ),
    'enrollment.rg_rejected': _entry(
        '{name}, precisamos de uma nova foto do seu RG: {detail} Reenvie pelo '
        'aplicativo, {name} — é rapidinho. 📄'
    ),
    'enrollment.selfie_approved': _entry(
        '{name}, sua matrícula está assinada. ✍️ E quem assinou foi você, com o seu '
        'próprio rosto — ninguém fez isso por você. Esse passo é seu pra sempre, '
        '{name}. Agora é com a gente: assim que estiver tudo conferido, a gente te '
        'avisa por aqui.',
        is_tts=True,
        storytelling=True,
        story_prompt=(
            'Você escreve para {name}, um(a) aluno(a) adulto(a) da educação de jovens e '
            'adultos (EJA), público simples e batalhador, que acabou de ASSINAR a '
            'matrícula com a própria selfie. Hoje é {data_hoje} — pode citar a data '
            'como o dia em que ele(a) deu esse passo. {faixa_etaria} Escreva uma '
            'mensagem calorosa e curta (no máximo 3 frases) celebrando que foi ELE(A) '
            'quem assinou, com o próprio rosto, e que agora é só aguardar a liberação. '
            "Trate por '{name}'. Português impecável, sem erros, sem gírias, sem emoji, "
            'sem inventar outros fatos.'
        ),
    ),
    'enrollment.selfie_in_review': _entry(
        '{name}, a selfie de uma matrícula precisa da sua análise — a IA ficou em '
        'dúvida. Aprove ou reprove no painel, {name}.'
    ),
    'enrollment.selfie_rejected': _entry(
        '{name}, sua selfie não pôde ser confirmada. Envie uma nova foto pelo '
        'aplicativo, nítida e mostrando bem o rosto, {name}.'
    ),
    'finance.commission_paid': _entry(
        'Olá, {nome}! Sua comissão foi paga. Enviamos o PIX de R$ {valor} referente '
        'ao fechamento da sua semana. O valor deve cair na sua conta em instantes.',
        title='Comissão paga',
        channels='whatsapp',
    ),
    'hub.coordinator_assigned': _entry(
        'Parabéns, {name}! Você agora é COORDENADOR de um polo. {name}, acompanhe '
        'as matrículas e libere os alunos pelo painel.'
    ),
    'lead.captured': _entry(
        'Olá, {name}! 🎉 Que bom ter você com a gente. Seu cadastro está pronto, '
        '{name} — falta só um passo pra garantir sua vaga: concluir o pagamento. Em '
        'instantes envio o link. Bora juntos nessa jornada!'
    ),
    'lead.captured.promoter': _entry(
        'Boa notícia, {name}! {lead_name} acaba de entrar na sua rede pela sua '
        'indicação. Incentive a concluir o pagamento, {name}. 👊'
    ),
    'lead.checkout.card': _entry(
        '{name}, para concluir sua matrícula pague {valor} no cartão:\n{link}\n\n'
        'Qualquer dúvida é só chamar, {name}.'
    ),
    'lead.checkout.pix': _entry(
        '{name}, para concluir sua matrícula pague o PIX de {valor}:\n{link}\n\nOu use '
        'o PIX copia-e-cola, {name}:\n{payload}'
    ),
    'lead.paid': _entry(
        'Parabéns, {name}! 🎉 Seu pagamento foi confirmado e sua matrícula começou. '
        'Você deu um passo importante, {name} — em breve enviamos os próximos '
        'passos.',
        is_tts=True,
    ),
    'lead.paid.coordinator': _entry(
        '{name}, uma nova matrícula entrou no seu polo. Acompanhe quando o aluno '
        'preencher os dados, {name}.'
    ),
    'lead.paid.promoter': _entry(
        '{name}, seu indicado pagou a matrícula! ✅ Sua comissão entra no fechamento '
        'de sexta, {name}. 💸'
    ),
    'lead.paid.receipt': _entry(
        '{name}, aqui está o comprovante do seu pagamento de {valor}:\n{link}\nGuarde '
        'para referência, {name}.'
    ),
    'lead.payment_reminder': _entry(
        'Olá, {nome}! Sua matrícula ainda aguarda pagamento. Acesse: {link}\n\nSe já '
        'pagou, ignore esta mensagem; a confirmação é automática.',
        title='Sua matrícula está quase lá',
        channels='whatsapp',
    ),
    'promoter.lead_invite': _entry(
        'Você recebeu um convite para conhecer o Supletivo V7M.\n\nAcesse com '
        'segurança: {link}\n\nVocê confirma seus próprios dados antes da matrícula.',
        channels='whatsapp',
    ),
    'promoter.reactivated': _entry(
        'Que bom te ver de volta, {name}! Sua atuação como promotor foi reativada. '
        '{name}, seu link de captação está ativo de novo — bora!'
    ),
    'promoter.suspended': _entry(
        '{name}, sua atuação como promotor foi temporariamente suspensa pelo '
        'coordenador do polo. Fale com o coordenador para regularizar, {name}.'
    ),
    'student.diploma_issued': _entry(
        '{name}, chegou o grande dia: o seu diploma está pronto! 🎓 Você terminou os '
        'seus estudos — o que um dia ficou para trás, hoje você concluiu. E isso é '
        'seu para sempre, {name}. Parabéns! A gente tem muito orgulho de você.',
        is_tts=True,
        storytelling=True,
        story_prompt=(
            'Você escreve para {name}, um(a) aluno(a) adulto(a) da EJA, público simples '
            'e batalhador, que ACABOU de ter o diploma emitido — muitas vezes um sonho '
            'adiado por décadas. Hoje é {data_hoje} — pode citar a data como o dia em '
            'que ele(a) concluiu. {faixa_etaria} Escreva uma mensagem curta (no máximo '
            '3 frases), emocionante e digna, dizendo que terminou os estudos e que isso '
            "é dele(a) para sempre. Trate por '{name}'. NÃO fale de retirada nem "
            'logística. Português impecável, sem erros, sem gírias, sem emoji, sem '
            'inventar outros fatos.'
        ),
    ),
    'student.diploma_pickup': _entry(
        'Para retirar o seu diploma, {name}, é só procurar o coordenador do seu '
        'polo. Ele já está esperando por você, {name}.'
    ),
    'student.document_in_review': _entry(
        '{name}, um documento de aluno ({doc_type}) precisa da sua análise — a IA '
        'ficou em dúvida. Aprove ou reprove no painel, {name}.'
    ),
    'student.document_rejected': _entry(
        '{name}, seu documento ({doc_type}) precisa ser reenviado. Envie uma nova '
        'foto, nítida e legível, {name}.{reason_text}'
    ),
    'student.exam_failed': _entry(
        '{name}, você não atingiu a nota desta vez — mas não desanime. Reagende '
        'para uma nova tentativa, {name}, você consegue!'
    ),
    'student.exam_passed': _entry(
        'Você foi APROVADO na prova, {name}! 🎉 Estamos finalizando a sua '
        'documentação, {name}. Falta pouco!'
    ),
    'student.exam_released': _entry(
        '{name}, seus documentos foram aprovados! Você já pode agendar a sua prova '
        'quando quiser, {name}.'
    ),
    'student.exam_scheduled': _entry(
        '{name}, um aluno do seu polo agendou a prova e aguarda a sua correção. '
        'Confira no painel, {name}.'
    ),
    'student.pendency_opened': _entry(
        '{name}, há uma pendência na sua matrícula: {detail}. Resolva para seguir '
        'com a emissão do diploma, {name}.'
    ),
    'student.veteran': _entry(
        '{name}, agora você é veterano da nossa escola. 💚 Você chegou até o fim — e '
        'quem chega ao fim inspira quem ainda está começando. Bem-vindo ao time, '
        '{name}!'
    ),
    'student.veteran.coordinator': _entry(
        '{name}, um aluno do seu polo se formou e foi diplomado. ✅ Sua comissão '
        'entra no próximo fechamento, {name}. 💸'
    ),
    'training.approved': _entry(
        'Parabéns, {name}! 🎉 Você foi aprovado e agora é PROMOTOR. {name}, seu link '
        'de captação já está ativo — comece a indicar e a ganhar!',
        is_tts=True,
    ),
    'training.cleared': _entry(
        'Treinamento concluído, {name}! 🎉 Seu painel está liberado e seu link de '
        'captação ativo. Agora é com você, {name} — comece a indicar e a ganhar!',
        is_tts=True,
    ),
    'training.must_train': _entry(
        'Parabéns, {name}! Você foi aprovado e agora é PROMOTOR. Antes de liberar '
        'seu painel, {name}, conclua o treinamento obrigatório no aplicativo — '
        'assim que terminar, tudo é liberado.'
    ),
    'training.new_material': _entry(
        '{name}, há um novo treinamento obrigatório no aplicativo. Conclua a '
        'atividade para continuar usando o painel, {name}.'
    ),
    'training.submission_rejected': _entry(
        '{name}, sua resposta precisa de ajuste: {detail} Refaça pelo app quando '
        'puder. 📝',
        subject='Atividade de treinamento precisa de ajuste',
    ),
    'staff.digest': _entry(
        '{message}',
        subject='Resumo financeiro — Supletivo Brasil',
        channels='whatsapp',
    ),
    'tools.adhoc': _entry(
        '{message}',
        subject='{subject}',
    ),
}
"""Rotas do funil web — UMA rota por passo (regra permanente do protótipo, DOCUMENTACAO A2)."""

from django.urls import path

from web import views
from web.assets import asset

app_name = "web"

urlpatterns = [
    path("", views.entry, name="entry"),
    path("asset/<name>", asset, name="asset"),
    path("sair", views.logout, name="logout"),
    # funil de entrada (conta)
    path("verificar", views.check_page, name="check"),
    path("verificar/enviar", views.check_submit, name="check_submit"),
    path("login", views.otp_page, name="otp"),
    path("login/enviar", views.otp_submit, name="otp_submit"),
    path("login/reenviar", views.otp_resend, name="otp_resend"),
    path("cpf", views.cpf_page, name="cpf"),
    path("cpf/enviar", views.cpf_submit, name="cpf_submit"),
    path("email", views.email_page, name="email"),
    path("email/enviar", views.email_submit, name="email_submit"),
    # wizard do candidato
    path("cadastro/endereco", views.address_page, name="address"),
    path("cadastro/endereco/status", views.address_status, name="address_status"),
    path("cadastro/endereco/dados", views.address_data, name="address_data"),
    path("cadastro/endereco/comprovante", views.address_proof, name="address_proof"),
    path("cadastro/documento", views.document_page, name="document"),
    path(
        "cadastro/documento/checar", views.document_classify, name="document_classify"
    ),
    path("cadastro/documento/foto/<slot>", views.document_photo, name="document_photo"),
    path("cadastro/documento/status", views.document_status, name="document_status"),
    path("cadastro/documento/campos", views.document_fields, name="document_fields"),
    path("cadastro/pix", views.pix_page, name="pix"),
    path("cadastro/pix/enviar", views.pix_submit, name="pix_submit"),
    path("cidades", views.cidades, name="cidades"),
    path("cadastro/escolaridade", views.education_page, name="education"),
    path(
        "cadastro/escolaridade/enviar", views.education_submit, name="education_submit"
    ),
    path("cadastro/selfie", views.selfie_page, name="selfie"),
    path("cadastro/selfie/enviar", views.selfie_submit, name="selfie_submit"),
    path("cadastro/selfie/status", views.selfie_status, name="selfie_status"),
    # pós-funil
    path("analise", views.analysis_page, name="analysis"),
    path("analise/status", views.analysis_status, name="analysis_status"),
    # comprovante no nome de outra pessoa: trava aqui até a pessoa dizer quem é
    path("cadastro/endereco/parentesco", views.kinship_page, name="kinship"),
    path(
        "cadastro/endereco/parentesco/enviar",
        views.kinship_submit,
        name="kinship_submit",
    ),
    path("treino", views.training_page, name="training"),
    path("treino/resposta", views.training_submit, name="training_submit"),
    path("treino/audio", views.training_audio, name="training_audio"),
    path("treino/status", views.training_status, name="training_status"),
    # painel do promotor — uma rota por tela (contrato A2/F1)
    path("painel", views.panel_page, name="panel"),
    path("painel/financas", views.panel_finance, name="panel_finance"),
    path("painel/financas/ciclo/<cycle_id>", views.panel_cycle, name="panel_cycle"),
    path("painel/indicacoes", views.panel_referrals, name="panel_referrals"),
    path("painel/matricular", views.panel_enroll, name="panel_enroll"),
    path("painel/convidar", views.panel_invite, name="panel_invite"),
    path("painel/bate-papo", views.panel_chat, name="panel_chat"),
    path("painel/bate-papo/enviar", views.panel_chat_send, name="panel_chat_send"),
    path("painel/dados", views.panel_personal_data, name="panel_data"),
    path("painel/dados/<key>", views.panel_file, name="panel_file"),
]

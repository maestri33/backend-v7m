"""Cliente de envio de email via SMTP STARTTLS — porte do micro legado (notify/mail).

Uma via só: SMTP STARTTLS na porta 587, autenticado (settings.MAIL_SMTP_*). O próprio servidor
(mail.v7m.org / Mailcow) aplica DKIM/SPF/DMARC na submissão autenticada — por isso não precisamos
do caminho SSH+sendmail do legado.

Regras (CONVENTION §8/§10):
 - host/port/user/senha/from vêm do .env via settings. Zero regra de negócio aqui.
 - smtplib é bloqueante → roda em asyncio.to_thread; a API pública é async (consumo in-process pelo
   futuro notify, que é async).
 - qualquer falha de SMTP vira MailError; quem chama (notify, async) decide.
"""

from __future__ import annotations

import asyncio
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import structlog
from django.conf import settings

logger = structlog.get_logger()


class MailError(Exception):
    """Falha no envio SMTP (auth, conexão, timeout, destinatário recusado).

    recipients_refused: quando o servidor recusa um ou mais destinatários
    (SMTPRecipientsRefused), traz {email: (code, msg)}.
    """

    def __init__(self, message: str, *, recipients_refused: dict | None = None):
        self.recipients_refused = recipients_refused or {}
        super().__init__(message)


class MailClient:
    """Envio de email via SMTP STARTTLS:587 autenticado."""

    def __init__(
        self, *, timeout: float | None = None, from_name: str | None = None
    ) -> None:
        self._host = settings.MAIL_SMTP_HOST
        self._port = settings.MAIL_SMTP_PORT
        self._user = settings.MAIL_SMTP_USER
        self._password = settings.MAIL_SMTP_PASSWORD
        self._from_email = settings.MAIL_FROM_EMAIL or settings.MAIL_SMTP_USER
        self._from_name = from_name or settings.MAIL_FROM_NAME
        self._timeout = timeout if timeout is not None else settings.MAIL_TIMEOUT

    @property
    def from_header(self) -> str:
        """Remetente exibido, ex.: 'Supletivo Brasil <noreply@v7m.org>'."""
        return f"{self._from_name} <{self._from_email}>"

    async def send_email(
        self,
        to_email: str,
        subject: str,
        *,
        html_body: str,
        plain_body: str | None = None,
    ) -> dict[str, Any]:
        """Envia email HTML (com fallback plain-text opcional).

        Retorna {to, subject, from, refused}. Qualquer falha de SMTP → MailError.
        """
        msg = MIMEMultipart("alternative")
        msg["To"] = to_email
        msg["Subject"] = subject
        msg["From"] = self.from_header
        if plain_body:
            msg.attach(MIMEText(plain_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        refused = await asyncio.to_thread(self._send_sync, msg, to_email)
        logger.info(
            "mail.sent", to=to_email, subject=subject[:80], refused=bool(refused)
        )
        return {
            "to": to_email,
            "subject": subject,
            "from": self.from_header,
            "refused": refused,
        }

    def _send_sync(self, msg: MIMEMultipart, to_email: str) -> dict:
        """Bloqueante (roda em thread): conecta, STARTTLS, login, envia."""
        try:
            with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as srv:
                srv.starttls()
                srv.login(self._user, self._password)
                srv.send_message(msg)
        except smtplib.SMTPRecipientsRefused as exc:
            logger.warning("mail.recipients_refused", to=to_email)
            raise MailError(
                f"destinatário recusado: {to_email}", recipients_refused=exc.recipients
            ) from exc
        except smtplib.SMTPException as exc:
            raise MailError(f"SMTP falhou: {type(exc).__name__}: {exc}") from exc
        except OSError as exc:  # conexão/timeout/DNS
            raise MailError(
                f"conexão SMTP falhou: {type(exc).__name__}: {exc}"
            ) from exc
        return {}

    async def verify_login(self) -> None:
        """Conecta + STARTTLS + login SEM enviar (prova auth/conectividade — §8).

        Usada pelo command mail_health. Falha → MailError.
        """
        await asyncio.to_thread(self._verify_login_sync)
        logger.info("mail.login_ok", host=self._host, port=self._port, user=self._user)

    def _verify_login_sync(self) -> None:
        try:
            with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as srv:
                srv.starttls()
                srv.login(self._user, self._password)
                srv.noop()
        except smtplib.SMTPException as exc:
            raise MailError(f"login SMTP falhou: {type(exc).__name__}: {exc}") from exc
        except OSError as exc:
            raise MailError(
                f"conexão SMTP falhou: {type(exc).__name__}: {exc}"
            ) from exc


def get_client(
    *, timeout: float | None = None, from_name: str | None = None
) -> MailClient:
    """Constrói o client com host/user/senha/from do .env (config via settings — §10)."""
    return MailClient(timeout=timeout, from_name=from_name)

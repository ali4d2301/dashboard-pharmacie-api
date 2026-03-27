from __future__ import annotations

import mimetypes
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Iterable

from settings import settings


@dataclass(frozen=True)
class EmailAttachment:
    filename: str
    content: bytes
    content_type: str = "text/csv"


def _is_valid_email(value: str) -> bool:
    parsed = parseaddr(value)[1]
    return bool(parsed and "@" in parsed)


def normalize_recipients(recipients: Iterable[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()

    for raw in recipients:
        email = (raw or "").strip()
        if not email:
            continue
        if not _is_valid_email(email):
            raise ValueError(f"Adresse e-mail invalide: {email}")
        lower_email = email.lower()
        if lower_email in seen:
            continue
        seen.add(lower_email)
        unique.append(email)

    return unique


def ensure_smtp_is_configured() -> None:
    missing = []
    if not settings.SMTP_HOST:
        missing.append("SMTP_HOST")
    if not settings.SMTP_FROM:
        missing.append("SMTP_FROM")
    if settings.SMTP_USERNAME and not settings.SMTP_PASSWORD:
        missing.append("SMTP_PASSWORD")

    if missing:
        raise RuntimeError(f"Configuration SMTP incomplete: {', '.join(missing)}")

    if settings.SMTP_USE_TLS and settings.SMTP_USE_SSL:
        raise RuntimeError("SMTP_USE_TLS et SMTP_USE_SSL ne peuvent pas etre actives en meme temps.")


def send_email(
    *,
    subject: str,
    html_body: str,
    recipients: list[str],
    attachments: list[EmailAttachment] | None = None,
) -> None:
    ensure_smtp_is_configured()

    normalized_recipients = normalize_recipients(recipients)
    if not normalized_recipients:
        raise ValueError("Aucun destinataire valide n'a ete fourni.")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.SMTP_FROM
    message["To"] = ", ".join(normalized_recipients)
    message.set_content(
        f"{subject}\n\n"
        "Consultez la version HTML pour afficher le détail complet du rapport."
    )
    message.add_alternative(html_body, subtype="html")

    for attachment in attachments or []:
        maintype, subtype = (attachment.content_type.split("/", maxsplit=1) + ["octet-stream"])[:2]
        if "/" not in attachment.content_type:
            guessed_type, _ = mimetypes.guess_type(attachment.filename)
            maintype, subtype = (guessed_type or "application/octet-stream").split("/", maxsplit=1)

        message.add_attachment(
            attachment.content,
            maintype=maintype,
            subtype=subtype,
            filename=attachment.filename,
        )

    if settings.SMTP_USE_SSL:
        with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=30) as smtp:
            if settings.SMTP_USERNAME:
                smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD or "")
            smtp.send_message(message)
        return

    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=30) as smtp:
        if settings.SMTP_USE_TLS:
            smtp.starttls()
        if settings.SMTP_USERNAME:
            smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD or "")
        smtp.send_message(message)

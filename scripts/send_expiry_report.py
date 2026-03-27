from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from db import SessionLocal
from mailer import EmailAttachment, send_email
from reporting import build_expiry_report, build_expiry_report_csv, get_default_expiry_report_recipients
from settings import settings


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


def main() -> int:
    parser = argparse.ArgumentParser(description="Envoie le rapport des produits a perimer par e-mail.")
    parser.add_argument("--date-from", help="Date de debut au format YYYY-MM-DD")
    parser.add_argument("--date-to", help="Date de fin au format YYYY-MM-DD")
    parser.add_argument(
        "--recipients",
        help="Liste d'e-mails separes par des virgules. Par defaut: EXPIRY_REPORT_RECIPIENTS",
    )
    parser.add_argument("--dry-run", action="store_true", help="Genere le rapport sans l'envoyer")
    parser.add_argument(
        "--no-csv",
        action="store_true",
        help="N'ajoute pas la piece jointe CSV, meme si EXPIRY_REPORT_ATTACH_CSV est active",
    )
    args = parser.parse_args()

    recipients = (
        [email.strip() for email in args.recipients.split(",") if email.strip()]
        if args.recipients
        else get_default_expiry_report_recipients()
    )

    try:
        with SessionLocal() as db:
            report = build_expiry_report(
                db,
                date_from=_parse_date(args.date_from),
                date_to=_parse_date(args.date_to),
            )
    except ValueError as exc:
        print(f"Erreur: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Erreur pendant la generation du rapport: {exc}", file=sys.stderr)
        return 1

    attachments: list[EmailAttachment] = []
    if settings.EXPIRY_REPORT_ATTACH_CSV and not args.no_csv:
        filename, content = build_expiry_report_csv(
            report["rows"],
            date_from=report["date_from"],
            date_to=report["date_to"],
        )
        attachments.append(EmailAttachment(filename=filename, content=content))

    print(f"Periode: {report['date_from']} -> {report['date_to']}")
    print(f"Resume: {report['summary']}")
    print(f"Sujet: {report['subject']}")

    if args.dry_run:
        print("Mode dry-run: aucun e-mail envoye.")
        return 0

    if not recipients:
        print("Erreur: aucun destinataire configure.", file=sys.stderr)
        return 1

    try:
        send_email(
            subject=report["subject"],
            html_body=report["html"],
            recipients=recipients,
            attachments=attachments,
        )
    except Exception as exc:
        print(f"Erreur pendant l'envoi: {exc}", file=sys.stderr)
        return 1

    print(f"E-mail envoye a: {', '.join(recipients)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

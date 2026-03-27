from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from settings import settings
from db import get_db
from deps_auth import require_role
from mailer import EmailAttachment, send_email
from reporting import (
    build_expiry_report,
    build_expiry_report_csv,
    build_weekly_report,
    build_weekly_report_csv,
    get_default_expiry_report_recipients,
    get_default_report_recipients,
)

router = APIRouter(
    prefix="/api/reports",
    tags=["reports"],
    dependencies=[Depends(require_role("admin"))],
)


class WeeklyReportRequest(BaseModel):
    date_from: date | None = None
    date_to: date | None = None
    recipients: list[str] = Field(default_factory=list)
    include_details_csv: bool = settings.WEEKLY_REPORT_ATTACH_CSV
    dry_run: bool = False


class ExpiryReportRequest(BaseModel):
    date_from: date | None = None
    date_to: date | None = None
    recipients: list[str] = Field(default_factory=list)
    include_details_csv: bool = settings.EXPIRY_REPORT_ATTACH_CSV
    dry_run: bool = False


@router.post("/weekly/send")
def send_weekly_report(payload: WeeklyReportRequest, db: Session = Depends(get_db)):
    try:
        report = build_weekly_report(db, date_from=payload.date_from, date_to=payload.date_to)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    recipients = payload.recipients or get_default_report_recipients()
    attachments: list[EmailAttachment] = []

    if payload.include_details_csv:
        filename, content = build_weekly_report_csv(
            report["rows"],
            date_from=report["date_from"],
            date_to=report["date_to"],
        )
        attachments.append(EmailAttachment(filename=filename, content=content))

    if not payload.dry_run:
        if not recipients:
            raise HTTPException(
                status_code=400,
                detail="Aucun destinataire configure. Renseignez `recipients` ou WEEKLY_REPORT_RECIPIENTS.",
            )
        try:
            send_email(
                subject=report["subject"],
                html_body=report["html"],
                recipients=recipients,
                attachments=attachments,
            )
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail="Erreur lors de l'envoi du rapport.") from exc

    return {
        "ok": True,
        "dry_run": payload.dry_run,
        "sent": not payload.dry_run,
        "recipients": recipients,
        "period": {
            "date_from": report["date_from"],
            "date_to": report["date_to"],
        },
        "summary": report["summary"],
        "csv_attached": payload.include_details_csv,
        "subject": report["subject"],
    }


@router.post("/expiries/send")
def send_expiry_report(payload: ExpiryReportRequest, db: Session = Depends(get_db)):
    try:
        report = build_expiry_report(db, date_from=payload.date_from, date_to=payload.date_to)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    recipients = payload.recipients or get_default_expiry_report_recipients()
    attachments: list[EmailAttachment] = []

    if payload.include_details_csv:
        filename, content = build_expiry_report_csv(
            report["rows"],
            date_from=report["date_from"],
            date_to=report["date_to"],
        )
        attachments.append(EmailAttachment(filename=filename, content=content))

    if not payload.dry_run:
        if not recipients:
            raise HTTPException(
                status_code=400,
                detail="Aucun destinataire configure. Renseignez `recipients` ou EXPIRY_REPORT_RECIPIENTS.",
            )
        try:
            send_email(
                subject=report["subject"],
                html_body=report["html"],
                recipients=recipients,
                attachments=attachments,
            )
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail="Erreur lors de l'envoi du rapport.") from exc

    return {
        "ok": True,
        "dry_run": payload.dry_run,
        "sent": not payload.dry_run,
        "recipients": recipients,
        "period": {
            "date_from": report["date_from"],
            "date_to": report["date_to"],
        },
        "summary": report["summary"],
        "csv_attached": payload.include_details_csv,
        "subject": report["subject"],
    }

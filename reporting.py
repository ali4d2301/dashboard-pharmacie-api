from __future__ import annotations

import csv
import html
import io
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session

from movement_recalc import get_lot_stock_values_for_movements
from settings import settings

SQL_WEEKLY_MOVEMENTS = text(
    """
    SELECT
        id_mvt_source,
        date_mvt,
        nom_produit,
        code_produit AS code_prod,
        numero_lot,
        date_peremption,
        forme,
        dosage,
        classe,
        unite,
        type_mouvement,
        mouvement,
        quantite,
        stock_apres,
        commentaire
    FROM tb_dashboard
    WHERE date_mvt BETWEEN :date_from AND :date_to
    ORDER BY date_mvt DESC, id_mvt_source DESC
    """
)

SQL_UPCOMING_EXPIRIES = text(
    """
    SELECT
        pl.date_peremption,
        pl.numero_lot,
        pl.stock_lot,
        p.code,
        p.produit,
        p.forme,
        p.dosage,
        p.classe,
        p.cible,
        p.unite
    FROM `product_lots` pl
    JOIN `0_products` p
        ON p.code = pl.code_prod
    WHERE pl.date_peremption BETWEEN :date_from AND :date_to
      AND COALESCE(pl.stock_lot, 0) > 0
      AND p.statut = 'Actif'
    ORDER BY pl.date_peremption ASC, p.produit ASC, pl.numero_lot ASC
    """
)

TYPE_LABELS = {
    "entree": "Entrée",
    "sortie": "Sortie",
}

MOUVEMENT_LABELS = {
    "acquision": "Acquisition",
    "achat": "Acquisition",
    "don": "Acquisition",
    "ajustement positif": "Ajustement positif",
    "dispensation": "Dispensation",
    "vente": "Dispensation",
    "perte": "Perte",
    "peremption": "Péremption",
    "ajustement negatif": "Ajustement négatif",
}


def _parse_csv_list(raw: str) -> list[str]:
    return [item.strip() for item in (raw or "").split(",") if item.strip()]


def format_date_fr(value: object) -> str:
    if value in (None, ""):
        return ""

    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y")

    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")

    text_value = str(value).strip()
    if not text_value:
        return ""

    for parser in (datetime.fromisoformat, date.fromisoformat):
        try:
            parsed = parser(text_value[:19])
            if isinstance(parsed, datetime):
                return parsed.strftime("%d/%m/%Y")
            return parsed.strftime("%d/%m/%Y")
        except ValueError:
            continue

    return text_value


def format_type_label(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return TYPE_LABELS.get(raw.lower(), raw)


def format_movement_label(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return MOUVEMENT_LABELS.get(raw.lower(), raw)


def resolve_report_period(
    date_from: date | None = None,
    date_to: date | None = None,
) -> tuple[date, date]:
    if (date_from is None) != (date_to is None):
        raise ValueError("date_from et date_to doivent être fournis ensemble.")

    if date_from and date_to:
        if date_from > date_to:
            raise ValueError("date_from doit être inférieure ou égale à date_to.")
        return date_from, date_to

    tz = ZoneInfo(settings.WEEKLY_REPORT_TIMEZONE)
    today = datetime.now(tz).date()
    week_start = today - timedelta(days=today.weekday())

    if today.weekday() >= 4:
        week_end = week_start + timedelta(days=4)
    else:
        week_end = today

    return week_start, week_end


def fetch_weekly_movements(db: Session, date_from: date, date_to: date) -> list[dict]:
    rows = db.execute(
        SQL_WEEKLY_MOVEMENTS,
        {
            "date_from": date_from,
            "date_to": date_to,
        },
    ).mappings().all()

    items = [dict(row) for row in rows]
    stock_map = get_lot_stock_values_for_movements(
        [item.get("id_mvt_source") for item in items],
        db,
    )

    for item in items:
        movement_id = item.get("id_mvt_source")
        lot_stock = stock_map.get(int(movement_id)) if movement_id is not None else None
        if lot_stock is None:
            item["stock_initial"] = None
            item["stock_apres"] = None
            continue
        item["stock_initial"] = lot_stock["stock_initial"]
        item["stock_apres"] = lot_stock["stock_apres"]

    return items


def resolve_expiry_report_period(
    date_from: date | None = None,
    date_to: date | None = None,
) -> tuple[date, date]:
    if (date_from is None) != (date_to is None):
        raise ValueError("date_from et date_to doivent être fournis ensemble.")

    if date_from and date_to:
        if date_from > date_to:
            raise ValueError("date_from doit être inférieure ou égale à date_to.")
        return date_from, date_to

    tz = ZoneInfo(settings.EXPIRY_REPORT_TIMEZONE)
    start_date = datetime.now(tz).date()
    end_date = start_date + timedelta(days=6)
    return start_date, end_date


def fetch_upcoming_expiries(db: Session, date_from: date, date_to: date) -> list[dict]:
    rows = db.execute(
        SQL_UPCOMING_EXPIRIES,
        {
            "date_from": date_from,
            "date_to": date_to,
        },
    ).mappings().all()

    items: list[dict] = []
    for row in rows:
        item = dict(row)
        expiry_date = item.get("date_peremption")
        item["days_until_expiry"] = (
            int((expiry_date - date_from).days) if isinstance(expiry_date, date) else None
        )
        items.append(item)
    return items


def build_weekly_report(db: Session, date_from: date | None = None, date_to: date | None = None) -> dict:
    start_date, end_date = resolve_report_period(date_from=date_from, date_to=date_to)
    rows = fetch_weekly_movements(db, start_date, end_date)

    total_movements = len(rows)
    total_entries = 0
    total_outputs = 0
    unique_products: set[str] = set()
    movement_breakdown: dict[str, dict[str, int]] = defaultdict(lambda: {"count": 0, "quantity": 0})
    product_breakdown: dict[str, dict[str, int | str]] = defaultdict(
        lambda: {"product": "", "movements": 0, "entries": 0, "outputs": 0}
    )

    for row in rows:
        product_name = str(row.get("nom_produit") or "Produit inconnu")
        movement_name = format_movement_label(row.get("mouvement")) or "Non renseigné"
        movement_type = str(row.get("type_mouvement") or "").lower()
        quantity = int(row.get("quantite") or 0)

        unique_products.add(product_name)
        movement_breakdown[movement_name]["count"] += 1
        movement_breakdown[movement_name]["quantity"] += quantity

        product_stats = product_breakdown[product_name]
        product_stats["product"] = product_name
        product_stats["movements"] += 1

        if movement_type == "entree":
            total_entries += quantity
            product_stats["entries"] += quantity
        elif movement_type == "sortie":
            total_outputs += quantity
            product_stats["outputs"] += quantity

    sorted_movement_breakdown = sorted(
        (
            {
                "label": label,
                "count": stats["count"],
                "quantity": stats["quantity"],
            }
            for label, stats in movement_breakdown.items()
        ),
        key=lambda item: (-item["count"], -item["quantity"], item["label"]),
    )

    top_products = sorted(
        (
            {
                "product": str(stats["product"]),
                "movements": int(stats["movements"]),
                "entries": int(stats["entries"]),
                "outputs": int(stats["outputs"]),
            }
            for stats in product_breakdown.values()
        ),
        key=lambda item: (-item["movements"], -(item["entries"] + item["outputs"]), item["product"]),
    )[:10]

    summary = {
        "total_movements": total_movements,
        "total_entries": total_entries,
        "total_outputs": total_outputs,
        "unique_products": len(unique_products),
    }

    report = {
        "date_from": start_date,
        "date_to": end_date,
        "rows": rows,
        "summary": summary,
        "movement_breakdown": sorted_movement_breakdown,
        "top_products": top_products,
        "subject": (
            f"{settings.WEEKLY_REPORT_SUBJECT_PREFIX} - Rapport hebdomadaire des mouvements "
            f"({start_date:%d/%m/%Y} au {end_date:%d/%m/%Y})"
        ),
    }

    report["html"] = render_weekly_report_html(report)
    return report


def build_expiry_report(db: Session, date_from: date | None = None, date_to: date | None = None) -> dict:
    start_date, end_date = resolve_expiry_report_period(date_from=date_from, date_to=date_to)
    rows = fetch_upcoming_expiries(db, start_date, end_date)

    unique_products = {str(row.get("produit") or "Produit inconnu") for row in rows}
    total_stock_at_risk = sum(int(row.get("stock_lot") or 0) for row in rows)

    by_day: dict[date, int] = defaultdict(int)
    for row in rows:
        expiry_date = row.get("date_peremption")
        if isinstance(expiry_date, date):
            by_day[expiry_date] += 1

    grouped_by_day = [
        {
            "date_peremption": expiry_date,
            "count": by_day[expiry_date],
        }
        for expiry_date in sorted(by_day)
    ]

    summary = {
        "total_lots": len(rows),
        "unique_products": len(unique_products),
        "total_stock_at_risk": total_stock_at_risk,
    }

    report = {
        "date_from": start_date,
        "date_to": end_date,
        "rows": rows,
        "summary": summary,
        "grouped_by_day": grouped_by_day,
        "subject": (
            f"{settings.EXPIRY_REPORT_SUBJECT_PREFIX} - Produits à péremption proche "
            f"({start_date:%d/%m/%Y} au {end_date:%d/%m/%Y})"
        ),
    }

    report["html"] = render_expiry_report_html(report)
    return report


def render_weekly_report_html(report: dict) -> str:
    summary = report["summary"]
    top_products = report["top_products"]
    movement_breakdown = report["movement_breakdown"]

    summary_cells = [
        ("Mouvements", summary["total_movements"]),
        ("Quantité entrée", summary["total_entries"]),
        ("Quantité sortie", summary["total_outputs"]),
        ("Produits concernés", summary["unique_products"]),
    ]

    cards_html = "".join(
        (
            "<td style='padding:12px;border:1px solid #d7e0ea;border-radius:8px;background:#f7fafc;'>"
            f"<div style='font-size:12px;color:#5b6775;'>{html.escape(label)}</div>"
            f"<div style='font-size:22px;font-weight:700;color:#17212b;'>{value}</div>"
            "</td>"
        )
        for label, value in summary_cells
    )

    movement_rows = "".join(
        (
            "<tr>"
            f"<td style='padding:8px;border:1px solid #d7e0ea;'>{html.escape(item['label'])}</td>"
            f"<td style='padding:8px;border:1px solid #d7e0ea;text-align:right;'>{item['count']}</td>"
            f"<td style='padding:8px;border:1px solid #d7e0ea;text-align:right;'>{item['quantity']}</td>"
            "</tr>"
        )
        for item in movement_breakdown
    ) or (
        "<tr><td colspan='3' style='padding:8px;border:1px solid #d7e0ea;'>"
        "Aucun mouvement sur la période."
        "</td></tr>"
    )

    product_rows = "".join(
        (
            "<tr>"
            f"<td style='padding:8px;border:1px solid #d7e0ea;'>{html.escape(item['product'])}</td>"
            f"<td style='padding:8px;border:1px solid #d7e0ea;text-align:right;'>{item['movements']}</td>"
            f"<td style='padding:8px;border:1px solid #d7e0ea;text-align:right;'>{item['entries']}</td>"
            f"<td style='padding:8px;border:1px solid #d7e0ea;text-align:right;'>{item['outputs']}</td>"
            "</tr>"
        )
        for item in top_products
    ) or (
        "<tr><td colspan='4' style='padding:8px;border:1px solid #d7e0ea;'>"
        "Aucune donnée à afficher."
        "</td></tr>"
    )

    return f"""
    <html>
      <body style="font-family:Segoe UI,Arial,sans-serif;background:#f3f6f9;color:#17212b;padding:24px;">
        <div style="max-width:960px;margin:0 auto;background:#ffffff;border:1px solid #d7e0ea;border-radius:14px;padding:24px;">
          <h2 style="margin:0 0 8px 0;">Rapport hebdomadaire des mouvements</h2>
          <p style="margin:0 0 20px 0;color:#5b6775;">
            Période du {format_date_fr(report['date_from'])} au {format_date_fr(report['date_to'])}
          </p>

          <table role="presentation" cellspacing="0" cellpadding="0" width="100%" style="border-collapse:separate;border-spacing:12px 0;margin-bottom:24px;">
            <tr>{cards_html}</tr>
          </table>

          <h3 style="margin:0 0 12px 0;">Synthèse par type de mouvement</h3>
          <table cellspacing="0" cellpadding="0" width="100%" style="border-collapse:collapse;margin-bottom:24px;">
            <thead>
              <tr style="background:#eef3f8;">
                <th style="padding:8px;border:1px solid #d7e0ea;text-align:left;">Mouvement</th>
                <th style="padding:8px;border:1px solid #d7e0ea;text-align:right;">Nb lignes</th>
                <th style="padding:8px;border:1px solid #d7e0ea;text-align:right;">Quantité</th>
              </tr>
            </thead>
            <tbody>{movement_rows}</tbody>
          </table>

          <h3 style="margin:0 0 12px 0;">Produits les plus mouvementés</h3>
          <table cellspacing="0" cellpadding="0" width="100%" style="border-collapse:collapse;margin-bottom:24px;">
            <thead>
              <tr style="background:#eef3f8;">
                <th style="padding:8px;border:1px solid #d7e0ea;text-align:left;">Produit</th>
                <th style="padding:8px;border:1px solid #d7e0ea;text-align:right;">Nb mouvements</th>
                <th style="padding:8px;border:1px solid #d7e0ea;text-align:right;">Entrées</th>
                <th style="padding:8px;border:1px solid #d7e0ea;text-align:right;">Sorties</th>
              </tr>
            </thead>
            <tbody>{product_rows}</tbody>
          </table>

          <p style="margin:0;color:#5b6775;">
            Le détail complet des mouvements est joint au format CSV.
          </p>
        </div>
      </body>
    </html>
    """.strip()


def render_expiry_report_html(report: dict) -> str:
    summary = report["summary"]
    rows = report["rows"]
    grouped_by_day = report["grouped_by_day"]

    summary_cells = [
        ("Lots concernés", summary["total_lots"]),
        ("Produits concernés", summary["unique_products"]),
        ("Stock à surveiller", summary["total_stock_at_risk"]),
    ]

    cards_html = "".join(
        (
            "<td style='padding:12px;border:1px solid #d7e0ea;border-radius:8px;background:#f7fafc;'>"
            f"<div style='font-size:12px;color:#5b6775;'>{html.escape(label)}</div>"
            f"<div style='font-size:22px;font-weight:700;color:#17212b;'>{value}</div>"
            "</td>"
        )
        for label, value in summary_cells
    )

    grouped_rows = "".join(
        (
            "<tr>"
            f"<td style='padding:8px;border:1px solid #d7e0ea;'>{format_date_fr(item['date_peremption'])}</td>"
            f"<td style='padding:8px;border:1px solid #d7e0ea;text-align:right;'>{item['count']}</td>"
            "</tr>"
        )
        for item in grouped_by_day
    ) or (
        "<tr><td colspan='2' style='padding:8px;border:1px solid #d7e0ea;'>"
        "Aucun lot n'expire sur la période."
        "</td></tr>"
    )

    detail_rows = "".join(
        (
            "<tr>"
            f"<td style='padding:8px;border:1px solid #d7e0ea;'>{format_date_fr(row.get('date_peremption'))}</td>"
            f"<td style='padding:8px;border:1px solid #d7e0ea;'>{html.escape(str(row.get('produit') or ''))}</td>"
            f"<td style='padding:8px;border:1px solid #d7e0ea;'>{html.escape(str(row.get('numero_lot') or ''))}</td>"
            f"<td style='padding:8px;border:1px solid #d7e0ea;text-align:right;'>{int(row.get('stock_lot') or 0)}</td>"
            f"<td style='padding:8px;border:1px solid #d7e0ea;text-align:right;'>{row.get('days_until_expiry')}</td>"
            "</tr>"
        )
        for row in rows[:50]
    ) or (
        "<tr><td colspan='5' style='padding:8px;border:1px solid #d7e0ea;'>"
        "Aucune donnée à afficher."
        "</td></tr>"
    )

    return f"""
    <html>
      <body style="font-family:Segoe UI,Arial,sans-serif;background:#f3f6f9;color:#17212b;padding:24px;">
        <div style="max-width:960px;margin:0 auto;background:#ffffff;border:1px solid #d7e0ea;border-radius:14px;padding:24px;">
          <h2 style="margin:0 0 8px 0;">Produits dont la péremption approche</h2>
          <p style="margin:0 0 20px 0;color:#5b6775;">
            Période du {format_date_fr(report['date_from'])} au {format_date_fr(report['date_to'])}
          </p>

          <table role="presentation" cellspacing="0" cellpadding="0" width="100%" style="border-collapse:separate;border-spacing:12px 0;margin-bottom:24px;">
            <tr>{cards_html}</tr>
          </table>

          <h3 style="margin:0 0 12px 0;">Répartition par jour de péremption</h3>
          <table cellspacing="0" cellpadding="0" width="100%" style="border-collapse:collapse;margin-bottom:24px;">
            <thead>
              <tr style="background:#eef3f8;">
                <th style="padding:8px;border:1px solid #d7e0ea;text-align:left;">Date de péremption</th>
                <th style="padding:8px;border:1px solid #d7e0ea;text-align:right;">Nb lots</th>
              </tr>
            </thead>
            <tbody>{grouped_rows}</tbody>
          </table>

          <h3 style="margin:0 0 12px 0;">Liste détaillée</h3>
          <table cellspacing="0" cellpadding="0" width="100%" style="border-collapse:collapse;margin-bottom:24px;">
            <thead>
              <tr style="background:#eef3f8;">
                <th style="padding:8px;border:1px solid #d7e0ea;text-align:left;">Date</th>
                <th style="padding:8px;border:1px solid #d7e0ea;text-align:left;">Produit</th>
                <th style="padding:8px;border:1px solid #d7e0ea;text-align:left;">Lot</th>
                <th style="padding:8px;border:1px solid #d7e0ea;text-align:right;">Stock lot</th>
                <th style="padding:8px;border:1px solid #d7e0ea;text-align:right;">Jours restants</th>
              </tr>
            </thead>
            <tbody>{detail_rows}</tbody>
          </table>

          <p style="margin:0;color:#5b6775;">
            Le détail complet des lots est joint au format CSV.
          </p>
        </div>
      </body>
    </html>
    """.strip()


def build_weekly_report_csv(
    rows: list[dict],
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> tuple[str, bytes]:
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(
        [
            "Date",
            "Produit",
            "Code",
            "Lot",
            "Péremption",
            "Forme",
            "Dosage",
            "Classe",
            "Unité",
            "Stock lot initial",
            "Type",
            "Mouvement",
            "Qté",
            "Stock lot après",
            "Commentaire",
        ]
    )

    for row in rows:
        writer.writerow(
            [
                format_date_fr(row.get("date_mvt")),
                row.get("nom_produit"),
                row.get("code_prod"),
                row.get("numero_lot"),
                format_date_fr(row.get("date_peremption")),
                row.get("forme"),
                row.get("dosage"),
                row.get("classe"),
                row.get("unite"),
                row.get("stock_initial"),
                format_type_label(row.get("type_mouvement")),
                format_movement_label(row.get("mouvement")),
                row.get("quantite"),
                row.get("stock_apres"),
                row.get("commentaire"),
            ]
        )

    if date_from and date_to:
        filename = f"mouvements_hebdo_{date_from.isoformat()}_{date_to.isoformat()}.csv"
    else:
        filename = f"mouvements_hebdo_{rows[0]['date_mvt'] if rows else 'vide'}.csv"
    return filename, output.getvalue().encode("utf-8-sig")


def build_expiry_report_csv(
    rows: list[dict],
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> tuple[str, bytes]:
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(
        [
            "Date de péremption",
            "Numéro de lot",
            "Stock lot",
            "Code",
            "Produit",
            "Forme",
            "Dosage",
            "Classe",
            "Cible",
            "Unité",
            "Jours restants",
        ]
    )

    for row in rows:
        writer.writerow(
            [
                format_date_fr(row.get("date_peremption")),
                row.get("numero_lot"),
                row.get("stock_lot"),
                row.get("code"),
                row.get("produit"),
                row.get("forme"),
                row.get("dosage"),
                row.get("classe"),
                row.get("cible"),
                row.get("unite"),
                row.get("days_until_expiry"),
            ]
        )

    if date_from and date_to:
        filename = f"peremptions_{date_from.isoformat()}_{date_to.isoformat()}.csv"
    else:
        filename = f"peremptions_{rows[0]['date_peremption'] if rows else 'vide'}.csv"
    return filename, output.getvalue().encode("utf-8-sig")


def get_default_report_recipients() -> list[str]:
    return _parse_csv_list(settings.WEEKLY_REPORT_RECIPIENTS)


def get_default_expiry_report_recipients() -> list[str]:
    return _parse_csv_list(settings.EXPIRY_REPORT_RECIPIENTS)

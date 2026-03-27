from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from db import get_db
from deps_auth import require_role
from movement_recalc import get_lot_stock_values_for_movements

router = APIRouter(
    prefix="/api/dashboard",
    tags=["dashboard"],
    dependencies=[Depends(require_role("admin", "viewer"))],
)

ALLOWED_SORT = {
    "date_mvt",
    "nom_produit",
    "code_prod",
    "numero_lot",
    "date_peremption",
    "forme",
    "dosage",
    "classe",
    "cible",
    "unite",
    "prix_achat",
    "prix_vente",
    "type_mouvement",
    "mouvement",
    "quantite",
    "stock_initial",
    "stock_apres",
    "commentaire",
}


def _build_order_clause(sort_by: str, sort_dir: str) -> str:
    if sort_by in {"stock_initial", "stock_apres"}:
        return "date_mvt DESC, id_mvt_source DESC"
    if sort_by == "date_mvt":
        return f"date_mvt {sort_dir}, id_mvt_source {sort_dir}"
    return f"{sort_by} {sort_dir}, date_mvt DESC, id_mvt_source DESC"


def _sort_by_lot_stock(items: list[dict], sort_by: str, sort_dir: str) -> list[dict]:
    if sort_by not in {"stock_initial", "stock_apres"}:
        return items

    if sort_dir == "desc":
        items.sort(
            key=lambda item: (
                item.get(sort_by) is None,
                -(item.get(sort_by) or 0),
                str(item.get("date_mvt") or ""),
                -int(item.get("id_mvt_source") or 0),
            )
        )
        return items

    items.sort(
        key=lambda item: (
            item.get(sort_by) is None,
            item.get(sort_by) or 0,
            str(item.get("date_mvt") or ""),
            int(item.get("id_mvt_source") or 0),
        )
    )
    return items


@router.get("/movements")
def get_movements(
    date_from: str = Query(..., description="YYYY-MM-DD"),
    date_to: str = Query(..., description="YYYY-MM-DD"),
    q: Optional[str] = Query(None, description="Recherche sur nom_produit ou numero_lot"),
    classe: Optional[str] = Query(None),
    cible: Optional[str] = Query(None),
    sort_by: str = Query("date_mvt"),
    sort_dir: str = Query("desc"),
    limit: int = Query(5000, ge=1, le=20000),
    db: Session = Depends(get_db),
):
    sort_by = sort_by if sort_by in ALLOWED_SORT else "date_mvt"
    sort_dir = "asc" if str(sort_dir).lower() == "asc" else "desc"
    order_clause = _build_order_clause(sort_by, sort_dir)

    sql = f"""
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
        cible,
        unite,
        prix_achat,
        prix_vente,
        type_mouvement,
        mouvement,
        quantite,
        CASE
          WHEN stock_apres IS NULL OR quantite IS NULL THEN NULL
          WHEN type_mouvement = 'entree' THEN stock_apres - quantite
          WHEN type_mouvement = 'sortie' THEN stock_apres + quantite
          ELSE NULL
        END AS stock_initial,
        stock_apres,
        commentaire
      FROM tb_dashboard
      WHERE date_mvt BETWEEN :date_from AND :date_to
        AND (
          :q IS NULL
          OR nom_produit LIKE CONCAT('%', :q, '%')
          OR COALESCE(numero_lot, '') LIKE CONCAT('%', :q, '%')
        )
        AND (:classe IS NULL OR classe = :classe)
        AND (:cible IS NULL OR cible = :cible)
      ORDER BY {order_clause}
      LIMIT :limit
    """

    rows = db.execute(
        text(sql),
        {
            "date_from": date_from,
            "date_to": date_to,
            "q": q,
            "classe": classe if classe not in (None, "", "ALL") else None,
            "cible": cible if cible not in (None, "", "ALL") else None,
            "limit": limit,
        },
    ).mappings().all()

    items = [dict(r) for r in rows]
    stock_map = get_lot_stock_values_for_movements(
        [item.get("id_mvt_source") for item in items],
        db,
    )

    for item in items:
        lot_stock = stock_map.get(int(item["id_mvt_source"])) if item.get("id_mvt_source") is not None else None
        if lot_stock is None:
            item["stock_initial"] = None
            item["stock_apres"] = None
            continue
        item["stock_initial"] = lot_stock["stock_initial"]
        item["stock_apres"] = lot_stock["stock_apres"]

    items = _sort_by_lot_stock(items, sort_by, sort_dir)

    return {
        "items": items,
        "limit": limit,
    }


@router.get("/movements/filters")
def get_movement_filters(
    date_from: str = Query(...),
    date_to: str = Query(...),
    db: Session = Depends(get_db),
):
    classes = db.execute(
        text("""
          SELECT DISTINCT classe
          FROM tb_dashboard
          WHERE date_mvt BETWEEN :date_from AND :date_to
            AND classe IS NOT NULL AND classe <> ''
          ORDER BY classe
        """),
        {"date_from": date_from, "date_to": date_to},
    ).scalars().all()

    cibles = db.execute(
        text("""
          SELECT DISTINCT cible
          FROM tb_dashboard
          WHERE date_mvt BETWEEN :date_from AND :date_to
            AND cible IS NOT NULL AND cible <> ''
          ORDER BY cible
        """),
        {"date_from": date_from, "date_to": date_to},
    ).scalars().all()

    return {"classes": classes, "cibles": cibles}

from fastapi import APIRouter, Query
from sqlalchemy import text
from sqlalchemy.orm import Session
from typing import Optional
from db import get_db
from fastapi import Depends

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

ALLOWED_SORT = {
    "date_mvt", "nom_produit", "forme", "dosage", "classe", "cible", "unite",
    "prix_achat", "prix_vente", "type_mouvement", "mouvement", "quantite",
    "stock_apres", "commentaire"
}

@router.get("/movements")
def get_movements(
    date_from: str = Query(..., description="YYYY-MM-DD"),
    date_to: str = Query(..., description="YYYY-MM-DD"),
    q: Optional[str] = Query(None, description="Recherche sur nom_produit"),
    classe: Optional[str] = Query(None),
    cible: Optional[str] = Query(None),
    sort_by: str = Query("date_mvt"),
    sort_dir: str = Query("desc"),
    limit: int = Query(5000, ge=1, le=20000),
    db: Session = Depends(get_db),
):
    sort_by = sort_by if sort_by in ALLOWED_SORT else "date_mvt"
    sort_dir = "asc" if str(sort_dir).lower() == "asc" else "desc"

    sql = f"""
      SELECT
        date_mvt, nom_produit, forme, dosage, classe, cible, unite, prix_achat,
        prix_vente, type_mouvement, mouvement, quantite, stock_apres, commentaire
      FROM tb_dashboard
      WHERE date_mvt BETWEEN :date_from AND :date_to
        AND (:q IS NULL OR nom_produit LIKE CONCAT('%', :q, '%'))
        AND (:classe IS NULL OR classe = :classe)
        AND (:cible IS NULL OR cible = :cible)
      ORDER BY {sort_by} {sort_dir}
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

    return {
        "items": [dict(r) for r in rows],
        "limit": limit
    }


@router.get("/movements/filters")
def get_movement_filters(
    date_from: str = Query(...),
    date_to: str = Query(...),
    db: Session = Depends(get_db),
):
    # uniquement tb_dashboard
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

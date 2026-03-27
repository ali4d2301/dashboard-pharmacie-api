from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from db import get_db
from deps_auth import require_role

router = APIRouter(
    prefix="/api/dashboard",
    tags=["dashboard"],
    dependencies=[Depends(require_role("admin", "viewer"))],
)


@router.get("/classes")
def get_classes(db: Session = Depends(get_db)):
    sql = text("""
        SELECT DISTINCT classe
        FROM `0_products`
        WHERE classe IS NOT NULL AND classe <> ''
        ORDER BY classe
    """)
    rows = db.execute(sql).mappings().all()
    return {"classes": [r["classe"] for r in rows]}


@router.get("/kpis")
def get_kpis(
    annee: int = Query(..., ge=2000),
    mois: int = Query(..., ge=1, le=12),
    classe: str = Query("Tout"),
    db: Session = Depends(get_db),
):
    classe_norm = "Tout" if (classe or "").strip().upper() == "ALL" else (classe or "Tout").strip()

    sql_rows_period = text("""
        SELECT COUNT(*) AS n
        FROM tb_dashboard d
        WHERE YEAR(d.date_mvt) = :annee
          AND MONTH(d.date_mvt) = :mois
    """)
    rows_period = int(db.execute(sql_rows_period, {"annee": annee, "mois": mois}).scalar() or 0)

    sql_expiring_products = text("""
        SELECT COUNT(DISTINCT pl.code_prod) AS nb
        FROM `product_lots` pl
        JOIN `0_products` p ON p.code = pl.code_prod
        WHERE YEAR(pl.date_peremption) = :annee
          AND MONTH(pl.date_peremption) = :mois
          AND COALESCE(pl.stock_lot, 0) > 0
          AND p.statut = 'Actif'
          AND (:classe = 'Tout' OR p.classe = :classe)
    """)
    nb_produits_perimant = int(
        db.execute(
            sql_expiring_products,
            {"annee": annee, "mois": mois, "classe": classe_norm},
        ).scalar()
        or 0
    )

    sql_products_with_movements = text("""
        SELECT COUNT(DISTINCT d.code_produit) AS nb
        FROM tb_dashboard d
        WHERE YEAR(d.date_mvt) = :annee
          AND MONTH(d.date_mvt) = :mois
          AND (:classe = 'Tout' OR d.classe = :classe)
    """)
    nb_produits_mouvement = int(
        db.execute(
            sql_products_with_movements,
            {"annee": annee, "mois": mois, "classe": classe_norm},
        ).scalar()
        or 0
    )

    sql_denom = text("""
        SELECT COUNT(DISTINCT p.code) AS denom
        FROM `0_products` p
        WHERE p.statut = 'Actif'
          AND (:classe = 'Tout' OR p.classe = :classe)
    """)
    denom = int(db.execute(sql_denom, {"classe": classe_norm}).scalar() or 0)

    sql_num = text("""
        WITH last_mvt AS (
            SELECT d.code_produit, MAX(d.id_mvt_source) AS last_id
            FROM tb_dashboard d
            WHERE YEAR(d.date_mvt) = :annee
              AND MONTH(d.date_mvt) = :mois
              AND (:classe = 'Tout' OR d.classe = :classe)
            GROUP BY d.code_produit
        )
        SELECT COUNT(*) AS num
        FROM last_mvt lm
        JOIN tb_dashboard d ON d.id_mvt_source = lm.last_id
        JOIN `0_products` p ON p.code = lm.code_produit
        WHERE p.statut = 'Actif'
          AND (:classe = 'Tout' OR p.classe = :classe)
          AND COALESCE(d.stock_apres, 0) > 0
    """)
    num = int(db.execute(sql_num, {"annee": annee, "mois": mois, "classe": classe_norm}).scalar() or 0)

    taux_disponibilite = 0.0 if denom == 0 else (num / denom) * 100.0

    return {
        "nb_produits_perimant": nb_produits_perimant,
        "taux_disponibilite": round(taux_disponibilite, 2),
        "nb_produits_mouvement": nb_produits_mouvement,
        "nb_produits_actifs": denom,
        "debug": {
            "annee": annee,
            "mois": mois,
            "classe_recue": classe,
            "classe_norm": classe_norm,
            "rows_period": rows_period,
            "nb_produits_perimant": nb_produits_perimant,
            "nb_produits_mouvement": nb_produits_mouvement,
            "nb_produits_actifs": denom,
            "dispo_num": num,
            "dispo_denom": denom,
        },
    }


def norm_classe(classe: str) -> str:
    c = (classe or "").strip()
    return "Tout" if c.upper() == "ALL" or c == "" else c


def build_month_context(annee: int, mois: int) -> dict[str, str]:
    current_month_start = date(annee, mois, 1)
    if mois == 12:
        next_month_start = date(annee + 1, 1, 1)
    else:
        next_month_start = date(annee, mois + 1, 1)

    if mois == 1:
        previous_year = annee - 1
        previous_month = 12
    else:
        previous_year = annee
        previous_month = mois - 1

    return {
        "ym": f"{annee:04d}-{mois:02d}",
        "ym_prec": f"{previous_year:04d}-{previous_month:02d}",
        "date_from": current_month_start.isoformat(),
        "date_to": next_month_start.isoformat(),
    }


@router.get("/etat_stock_share")
def etat_stock_share(
    annee: int = Query(..., ge=2000),
    mois: int = Query(..., ge=1, le=12),
    classe: str = Query("ALL"),
    db: Session = Depends(get_db),
):
    classe_norm = norm_classe(classe)
    ym = f"{annee:04d}-{mois:02d}"

    sql = text("""
        SELECT
            esm.etat AS etat,
            COUNT(*) AS nb
        FROM etat_stock_mensuel esm
        JOIN `0_products` p ON p.code = esm.code_prod
        WHERE LEFT(CAST(esm.mois AS CHAR), 7) = :ym
          AND p.statut = 'Actif'
          AND (:classe = 'Tout' OR p.classe = :classe)
        GROUP BY esm.etat
        ORDER BY nb DESC
    """)

    rows = db.execute(sql, {"ym": ym, "classe": classe_norm}).mappings().all()

    items = [{"name": r["etat"] or "Non defini", "value": int(r["nb"] or 0)} for r in rows]
    total = sum(i["value"] for i in items)

    return {"ym": ym, "classe": classe_norm, "total": total, "items": items}


@router.get("/movement_hist")
def movement_hist(
    annee: int = Query(..., ge=2000),
    mois: int = Query(..., ge=1, le=12),
    classe: str = Query("ALL"),
    db: Session = Depends(get_db),
):
    classe_norm = norm_classe(classe)

    sql = text("""
        SELECT
            d.mouvement AS mouvement,
            d.type_mouvement AS type_mouvement,
            COUNT(*) AS nb
        FROM tb_dashboard d
        JOIN `0_products` p ON p.code = d.code_produit
        WHERE YEAR(d.date_mvt) = :annee
          AND MONTH(d.date_mvt) = :mois
          AND p.statut = 'Actif'
          AND (:classe = 'Tout' OR p.classe = :classe)
          AND d.mouvement IS NOT NULL AND d.mouvement <> ''
          AND d.type_mouvement IN ('entree', 'sortie')
        GROUP BY d.mouvement, d.type_mouvement
        ORDER BY d.mouvement, d.type_mouvement
    """)

    rows = db.execute(sql, {"annee": annee, "mois": mois, "classe": classe_norm}).mappings().all()

    items = [
        {
            "mouvement": r["mouvement"],
            "type": r["type_mouvement"],
            "value": int(r["nb"] or 0),
        }
        for r in rows
    ]

    return {"items": items}


SQL_TABLEAU_MENSUEL = text("""
WITH movement_totals AS (
  SELECT
    d.code_produit,
    SUM(CASE WHEN d.type_mouvement = 'entree' THEN d.quantite ELSE 0 END) AS qte_entree,
    SUM(CASE WHEN d.type_mouvement = 'sortie' THEN d.quantite ELSE 0 END) AS qte_sortie
  FROM tb_dashboard d
  WHERE d.date_mvt >= :date_from
    AND d.date_mvt < :date_to
  GROUP BY d.code_produit
),
nearest_expiry AS (
  SELECT
    pl.code_prod,
    MIN(pl.date_peremption) AS prochaine_peremption
  FROM product_lots pl
  WHERE pl.stock_lot > 0
  GROUP BY pl.code_prod
),
nearest_expiry_qty AS (
  SELECT
    pl.code_prod,
    pl.date_peremption,
    SUM(pl.stock_lot) AS quantite_prochaine_peremption
  FROM product_lots pl
  WHERE pl.stock_lot > 0
  GROUP BY pl.code_prod, pl.date_peremption
)
SELECT
  p.produit AS produit,
  p.dosage AS dosage,
  p.forme AS forme,
  p.unite AS unite,
  p.cible AS cible,
  prev.stock AS quantite_initiale,
  COALESCE(mv.qte_entree, 0) AS quantite_entree,
  COALESCE(mv.qte_sortie, 0) AS quantite_sortie,
  cur.stock AS sdu,
  cur.cmm AS cmm,
  cur.msd AS msd,
  cur.etat AS etat_stock,
  ne.prochaine_peremption AS prochaine_peremption,
  neq.quantite_prochaine_peremption AS quantite_prochaine_peremption
FROM etat_stock_mensuel cur
JOIN `0_products` p
  ON p.code = cur.code_prod
LEFT JOIN etat_stock_mensuel prev
  ON prev.code_prod = cur.code_prod
 AND prev.mois = :ym_prec
LEFT JOIN movement_totals mv
  ON mv.code_produit = cur.code_prod
LEFT JOIN nearest_expiry ne
  ON ne.code_prod = cur.code_prod
LEFT JOIN nearest_expiry_qty neq
  ON neq.code_prod = cur.code_prod
 AND neq.date_peremption = ne.prochaine_peremption
WHERE cur.mois = :ym
  AND (:classe = 'Tout' OR p.classe = :classe)
ORDER BY p.produit ASC;
""")


@router.get("/tableau_mensuel")
def tableau_mensuel(
    annee: int = Query(..., ge=2000),
    mois: int = Query(..., ge=1, le=12),
    classe: str = Query("ALL"),
    db: Session = Depends(get_db),
):
    classe_norm = norm_classe(classe)
    month_context = build_month_context(annee, mois)

    rows = db.execute(
        SQL_TABLEAU_MENSUEL,
        {
            "classe": classe_norm,
            **month_context,
        },
    ).mappings().all()

    return {"data": [dict(r) for r in rows]}

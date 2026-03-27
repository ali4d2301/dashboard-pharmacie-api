from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from db import get_db
from deps_auth import require_role

router = APIRouter(
    prefix="/api/dashboard",
    tags=["dashboard"],
    dependencies=[Depends(require_role("admin", "viewer"))],
)


@router.get("/list_products")
def list_products(db: Session = Depends(get_db)):
    rows = db.execute(
        text("""
            WITH nearest_expiry AS (
                SELECT
                    code_prod,
                    COUNT(*) AS lots_count,
                    MIN(CASE WHEN stock_lot > 0 THEN date_peremption END) AS prochaine_peremption
                FROM `product_lots`
                GROUP BY code_prod
            ),
            nearest_expiry_qty AS (
                SELECT
                    pl.code_prod,
                    ne.prochaine_peremption,
                    SUM(pl.stock_lot) AS stock_peremption_proche
                FROM `product_lots` pl
                JOIN nearest_expiry ne
                    ON ne.code_prod = pl.code_prod
                   AND ne.prochaine_peremption = pl.date_peremption
                WHERE COALESCE(pl.stock_lot, 0) > 0
                GROUP BY pl.code_prod, ne.prochaine_peremption
            ),
            latest_activity AS (
                SELECT
                    code_produit AS code_prod,
                    MAX(date_mvt) AS date_mise_a_jour
                FROM `tb_dashboard`
                GROUP BY code_produit
            )
            SELECT
                p.code,
                p.produit,
                p.forme,
                p.dosage,
                p.classe,
                p.cible,
                p.unite,
                p.prix_achat,
                p.prix_vente,
                p.stock_actuel,
                COALESCE(ne.lots_count, 0) AS lots_count,
                ne.prochaine_peremption,
                COALESCE(neq.stock_peremption_proche, 0) AS stock_peremption_proche,
                p.statut,
                COALESCE(la.date_mise_a_jour, p.date_creation) AS date_mise_a_jour
            FROM `0_products` p
            LEFT JOIN nearest_expiry ne
                ON ne.code_prod = p.code
            LEFT JOIN nearest_expiry_qty neq
                ON neq.code_prod = p.code
               AND neq.prochaine_peremption = ne.prochaine_peremption
            LEFT JOIN latest_activity la
                ON la.code_prod = p.code
            ORDER BY p.produit ASC
        """)
    ).mappings().all()

    return {
        "columns": [
            "code",
            "produit",
            "forme",
            "dosage",
            "classe",
            "cible",
            "unite",
            "prix_achat",
            "prix_vente",
            "stock_actuel",
            "lots_count",
            "prochaine_peremption",
            "stock_peremption_proche",
            "statut",
            "date_mise_a_jour",
        ],
        "rows": [dict(r) for r in rows],
    }

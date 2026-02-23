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
    rows = db.execute(text("""
        SELECT
            code, produit, forme, dosage, classe, cible, unite,
            prix_achat, prix_vente, stock_actuel, date_creation, statut
        FROM `0_products`
        ORDER BY produit ASC
    """)).mappings().all()

    return {
        "columns": [
            "code","produit","forme","dosage","classe","cible","unite",
            "prix_achat","prix_vente","stock_actuel","date_creation","statut"
        ],
        "rows": [dict(r) for r in rows]
    }

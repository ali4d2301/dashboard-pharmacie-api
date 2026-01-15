from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel

from db import get_db

router = APIRouter(prefix="/api/products", tags=["products"])

TABLE = "`0_products`"


# =========================
# GET – liste produits
# =========================
@router.get("/edit_products")
def get_products(db: Session = Depends(get_db)):
    query = text(f"""
        SELECT code, produit, unite, prix_achat, prix_vente, statut
        FROM {TABLE}
        ORDER BY code
        LIMIT 500
    """)

    result = db.execute(query)
    rows = result.mappings().all()   # dictionnaires propres

    return rows


# =========================
# PATCH MODEL
# =========================
class ProductPatch(BaseModel):
    code: str
    produit: Optional[str] = None
    unite: Optional[str] = None
    prix_achat: Optional[float] = None
    prix_vente: Optional[float] = None
    statut: Optional[str] = None


# =========================
# PUT – mise à jour produits
# =========================
@router.put("/edit_products")
def update_products(
    patches: List[ProductPatch],
    db: Session = Depends(get_db)
):
    updated = 0

    for p in patches:
        fields = []
        params = {}

        def add(col, val):
            if val is not None:
                fields.append(f"{col} = :{col}")
                params[col] = val

        add("produit", p.produit)
        add("unite", p.unite)
        add("prix_achat", p.prix_achat)
        add("prix_vente", p.prix_vente)
        add("statut", p.statut)

        if not fields:
            continue

        params["code"] = p.code

        query = text(f"""
            UPDATE {TABLE}
            SET {", ".join(fields)}
            WHERE code = :code
        """)

        result = db.execute(query, params)
        updated += result.rowcount

    db.commit()

    return {"updated": updated}

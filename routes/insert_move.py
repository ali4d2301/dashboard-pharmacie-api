from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from db import get_db

router = APIRouter(prefix="/api", tags=["mouvements"])

# ✅ adapte si ta liste ENUM exacte diffère
MOUVEMENTS_ALLOWED = {"achat", "vente", "perte", "peremption", "don", "ajustement positif", "ajustement negatif"}

class MouvementCreate(BaseModel):
    date_mvt: date
    code_prod: str = Field(min_length=1, max_length=50)
    type_mvt: Literal["entree", "sortie"]
    mouvement: str
    # L’utilisateur ne saisit pas -> on met 1 par défaut
    quantite: int = 1
    commentaire: Optional[str] = None

@router.get("/products/{code}")
def get_product_active(code: str, db: Session = Depends(get_db)):
    q = text("""
        SELECT code, produit, forme, dosage, unite, prix_achat, prix_vente, stock_actuel, statut
        FROM `0_products`
        WHERE code = :code
        LIMIT 1
    """)
    row = db.execute(q, {"code": code}).mappings().first()

    if not row:
        raise HTTPException(status_code=404, detail="Produit introuvable")

    if row["statut"] != "Actif":
        # spécial demandé : rien ne se passe + notification
        raise HTTPException(status_code=409, detail="Produit inactif")

    return dict(row)

@router.post("/mouvements", status_code=201)
def create_mouvement(payload: MouvementCreate, db: Session = Depends(get_db)):
    if payload.mouvement not in MOUVEMENTS_ALLOWED:
        raise HTTPException(status_code=422, detail="Mouvement non autorisé")

    # 1) vérifier produit actif
    check = db.execute(
        text("SELECT statut FROM `0_products` WHERE code=:c LIMIT 1"),
        {"c": payload.code_prod},
    ).mappings().first()

    if not check:
        raise HTTPException(status_code=404, detail="Produit introuvable")
    if check["statut"] != "Actif":
        raise HTTPException(status_code=409, detail="Produit inactif")

    # 2) insert (id auto_increment, stock_apres géré en DB)
    ins = text("""
        INSERT INTO `0_mouvement_stock` (date_mvt, code_prod, type_mvt, mouvement, quantite, commentaire)
        VALUES (:date_mvt, :code_prod, :type_mvt, :mouvement, :quantite, :commentaire)
    """)
    db.execute(ins, payload.model_dump())
    db.commit()

    return {"ok": True}

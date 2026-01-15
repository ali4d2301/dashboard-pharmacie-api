from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import  text
from db import get_db, engine
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from datetime import date
from typing import Optional

router = APIRouter(prefix="/api/products", tags=["products"])

TABLE = "`0_products`"

class ProductIn(BaseModel):
    code: str = Field(..., max_length=50)
    produit: str = Field(..., max_length=255)
    forme: Optional[str] = Field(None, max_length=100)
    dosage: Optional[str] = Field(None, max_length=100)
    classe: Optional[str] = Field(None, max_length=150)
    cible: Optional[str] = Field(None, max_length=150)
    unite: Optional[str] = Field(None, max_length=30)
    prix_achat: Optional[float] = None
    prix_vente: Optional[float] = None
    stock_actuel: Optional[int] = None
    date_creation: Optional[date] = None
    statut: str = Field("Actif")

@router.post("/insert_prod", status_code=201)
def create_product(p: ProductIn, db: Session = Depends(get_db)):
    if p.statut not in ("Actif", "Inactif"):
        raise HTTPException(status_code=400, detail="statut doit être Actif ou Inactif")

    sql = text(f"""
        INSERT INTO {TABLE}
        (code, produit, forme, dosage, classe, cible, unite, prix_achat, prix_vente, stock_actuel, date_creation, statut)
        VALUES (:code, :produit, :forme, :dosage, :classe, :cible, :unite, :prix_achat, :prix_vente, :stock_actuel, :date_creation, :statut)
    """)

    try:
        with engine.begin() as conn:
            conn.execute(sql, {
                "code": p.code,
                "produit": p.produit,
                "forme": p.forme,
                "dosage": p.dosage,
                "classe": p.classe,
                "cible": p.cible,
                "unite": p.unite,
                "prix_achat": p.prix_achat,
                "prix_vente": p.prix_vente,
                "stock_actuel": p.stock_actuel,
                "date_creation": p.date_creation,
                "statut": p.statut,
            })
        return {"message": "✅ Produit enregistré."}

    except IntegrityError as e:
        # doublon sur PRIMARY KEY (code)
        raise HTTPException(status_code=409, detail="Ce code existe déjà.") from e
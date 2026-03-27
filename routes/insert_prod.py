from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from db import get_db
from deps_auth import require_role

router = APIRouter(
    prefix="/api/products",
    tags=["products"],
    dependencies=[Depends(require_role("admin"))],
)

PRODUCTS_TABLE = "`0_products`"
LOTS_TABLE = "`product_lots`"


class ProductIn(BaseModel):
    code: str = Field(..., max_length=50)
    numero_lot: str = Field(..., max_length=100)
    produit: str = Field(..., max_length=255)
    forme: Optional[str] = Field(None, max_length=100)
    dosage: Optional[str] = Field(None, max_length=100)
    classe: Optional[str] = Field(None, max_length=150)
    cible: Optional[str] = Field(None, max_length=150)
    unite: Optional[str] = Field(None, max_length=30)
    prix_achat: Optional[float] = None
    prix_vente: Optional[float] = None
    stock_actuel: Optional[int] = None
    date_peremption: date
    date_creation: Optional[date] = None
    statut: str = Field("Actif")

    @field_validator("code", "numero_lot", "produit", mode="before")
    @classmethod
    def validate_required_text(cls, value: object) -> str:
        if value is None:
            raise ValueError("Champ obligatoire")

        text_value = str(value).strip()
        if not text_value:
            raise ValueError("Champ obligatoire")

        return text_value


@router.post("/insert_prod", status_code=201)
def create_product(p: ProductIn, db: Session = Depends(get_db)):
    if p.statut not in ("Actif", "Inactif"):
        raise HTTPException(status_code=400, detail="statut doit etre Actif ou Inactif")

    stock_initial = int(p.stock_actuel or 0)
    created_at = p.date_creation or date.today()

    insert_product = text(f"""
        INSERT INTO {PRODUCTS_TABLE}
        (
            code, produit, forme, dosage, classe, cible, unite,
            prix_achat, prix_vente, stock_actuel, date_creation, statut
        )
        VALUES (
            :code, :produit, :forme, :dosage, :classe, :cible, :unite,
            :prix_achat, :prix_vente, :stock_actuel, :date_creation, :statut
        )
    """)

    insert_lot = text(f"""
        INSERT INTO {LOTS_TABLE}
        (code_prod, numero_lot, date_peremption, stock_lot, created_at)
        VALUES (:code_prod, :numero_lot, :date_peremption, :stock_lot, :created_at)
    """)

    try:
        db.execute(
            insert_product,
            {
                "code": p.code,
                "produit": p.produit,
                "forme": p.forme,
                "dosage": p.dosage,
                "classe": p.classe,
                "cible": p.cible,
                "unite": p.unite,
                "prix_achat": p.prix_achat,
                "prix_vente": p.prix_vente,
                "stock_actuel": stock_initial,
                "date_creation": p.date_creation,
                "statut": p.statut,
            },
        )
        db.execute(
            insert_lot,
            {
                "code_prod": p.code,
                "numero_lot": p.numero_lot,
                "date_peremption": p.date_peremption,
                "stock_lot": stock_initial,
                "created_at": created_at,
            },
        )
        db.commit()
        return {"message": "Produit et lot initial enregistres."}
    except IntegrityError as e:
        db.rollback()
        message = str(e).lower()
        if "duplicate" in message and "primary" in message:
            raise HTTPException(status_code=409, detail="Ce code existe deja.") from e
        if "uq_product_lots_code_lot_exp" in message or "duplicate" in message:
            raise HTTPException(
                status_code=409,
                detail="Ce lot existe deja pour ce produit.",
            ) from e
        raise
    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="Erreur lors de la creation du produit.") from e

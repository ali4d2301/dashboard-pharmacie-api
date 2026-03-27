from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
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

TABLE = "`0_products`"
LOTS_TABLE = "`product_lots`"
DASHBOARD_TABLE = "`tb_dashboard`"


@router.get("/edit_products")
def get_products(db: Session = Depends(get_db)):
    query = text(
        f"""
        SELECT
            p.code,
            p.produit,
            p.classe,
            p.cible,
            p.unite,
            p.statut,
            COALESCE(l.lots_count, 0) AS lots_count,
            l.prochaine_peremption
        FROM {TABLE} p
        LEFT JOIN (
            SELECT
                code_prod,
                COUNT(*) AS lots_count,
                MIN(CASE WHEN stock_lot > 0 THEN date_peremption END) AS prochaine_peremption
            FROM {LOTS_TABLE}
            GROUP BY code_prod
        ) l
            ON l.code_prod = p.code
        ORDER BY p.code
        LIMIT 500
        """
    )

    rows = [dict(row) for row in db.execute(query).mappings().all()]
    if not rows:
        return rows

    lots_rows = db.execute(
        text(
            f"""
            SELECT
                id,
                code_prod,
                numero_lot,
                date_peremption,
                stock_lot,
                created_at
            FROM {LOTS_TABLE}
            ORDER BY code_prod ASC, date_peremption ASC, numero_lot ASC
            """
        )
    ).mappings().all()

    lots_by_code = {}
    for lot in lots_rows:
        lot_dict = dict(lot)
        lots_by_code.setdefault(lot_dict["code_prod"], []).append(lot_dict)

    for row in rows:
        row["lots"] = lots_by_code.get(row["code"], [])

    return rows


class ProductPatch(BaseModel):
    code: str
    produit: Optional[str] = None
    classe: Optional[str] = None
    cible: Optional[str] = None
    unite: Optional[str] = None
    statut: Optional[str] = None
    lot_id: Optional[int] = None
    lot_date_peremption: Optional[str] = None

    @field_validator("code", mode="before")
    @classmethod
    def validate_code(cls, value: object) -> str:
        if value is None:
            raise ValueError("code obligatoire")

        text_value = str(value).strip()
        if not text_value:
            raise ValueError("code obligatoire")

        return text_value

    @field_validator("produit", mode="before")
    @classmethod
    def validate_produit(cls, value: object) -> Optional[str]:
        if value is None:
            return None

        text_value = str(value).strip()
        if not text_value:
            raise ValueError("produit obligatoire")

        return text_value

    @field_validator("statut")
    @classmethod
    def validate_statut(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if value not in ("Actif", "Inactif"):
            raise ValueError("statut doit etre Actif ou Inactif")
        return value


def _normalize_optional_date(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None

    text_value = str(value).strip()
    return text_value or None


@router.put("/edit_products")
def update_products(patches: List[ProductPatch], db: Session = Depends(get_db)):
    updated = 0

    try:
        for patch in patches:
            fields = []
            params = {}

            def add_product_field(column: str, value: object) -> None:
                if value is not None:
                    fields.append(f"{column} = :{column}")
                    params[column] = value

            add_product_field("produit", patch.produit)
            add_product_field("classe", patch.classe)
            add_product_field("cible", patch.cible)
            add_product_field("unite", patch.unite)
            add_product_field("statut", patch.statut)

            if fields:
                params["code"] = patch.code
                result = db.execute(
                    text(
                        f"""
                        UPDATE {TABLE}
                        SET {", ".join(fields)}
                        WHERE code = :code
                        """
                    ),
                    params,
                )
                updated += result.rowcount

            if patch.lot_id is None:
                continue

            lot_row = db.execute(
                text(
                    f"""
                    SELECT id, code_prod, date_peremption
                    FROM {LOTS_TABLE}
                    WHERE id = :lot_id
                    LIMIT 1
                    """
                ),
                {"lot_id": patch.lot_id},
            ).mappings().first()

            if not lot_row:
                raise HTTPException(status_code=404, detail=f"Lot introuvable (id={patch.lot_id})")
            if lot_row["code_prod"] != patch.code:
                raise HTTPException(status_code=400, detail="Le lot choisi ne correspond pas au produit.")

            current_date = _normalize_optional_date(
                str(lot_row["date_peremption"]) if lot_row["date_peremption"] else None
            )
            next_date = (
                _normalize_optional_date(patch.lot_date_peremption)
                if patch.lot_date_peremption is not None
                else current_date
            )

            if patch.lot_date_peremption is None or next_date == current_date:
                continue
            if next_date is None:
                raise HTTPException(
                    status_code=400,
                    detail="La date de peremption du lot est obligatoire.",
                )

            lot_params = {
                "lot_id": patch.lot_id,
                "lot_date_peremption": next_date,
            }

            db.execute(
                text(
                    f"""
                    UPDATE {LOTS_TABLE}
                    SET date_peremption = :lot_date_peremption
                    WHERE id = :lot_id
                    """
                ),
                lot_params,
            )

            db.execute(
                text(
                    f"""
                    UPDATE {DASHBOARD_TABLE}
                    SET date_peremption = :lot_date_peremption
                    WHERE lot_id = :lot_id
                    """
                ),
                lot_params,
            )

            updated += 1

        db.commit()
    except IntegrityError as exc:
        db.rollback()
        message = str(exc).lower()
        if "uq_product_lots_code_lot_exp" in message or "duplicate" in message:
            raise HTTPException(
                status_code=409,
                detail="Un lot avec cette peremption existe deja pour ce produit.",
            ) from exc
        raise HTTPException(status_code=409, detail="Contrainte de base de donnees violee.") from exc
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Erreur lors de la mise a jour des produits.",
        ) from exc

    return {"updated": updated}

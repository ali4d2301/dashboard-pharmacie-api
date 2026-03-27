from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from db import get_db
from deps_auth import require_role
from movement_recalc import HistoricalStockError, get_movement_product_codes, recalculate_products

router = APIRouter(
    prefix="/api/movements",
    tags=["mouvements"],
    dependencies=[Depends(require_role("admin"))],
)

TABLE = "`0_mouvement_stock`"

ALLOWED_TYPE = {"entree", "sortie"}
ALLOWED_MVT = {
    "acquision",
    "dispensation",
    "perte",
    "peremption",
    "achat",
    "vente",
    "don",
    "ajustement",
    "ajustement positif",
    "ajustement negatif",
}


class MovementOut(BaseModel):
    id: int
    date_mvt: date
    code_prod: str
    type_mvt: str
    mouvement: str
    quantite: float
    commentaire: Optional[str] = None


class MovementPatch(BaseModel):
    id: int
    date_mvt: Optional[date] = None
    quantite: Optional[float] = Field(default=None, ge=0)
    type_mvt: Optional[str] = None
    mouvement: Optional[str] = None
    commentaire: Optional[str] = None

    @field_validator("date_mvt")
    @classmethod
    def validate_date_mvt(cls, value: Optional[date]) -> Optional[date]:
        if value is not None and value > date.today():
            raise ValueError("La date du mouvement ne peut pas etre posterieure a la date du jour.")
        return value


class BulkUpdateResult(BaseModel):
    updated: int


def _validate_patch(patch: MovementPatch) -> None:
    if patch.type_mvt is not None and patch.type_mvt not in ALLOWED_TYPE:
        raise HTTPException(status_code=400, detail=f"type_mvt invalide: {patch.type_mvt}")
    if patch.mouvement is not None and patch.mouvement not in ALLOWED_MVT:
        raise HTTPException(status_code=400, detail=f"mouvement invalide: {patch.mouvement}")


def _signed_quantity(type_mvt: str, quantite: float | int | None) -> int:
    qty = int(quantite or 0)
    if type_mvt == "entree":
        return qty
    if type_mvt == "sortie":
        return -qty
    raise HTTPException(status_code=400, detail=f"type_mvt invalide: {type_mvt}")


@router.get("/edit", response_model=List[MovementOut])
def list_movements_for_edit(
    code_prod: str,
    day: date,
    db: Session = Depends(get_db),
):
    if day > date.today():
        raise HTTPException(
            status_code=400,
            detail="La date du mouvement ne peut pas etre posterieure a la date du jour.",
        )

    query = text(
        f"""
        SELECT id, date_mvt, code_prod, type_mvt, mouvement, quantite, commentaire
        FROM {TABLE}
        WHERE code_prod = :code_prod
          AND date_mvt = :day
        ORDER BY id ASC
        """
    )
    rows = db.execute(query, {"code_prod": code_prod, "day": day}).mappings().all()
    return [dict(row) for row in rows]


@router.put("/edit", response_model=BulkUpdateResult)
def bulk_update_movements(
    patches: List[MovementPatch],
    db: Session = Depends(get_db),
):
    if not patches:
        return {"updated": 0}

    patch_ids = [int(patch.id) for patch in patches]
    if len(set(patch_ids)) != len(patch_ids):
        raise HTTPException(
            status_code=400,
            detail="Chaque mouvement doit apparaitre une seule fois dans la requete.",
        )

    for patch in patches:
        _validate_patch(patch)

    existing_stmt = text(
        f"""
        SELECT id, code_prod, lot_id, type_mvt, quantite
        FROM {TABLE}
        WHERE id IN :ids
        """
    ).bindparams(bindparam("ids", expanding=True))
    existing_rows = db.execute(existing_stmt, {"ids": patch_ids}).mappings().all()
    existing_map = {int(row["id"]): dict(row) for row in existing_rows}

    missing_ids = [movement_id for movement_id in patch_ids if movement_id not in existing_map]
    if missing_ids:
        raise HTTPException(status_code=404, detail=f"Mouvement introuvable (id={missing_ids[0]})")

    stock_delta_by_lot: Dict[int, int] = {}
    stock_delta_by_product: Dict[str, int] = {}

    for patch in patches:
        current = existing_map[int(patch.id)]
        next_type = patch.type_mvt if patch.type_mvt is not None else str(current["type_mvt"])
        next_quantity = patch.quantite if patch.quantite is not None else current["quantite"]
        old_signed = _signed_quantity(str(current["type_mvt"]), current["quantite"])
        new_signed = _signed_quantity(next_type, next_quantity)
        delta = new_signed - old_signed

        if delta == 0:
            continue

        lot_id = int(current["lot_id"])
        code_prod = str(current["code_prod"])
        stock_delta_by_lot[lot_id] = stock_delta_by_lot.get(lot_id, 0) + delta
        stock_delta_by_product[code_prod] = stock_delta_by_product.get(code_prod, 0) + delta

    affected_products = get_movement_product_codes(patch_ids, db)
    updated = 0

    try:
        for patch in patches:
            sets = []
            params: Dict[str, Any] = {"id": patch.id}

            if patch.date_mvt is not None:
                sets.append("date_mvt = :date_mvt")
                params["date_mvt"] = patch.date_mvt

            if patch.quantite is not None:
                sets.append("quantite = :quantite")
                params["quantite"] = patch.quantite

            if patch.type_mvt is not None:
                sets.append("type_mvt = :type_mvt")
                params["type_mvt"] = patch.type_mvt

            if patch.mouvement is not None:
                sets.append("mouvement = :mouvement")
                params["mouvement"] = patch.mouvement

            if patch.commentaire is not None:
                sets.append("commentaire = :commentaire")
                params["commentaire"] = patch.commentaire

            if not sets:
                continue

            result = db.execute(
                text(
                    f"""
                    UPDATE {TABLE}
                    SET {", ".join(sets)}
                    WHERE id = :id
                    """
                ),
                params,
            )

            if result.rowcount == 0:
                raise HTTPException(status_code=404, detail=f"Mouvement introuvable (id={patch.id})")

            updated += result.rowcount

        if stock_delta_by_lot:
            db.execute(
                text(
                    """
                    UPDATE `product_lots`
                    SET stock_lot = stock_lot + :delta
                    WHERE id = :id
                    """
                ),
                [
                    {"id": lot_id, "delta": delta}
                    for lot_id, delta in stock_delta_by_lot.items()
                ],
            )

        if stock_delta_by_product:
            db.execute(
                text(
                    """
                    UPDATE `0_products`
                    SET stock_actuel = stock_actuel + :delta
                    WHERE code = :code
                    """
                ),
                [
                    {"code": code_prod, "delta": delta}
                    for code_prod, delta in stock_delta_by_product.items()
                ],
            )

        recalculate_products(affected_products, db)
        db.commit()
        return {"updated": updated}
    except HistoricalStockError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erreur serveur: {str(exc)}") from exc

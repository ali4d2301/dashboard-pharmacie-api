from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from db import get_db
from deps_auth import require_role
from movement_recalc import HistoricalStockError, get_lot_stocks_on_date, recalculate_product_history

router = APIRouter(
    prefix="/api",
    tags=["mouvements"],
    dependencies=[Depends(require_role("admin"))],
)

PRODUCTS_TABLE = "`0_products`"
LOTS_TABLE = "`product_lots`"
MOVEMENTS_TABLE = "`0_mouvement_stock`"

MOUVEMENTS_ALLOWED = {
    "acquision",
    "dispensation",
    "perte",
    "peremption",
    "achat",
    "vente",
    "don",
    "ajustement positif",
    "ajustement negatif",
}


class MouvementCreate(BaseModel):
    date_mvt: date
    code_prod: str = Field(min_length=1, max_length=50)
    type_mvt: Literal["entree", "sortie"]
    mouvement: str
    quantite: int = Field(default=1, ge=1)
    lot_id: Optional[int] = None
    numero_lot: Optional[str] = Field(default=None, max_length=100)
    date_peremption: Optional[date] = None
    commentaire: Optional[str] = None

    @field_validator("numero_lot", mode="before")
    @classmethod
    def normalize_numero_lot(cls, value: object) -> Optional[str]:
        if value is None:
            return None
        text_value = str(value).strip()
        return text_value or None

    @field_validator("date_mvt")
    @classmethod
    def validate_date_mvt(cls, value: date) -> date:
        if value > date.today():
            raise ValueError("La date du mouvement ne peut pas être postérieure à la date du jour.")
        return value


@router.get("/products/options")
def list_product_options(db: Session = Depends(get_db)):
    rows = db.execute(
        text(
            f"""
            SELECT code, produit
            FROM {PRODUCTS_TABLE}
            WHERE statut = 'Actif'
            ORDER BY code ASC, produit ASC
            """
        )
    ).mappings().all()

    return {
        "items": [
            {
                "code": str(row["code"]),
                "produit": str(row["produit"] or ""),
            }
            for row in rows
        ]
    }


@router.get("/products/{code}")
def get_product_active(code: str, db: Session = Depends(get_db)):
    q = text(f"""
        SELECT
            p.code,
            p.produit,
            p.forme,
            p.dosage,
            p.unite,
            p.prix_achat,
            p.prix_vente,
            p.stock_actuel,
            p.statut,
            COALESCE(l.lots_count, 0) AS lots_count,
            l.prochaine_peremption
        FROM {PRODUCTS_TABLE} p
        LEFT JOIN (
            SELECT
                code_prod,
                COUNT(*) AS lots_count,
                MIN(CASE WHEN stock_lot > 0 THEN date_peremption END) AS prochaine_peremption
            FROM {LOTS_TABLE}
            GROUP BY code_prod
        ) l
            ON l.code_prod = p.code
        WHERE p.code = :code
        LIMIT 1
    """)
    row = db.execute(q, {"code": code}).mappings().first()

    if not row:
        raise HTTPException(status_code=404, detail="Produit introuvable")

    if row["statut"] != "Actif":
        raise HTTPException(status_code=409, detail="Produit inactif")

    return dict(row)


@router.get("/products/{code}/lots")
def list_product_lots(
    code: str,
    movement_date: Optional[date] = Query(default=None),
    movement_type: Optional[Literal["entree", "sortie"]] = Query(default=None),
    db: Session = Depends(get_db),
):
    if movement_date is not None and movement_date > date.today():
        raise HTTPException(
            status_code=422,
            detail="La date du mouvement ne peut pas être postérieure à la date du jour.",
        )

    rows = db.execute(
        text(f"""
            SELECT
                id,
                code_prod,
                numero_lot,
                date_peremption,
                stock_lot,
                created_at,
                (
                    SELECT MIN(m.date_mvt)
                    FROM {MOVEMENTS_TABLE} m
                    WHERE m.lot_id = {LOTS_TABLE}.id
                ) AS first_movement_date
            FROM {LOTS_TABLE}
            WHERE code_prod = :code
            ORDER BY date_peremption ASC, numero_lot ASC
        """),
        {"code": code},
    ).mappings().all()

    items = [dict(r) for r in rows]
    stock_snapshot = (
        get_lot_stocks_on_date(code, movement_date, db)
        if movement_date is not None
        else None
    )
    if stock_snapshot is not None:
        for item in items:
            historical_stock = int(stock_snapshot.get(int(item["id"]), 0))
            item["stock_lot_at_movement_date"] = historical_stock
            if movement_type == "sortie":
                item["stock_lot"] = historical_stock

    existing_items = items
    if movement_date is not None:
        existing_items = [
            item
            for item in items
            if _get_lot_effective_created_at(item) is None
            or _get_lot_effective_created_at(item) <= movement_date
        ]

    selectable_items = existing_items
    if movement_type == "sortie":
        selectable_items = [
            item for item in existing_items if float(item["stock_lot"] or 0) > 0
        ]

    return {
        "items": selectable_items,
        "total_items": len(items),
        "existing_items_on_movement_date": len(existing_items),
        "selectable_items_on_movement_date": len(selectable_items),
        "filtered_by_movement_date": movement_date is not None,
        "movement_date": movement_date,
        "movement_type": movement_type,
    }


def _assert_lot_exists_for_movement_date(lot: dict, movement_date: date) -> None:
    effective_created_at = _get_lot_effective_created_at(lot)
    if effective_created_at is not None and effective_created_at > movement_date:
        raise HTTPException(
            status_code=409,
            detail="Le lot sélectionné n'existait pas encore à la date du mouvement.",
        )


def _get_lot_effective_created_at(lot: dict) -> Optional[date]:
    created_at = lot.get("created_at")
    first_movement_date = lot.get("first_movement_date")
    if created_at is None:
        return first_movement_date
    if first_movement_date is None:
        return created_at
    return min(created_at, first_movement_date)


def _get_lot_for_product(lot_id: int, code_prod: str, db: Session) -> dict:
    lot = db.execute(
        text(f"""
            SELECT
                id,
                code_prod,
                numero_lot,
                date_peremption,
                stock_lot,
                created_at,
                (
                    SELECT MIN(m.date_mvt)
                    FROM {MOVEMENTS_TABLE} m
                    WHERE m.lot_id = {LOTS_TABLE}.id
                ) AS first_movement_date
            FROM {LOTS_TABLE}
            WHERE id = :lot_id AND code_prod = :code_prod
            LIMIT 1
        """),
        {"lot_id": lot_id, "code_prod": code_prod},
    ).mappings().first()
    if not lot:
        raise HTTPException(status_code=404, detail="Lot introuvable pour ce produit")
    return dict(lot)


def _resolve_lot_id(payload: MouvementCreate, db: Session) -> int:
    if payload.lot_id is not None:
        lot = _get_lot_for_product(int(payload.lot_id), payload.code_prod, db)
        _assert_lot_exists_for_movement_date(lot, payload.date_mvt)
        return int(lot["id"])

    if payload.numero_lot is None or payload.date_peremption is None:
        raise HTTPException(
            status_code=400,
            detail="Numero de lot et date de peremption obligatoires pour creer un nouveau lot.",
        )

    lot = db.execute(
        text(f"""
            SELECT
                id,
                created_at,
                (
                    SELECT MIN(m.date_mvt)
                    FROM {MOVEMENTS_TABLE} m
                    WHERE m.lot_id = {LOTS_TABLE}.id
                ) AS first_movement_date
            FROM {LOTS_TABLE}
            WHERE code_prod = :code_prod
              AND numero_lot = :numero_lot
              AND date_peremption = :date_peremption
            LIMIT 1
        """),
        {
            "code_prod": payload.code_prod,
            "numero_lot": payload.numero_lot,
            "date_peremption": payload.date_peremption,
        },
    ).mappings().first()
    if lot:
        _assert_lot_exists_for_movement_date(dict(lot), payload.date_mvt)
        return int(lot["id"])

    try:
        result = db.execute(
            text(f"""
                INSERT INTO {LOTS_TABLE}
                (code_prod, numero_lot, date_peremption, stock_lot, created_at)
                VALUES (:code_prod, :numero_lot, :date_peremption, 0, :created_at)
            """),
            {
                "code_prod": payload.code_prod,
                "numero_lot": payload.numero_lot,
                "date_peremption": payload.date_peremption,
                "created_at": payload.date_mvt,
            },
        )
        return int(result.lastrowid)
    except IntegrityError:
        lot = db.execute(
            text(f"""
                SELECT
                    id,
                    created_at,
                    (
                        SELECT MIN(m.date_mvt)
                        FROM {MOVEMENTS_TABLE} m
                        WHERE m.lot_id = {LOTS_TABLE}.id
                    ) AS first_movement_date
                FROM {LOTS_TABLE}
                WHERE code_prod = :code_prod
                  AND numero_lot = :numero_lot
                  AND date_peremption = :date_peremption
                LIMIT 1
            """),
            {
                "code_prod": payload.code_prod,
                "numero_lot": payload.numero_lot,
                "date_peremption": payload.date_peremption,
            },
        ).mappings().first()
        if not lot:
            raise
        _assert_lot_exists_for_movement_date(dict(lot), payload.date_mvt)
        return int(lot["id"])


@router.post("/mouvements", status_code=201)
def create_mouvement(payload: MouvementCreate, db: Session = Depends(get_db)):
    if payload.mouvement not in MOUVEMENTS_ALLOWED:
        raise HTTPException(status_code=422, detail="Mouvement non autorise")

    check = db.execute(
        text(f"SELECT statut FROM {PRODUCTS_TABLE} WHERE code = :code LIMIT 1"),
        {"code": payload.code_prod},
    ).mappings().first()

    if not check:
        raise HTTPException(status_code=404, detail="Produit introuvable")
    if check["statut"] != "Actif":
        raise HTTPException(status_code=409, detail="Produit inactif")

    if payload.type_mvt == "sortie" and payload.lot_id is None:
        raise HTTPException(status_code=400, detail="Choisissez un lot pour ce mouvement.")

    if payload.type_mvt == "entree":
        lot_id = _resolve_lot_id(payload, db)
    else:
        lot = _get_lot_for_product(int(payload.lot_id), payload.code_prod, db)
        _assert_lot_exists_for_movement_date(lot, payload.date_mvt)
        lot_id = int(lot["id"])

    ins = text(f"""
        INSERT INTO {MOVEMENTS_TABLE}
        (date_mvt, code_prod, lot_id, type_mvt, mouvement, quantite, commentaire)
        VALUES (:date_mvt, :code_prod, :lot_id, :type_mvt, :mouvement, :quantite, :commentaire)
    """)

    try:
        db.execute(
            ins,
            {
                "date_mvt": payload.date_mvt,
                "code_prod": payload.code_prod,
                "lot_id": lot_id,
                "type_mvt": payload.type_mvt,
                "mouvement": payload.mouvement,
                "quantite": payload.quantite,
                "commentaire": payload.commentaire,
            },
        )
        recalculate_product_history(payload.code_prod, db)
        db.commit()
    except HistoricalStockError as e:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(e)) from e
    except SQLAlchemyError as e:
        db.rollback()
        message = str(e)
        if "Illegal mix of collations" in message:
            raise HTTPException(
                status_code=500,
                detail="Conflit de collation dans le trigger SQL des mouvements. Le correctif serveur doit être appliqué.",
            ) from e
        if "foreign key constraint fails" in message.lower():
            raise HTTPException(
                status_code=409,
                detail="Le produit ou le lot sélectionné n'est plus disponible. Recharge la fiche puis recommence.",
            ) from e
        if "Data truncated for column 'mouvement'" in message or "Incorrect enum value" in message:
            raise HTTPException(
                status_code=422,
                detail="Le mouvement choisi n'est pas autorise pour cet enregistrement.",
            ) from e
        if "0_mouvement_stock_chk_1" in message or "check constraint" in message.lower():
            raise HTTPException(
                status_code=422,
                detail="La quantité doit être strictement positive.",
            ) from e
        if "Stock insuffisant pour ce lot" in message:
            raise HTTPException(status_code=409, detail="Stock insuffisant pour ce lot") from e
        if "Lot introuvable" in message:
            raise HTTPException(status_code=404, detail="Lot introuvable pour ce produit") from e
        if "Le lot ne correspond pas au produit" in message:
            raise HTTPException(status_code=400, detail="Le lot choisi ne correspond pas au produit") from e
        raise HTTPException(status_code=500, detail="Erreur lors de l'enregistrement du mouvement.") from e

    return {"ok": True, "lot_id": lot_id}

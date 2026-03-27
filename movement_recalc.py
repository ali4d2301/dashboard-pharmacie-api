from collections import defaultdict
from datetime import date
from typing import Iterable

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

LOTS_TABLE = "`product_lots`"
MOVEMENTS_TABLE = "`0_mouvement_stock`"
PRODUCTS_TABLE = "`0_products`"
DASHBOARD_TABLE = "`tb_dashboard`"


class HistoricalStockError(ValueError):
    pass


def _effective_created_at(created_at: date | None, first_movement_date: date | None) -> date | None:
    if created_at is None:
        return first_movement_date
    if first_movement_date is None:
        return created_at
    return min(created_at, first_movement_date)


def _signed_quantity(type_mvt: str, quantite: int | float | None) -> int:
    qty = int(quantite or 0)
    if type_mvt == "entree":
        return qty
    if type_mvt == "sortie":
        return -qty
    raise HistoricalStockError(f"Type de mouvement inconnu: {type_mvt}")


def get_movement_product_codes(movement_ids: Iterable[int], db: Session) -> list[str]:
    ids = [int(movement_id) for movement_id in movement_ids]
    if not ids:
        return []

    stmt = text(
        f"""
        SELECT DISTINCT code_prod
        FROM {MOVEMENTS_TABLE}
        WHERE id IN :ids
        """
    ).bindparams(bindparam("ids", expanding=True))

    return [str(code) for code in db.execute(stmt, {"ids": ids}).scalars().all() if code]


def recalculate_product_history(code_prod: str, db: Session) -> dict:
    lot_rows = db.execute(
        text(
            f"""
            SELECT
                id,
                numero_lot,
                created_at,
                stock_lot
            FROM {LOTS_TABLE}
            WHERE code_prod = :code_prod
            ORDER BY created_at ASC, id ASC
            FOR UPDATE
            """
        ),
        {"code_prod": code_prod},
    ).mappings().all()

    movement_rows = db.execute(
        text(
            f"""
            SELECT
                id,
                date_mvt,
                lot_id,
                type_mvt,
                quantite
            FROM {MOVEMENTS_TABLE}
            WHERE code_prod = :code_prod
            ORDER BY date_mvt ASC, id ASC
            FOR UPDATE
            """
        ),
        {"code_prod": code_prod},
    ).mappings().all()

    lot_meta: dict[int, dict] = {}
    lot_deltas = defaultdict(int)
    first_movement_by_lot: dict[int, date] = {}

    for row in movement_rows:
        lot_id = int(row["lot_id"])
        movement_date = row["date_mvt"]
        lot_deltas[lot_id] += _signed_quantity(str(row["type_mvt"]), row["quantite"])
        first_movement_by_lot.setdefault(lot_id, movement_date)

    opening_by_lot: dict[int, int] = {}
    running_by_lot: dict[int, int] = {}

    for row in lot_rows:
        lot_id = int(row["id"])
        effective_created_at = _effective_created_at(
            row["created_at"],
            first_movement_by_lot.get(lot_id),
        )
        lot_meta[lot_id] = {
            "numero_lot": row["numero_lot"],
            "created_at": effective_created_at,
        }
        current_stock = int(row["stock_lot"] or 0)
        opening_stock = current_stock - lot_deltas.get(lot_id, 0)
        if opening_stock < 0:
            numero_lot = row["numero_lot"] or f"#{lot_id}"
            raise HistoricalStockError(
                f"Le lot {numero_lot} a un stock historique incoherent."
            )
        opening_by_lot[lot_id] = opening_stock
        running_by_lot[lot_id] = opening_stock

    running_product_stock = sum(running_by_lot.values())
    movement_updates: list[dict[str, int]] = []

    for row in movement_rows:
        movement_id = int(row["id"])
        lot_id = int(row["lot_id"])
        movement_date = row["date_mvt"]
        quantity = int(row["quantite"] or 0)
        type_mvt = str(row["type_mvt"])

        meta = lot_meta.get(lot_id)
        if meta is None:
            raise HistoricalStockError("Un mouvement reference un lot introuvable.")

        created_at = meta["created_at"]
        if created_at is not None and movement_date < created_at:
            numero_lot = meta["numero_lot"] or f"#{lot_id}"
            raise HistoricalStockError(
                f"Le lot {numero_lot} n'existait pas encore a la date du mouvement."
            )

        current_lot_stock = running_by_lot[lot_id]

        if type_mvt == "sortie" and current_lot_stock < quantity:
            numero_lot = meta["numero_lot"] or f"#{lot_id}"
            raise HistoricalStockError(
                f"Stock insuffisant pour le lot {numero_lot} a la date du mouvement."
            )

        signed_qty = _signed_quantity(type_mvt, quantity)
        running_by_lot[lot_id] = current_lot_stock + signed_qty
        running_product_stock += signed_qty

        if running_product_stock < 0:
            raise HistoricalStockError(
                "Le stock global du produit devient negatif dans la chronologie des mouvements."
            )

        movement_updates.append(
            {
                "id": movement_id,
                "stock_apres": running_product_stock,
            }
        )

    if movement_updates:
        stmt = text(
            f"""
            UPDATE {MOVEMENTS_TABLE}
            SET stock_apres = :stock_apres
            WHERE id = :id
            """
        )
        for row in movement_updates:
            db.execute(stmt, row)

    lot_updates = [
        {"id": lot_id, "stock_lot": stock}
        for lot_id, stock in running_by_lot.items()
    ]
    if lot_updates:
        stmt = text(
            f"""
            UPDATE {LOTS_TABLE}
            SET stock_lot = :stock_lot
            WHERE id = :id
            """
        )
        for row in lot_updates:
            db.execute(stmt, row)

    db.execute(
        text(
            f"""
            UPDATE {PRODUCTS_TABLE}
            SET stock_actuel = :stock_actuel
            WHERE code = :code_prod
            """
        ),
        {"code_prod": code_prod, "stock_actuel": running_product_stock},
    )

    db.execute(
        text(
            f"""
            DELETE FROM {DASHBOARD_TABLE}
            WHERE code_produit = :code_prod
            """
        ),
        {"code_prod": code_prod},
    )

    db.execute(
        text(
            f"""
            INSERT INTO {DASHBOARD_TABLE} (
                id_mvt_source,
                date_mvt,
                code_produit,
                lot_id,
                numero_lot,
                date_peremption,
                nom_produit,
                forme,
                dosage,
                classe,
                cible,
                unite,
                prix_achat,
                prix_vente,
                type_mouvement,
                mouvement,
                quantite,
                stock_apres,
                commentaire
            )
            SELECT
                m.id,
                m.date_mvt,
                m.code_prod,
                pl.id,
                pl.numero_lot,
                pl.date_peremption,
                p.produit,
                p.forme,
                p.dosage,
                p.classe,
                p.cible,
                p.unite,
                p.prix_achat,
                p.prix_vente,
                m.type_mvt,
                m.mouvement,
                m.quantite,
                m.stock_apres,
                m.commentaire
            FROM {MOVEMENTS_TABLE} m
            JOIN {PRODUCTS_TABLE} p
                ON p.code = m.code_prod
            JOIN {LOTS_TABLE} pl
                ON pl.id = m.lot_id
            WHERE m.code_prod = :code_prod
            ORDER BY m.date_mvt ASC, m.id ASC
            """
        ),
        {"code_prod": code_prod},
    )

    return {
        "code_prod": code_prod,
        "movements": len(movement_rows),
        "stock_actuel": running_product_stock,
    }


def recalculate_products(code_prods: Iterable[str], db: Session) -> list[dict]:
    normalized = sorted({str(code).strip() for code in code_prods if str(code).strip()})
    return [recalculate_product_history(code_prod, db) for code_prod in normalized]


def get_lot_stocks_on_date(code_prod: str, movement_date: date, db: Session) -> dict[int, int]:
    lot_rows = db.execute(
        text(
            f"""
            SELECT id, created_at, stock_lot
            FROM {LOTS_TABLE}
            WHERE code_prod = :code_prod
            ORDER BY created_at ASC, id ASC
            """
        ),
        {"code_prod": code_prod},
    ).mappings().all()

    total_delta_rows = db.execute(
        text(
            f"""
            SELECT lot_id, date_mvt, type_mvt, quantite
            FROM {MOVEMENTS_TABLE}
            WHERE code_prod = :code_prod
            ORDER BY date_mvt ASC, id ASC
            """
        ),
        {"code_prod": code_prod},
    ).mappings().all()

    running_by_lot: dict[int, int] = {}
    lot_created_at: dict[int, date | None] = {}
    total_delta_by_lot = defaultdict(int)
    first_movement_by_lot: dict[int, date] = {}

    for row in total_delta_rows:
        lot_id = int(row["lot_id"])
        total_delta_by_lot[lot_id] += _signed_quantity(str(row["type_mvt"]), row["quantite"])
        first_movement_by_lot.setdefault(lot_id, row["date_mvt"])

    for row in lot_rows:
        lot_id = int(row["id"])
        lot_created_at[lot_id] = _effective_created_at(
            row["created_at"],
            first_movement_by_lot.get(lot_id),
        )
        running_by_lot[lot_id] = int(row["stock_lot"] or 0) - total_delta_by_lot.get(lot_id, 0)

    for row in total_delta_rows:
        lot_id = int(row["lot_id"])
        current_created_at = lot_created_at.get(lot_id)
        row_date = row["date_mvt"]
        if current_created_at is not None and row_date < current_created_at:
            continue
        if row_date > movement_date:
            break
        running_by_lot[lot_id] = running_by_lot.get(lot_id, 0) + _signed_quantity(
            str(row["type_mvt"]), row["quantite"]
        )

    snapshot: dict[int, int] = {}
    for lot_id, stock in running_by_lot.items():
        created_at = lot_created_at.get(lot_id)
        if created_at is not None and created_at > movement_date:
            continue
        snapshot[lot_id] = stock

    return snapshot


def get_lot_stock_values_for_movements(
    movement_ids: Iterable[int],
    db: Session,
) -> dict[int, dict[str, int]]:
    ids = sorted({int(movement_id) for movement_id in movement_ids if movement_id is not None})
    if not ids:
        return {}

    target_rows = db.execute(
        text(
            f"""
            SELECT id, lot_id
            FROM {MOVEMENTS_TABLE}
            WHERE id IN :ids
            """
        ).bindparams(bindparam("ids", expanding=True)),
        {"ids": ids},
    ).mappings().all()

    lot_ids = sorted({int(row["lot_id"]) for row in target_rows if row["lot_id"] is not None})
    if not lot_ids:
        return {}

    lot_rows = db.execute(
        text(
            f"""
            SELECT id, stock_lot
            FROM {LOTS_TABLE}
            WHERE id IN :lot_ids
            """
        ).bindparams(bindparam("lot_ids", expanding=True)),
        {"lot_ids": lot_ids},
    ).mappings().all()

    current_stock_by_lot = {
        int(row["id"]): int(row["stock_lot"] or 0)
        for row in lot_rows
    }

    movement_rows = db.execute(
        text(
            f"""
            SELECT id, lot_id, date_mvt, type_mvt, quantite
            FROM {MOVEMENTS_TABLE}
            WHERE lot_id IN :lot_ids
            ORDER BY lot_id ASC, date_mvt ASC, id ASC
            """
        ).bindparams(bindparam("lot_ids", expanding=True)),
        {"lot_ids": lot_ids},
    ).mappings().all()

    total_delta_by_lot = defaultdict(int)
    for row in movement_rows:
        lot_id = int(row["lot_id"])
        total_delta_by_lot[lot_id] += _signed_quantity(str(row["type_mvt"]), row["quantite"])

    running_by_lot = {
        lot_id: current_stock_by_lot.get(lot_id, 0) - total_delta_by_lot.get(lot_id, 0)
        for lot_id in lot_ids
    }

    target_id_set = set(ids)
    stock_map: dict[int, dict[str, int]] = {}

    for row in movement_rows:
        lot_id = int(row["lot_id"])
        movement_id = int(row["id"])
        lot_stock_initial = running_by_lot.get(lot_id, 0)
        lot_stock_after = lot_stock_initial + _signed_quantity(str(row["type_mvt"]), row["quantite"])
        running_by_lot[lot_id] = lot_stock_after

        if movement_id in target_id_set:
            stock_map[movement_id] = {
                "stock_initial": lot_stock_initial,
                "stock_apres": lot_stock_after,
            }

    return stock_map

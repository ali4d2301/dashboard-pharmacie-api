# backend/routers/movements_edit.py
from datetime import date, datetime, timedelta
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from db import get_db

router = APIRouter(prefix="/api/movements", tags=["mouvements"])

# Nom exact de la table (backticks si elle commence par 0_)
TABLE = "`0_mouvement_stock`"  # adapte si besoin

# Valeurs autorisées (adapte selon ta DB: entree/sortie ou entrée/sortie)
ALLOWED_TYPE = {"entree", "sortie"}
ALLOWED_MVT = {"achat", "vente", "perte", "peremption", "don", "ajustement"}


# ---------- Schémas Pydantic ----------

class MovementOut(BaseModel):
    """Ce que le frontend reçoit pour afficher/éditer."""
    id: int
    date_mvt: date
    code_prod: str
    type_mvt: str
    mouvement: str
    quantite: float
    commentaire: Optional[str] = None


class MovementPatch(BaseModel):
    """
    Patch partiel (comme ton edit_products) :
    - id obligatoire
    - les autres champs sont optionnels (seulement ceux modifiés)
    """
    id: int

    # Champs modifiables uniquement
    date_mvt: Optional[date] = None
    quantite: Optional[float] = Field(default=None, ge=0)
    type_mvt: Optional[str] = None
    mouvement: Optional[str] = None
    commentaire: Optional[str] = None


class BulkUpdateResult(BaseModel):
    updated: int


# ---------- Helpers ----------

def _day_to_range(day: date) -> tuple[datetime, datetime]:
    """
    Convertit un jour (YYYY-MM-DD) en intervalle [start, end)
    pour filtrer toutes les lignes de ce jour (peu importe l'heure).
    """
    start = datetime.combine(day, datetime.min.time())
    end = start + timedelta(days=1)
    return start, end


def _validate_patch(p: MovementPatch):
    """Valide les valeurs si présentes."""
    if p.type_mvt is not None and p.type_mvt not in ALLOWED_TYPE:
        raise HTTPException(status_code=400, detail=f"type_mvt invalide: {p.type_mvt}")
    if p.mouvement is not None and p.mouvement not in ALLOWED_MVT:
        raise HTTPException(status_code=400, detail=f"mouvement invalide: {p.mouvement}")


# ---------- Routes ----------

@router.get("/edit", response_model=List[MovementOut])
def list_movements_for_edit(
    code_prod: str,
    day: date,  # ex: 2026-01-05
    db: Session = Depends(get_db),
):
    """
    Retourne tous les mouvements d'un produit sur une date donnée (jour).
    Utilisé par le frontend quand l'utilisateur saisit code + date.
    """
    start_dt, end_dt = _day_to_range(day)

    q = text(f"""
    SELECT id, date_mvt, code_prod, type_mvt, mouvement, quantite, commentaire
    FROM {TABLE}
    WHERE code_prod = :code_prod
      AND date_mvt = :day
    ORDER BY id ASC
    """)
    rows = db.execute(q, {"code_prod": code_prod, "day": day}).mappings().all()
    return [dict(r) for r in rows]


@router.put("/edit", response_model=BulkUpdateResult)
def bulk_update_movements(
    patches: List[MovementPatch],
    db: Session = Depends(get_db),
):
    """
    Met à jour en lot (bulk) comme /api/products/edit_products :
    Body: [
      { "id": 12, "quantite": 5, "commentaire": "..." },
      { "id": 13, "date_mvt": "2026-01-05T10:20", "type_mvt": "sortie" }
    ]
    Retour: { updated: N }
    """
    if not patches:
        return {"updated": 0}

    # Validation des patches (valeurs autorisées)
    for p in patches:
        _validate_patch(p)

    updated = 0

    # On fait une transaction unique : tout passe ou on rollback
    try:
        for p in patches:
            # construire SET dynamiquement (uniquement champs présents)
            sets = []
            params: Dict[str, Any] = {"id": p.id}

            if p.date_mvt is not None:
                sets.append("date_mvt = :date_mvt")
                params["date_mvt"] = p.date_mvt

            if p.quantite is not None:
                sets.append("quantite = :quantite")
                params["quantite"] = p.quantite

            if p.type_mvt is not None:
                sets.append("type_mvt = :type_mvt")
                params["type_mvt"] = p.type_mvt

            if p.mouvement is not None:
                sets.append("mouvement = :mouvement")
                params["mouvement"] = p.mouvement

            if p.commentaire is not None:
                sets.append("commentaire = :commentaire")
                params["commentaire"] = p.commentaire

            # Si aucun champ modifié (patch vide), on ignore
            if not sets:
                continue

            upd = text(f"""
                UPDATE {TABLE}
                SET {", ".join(sets)}
                WHERE id = :id
            """)

            res = db.execute(upd, params)
            # res.rowcount = nb lignes touchées (0 si id inexistant)
            if res.rowcount == 0:
                raise HTTPException(status_code=404, detail=f"Mouvement introuvable (id={p.id})")

            updated += res.rowcount

        db.commit()
        return {"updated": updated}

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erreur serveur: {str(e)}")

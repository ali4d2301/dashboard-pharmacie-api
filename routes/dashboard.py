from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from db import get_db, engine

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

@router.get("/classes")
def get_classes(db: Session = Depends(get_db)):
    sql = text("""
        SELECT DISTINCT classe
        FROM  `0_products`
        WHERE classe IS NOT NULL AND classe <> ''
        ORDER BY classe
    """)
    rows = db.execute(sql).mappings().all()
    return {"classes": [r["classe"] for r in rows]}

@router.get("/kpis")
def get_kpis(
    annee: int = Query(..., ge=2000),
    mois: int = Query(..., ge=1, le=12),
    classe: str = Query("Tout"),
    db: Session = Depends(get_db),
):
    # Tolérance: si le frontend envoie ALL, on le traite comme "Tout"
    classe_norm = "Tout" if (classe or "").strip().upper() == "ALL" else (classe or "Tout").strip()

    # Debug 0: vérifier qu'on a bien des lignes sur la période (sans filtre classe)
    sql_rows_period = text("""
        SELECT COUNT(*) AS n
        FROM tb_dashboard d
        WHERE YEAR(d.date_mvt) = :annee
          AND MONTH(d.date_mvt) = :mois
    """)
    rows_period = int(db.execute(sql_rows_period, {"annee": annee, "mois": mois}).scalar() or 0)

    # 1) Nombre de produits ayant au moins un mouvement dans la période
    sql_nb = text("""
        SELECT COUNT(DISTINCT d.code_produit) AS nb
        FROM tb_dashboard d
        WHERE YEAR(d.date_mvt) = :annee
          AND MONTH(d.date_mvt) = :mois
          AND (:classe = 'Tout' OR d.classe = :classe)
    """)
    nb_produits = int(db.execute(sql_nb, {"annee": annee, "mois": mois, "classe": classe_norm}).scalar() or 0)

    # 2) denom = produits Actif présents
    sql_denom = text("""
        SELECT COUNT(DISTINCT p.code) AS denom
        FROM `0_products` p
        WHERE p.statut = 'Actif'
        AND (:classe = 'Tout' OR p.classe = :classe)
    """)
    denom = int(db.execute(sql_denom, {"classe": classe_norm}).scalar() or 0)

    # 2) num = stock_apres > 0 après dernier mouvement du mois
    sql_num = text("""
        WITH last_mvt AS (
            SELECT d.code_produit, MAX(d.id_mvt_source) AS last_id
            FROM tb_dashboard d
            WHERE YEAR(d.date_mvt) = :annee
              AND MONTH(d.date_mvt) = :mois
              AND (:classe = 'Tout' OR d.classe = :classe)
            GROUP BY d.code_produit
        )
        SELECT COUNT(*) AS num
        FROM last_mvt lm
        JOIN tb_dashboard d ON d.id_mvt_source = lm.last_id
        JOIN `0_products` p ON p.code = lm.code_produit
        WHERE p.statut = 'Actif'
          AND (:classe = 'Tout' OR p.classe = :classe)
          AND COALESCE(d.stock_apres, 0) > 0
    """)
    num = int(db.execute(sql_num, {"annee": annee, "mois": mois, "classe": classe_norm}).scalar() or 0)

    taux_disponibilite = 0.0 if denom == 0 else (num / denom) * 100.0

    # 3) bénéfice net
    sql_profit = text("""
        SELECT
          COALESCE(SUM(
            CASE
              WHEN d.type_mouvement = 'sortie' AND d.mouvement = 'vente'
              THEN COALESCE(d.quantite,0) * COALESCE(d.prix_vente,0)
              ELSE 0
            END
          ), 0) AS total_ventes,
          COALESCE(SUM(
            CASE
              WHEN d.type_mouvement = 'entree' AND d.mouvement = 'achat'
              THEN COALESCE(d.quantite,0) * COALESCE(d.prix_achat,0)
              ELSE 0
            END
          ), 0) AS total_achats
        FROM tb_dashboard d
        WHERE YEAR(d.date_mvt) = :annee
          AND MONTH(d.date_mvt) = :mois
          AND (:classe = 'Tout' OR d.classe = :classe)
    """)
    row = db.execute(sql_profit, {"annee": annee, "mois": mois, "classe": classe_norm}).mappings().first() or {}
    total_ventes = float(row.get("total_ventes", 0) or 0)
    total_achats = float(row.get("total_achats", 0) or 0)

    benefice_net = total_ventes - total_achats

    return {
        "nb_produits": nb_produits,
        "taux_disponibilite": round(taux_disponibilite, 2),
        "benefice_net": round(benefice_net, 2),
        "debug": {
            "annee": annee,
            "mois": mois,
            "classe_recue": classe,
            "classe_norm": classe_norm,
            "rows_period": rows_period,
            "dispo_num": num,
            "dispo_denom": denom,
            "total_ventes": round(total_ventes, 2),
            "total_achats": round(total_achats, 2),
        }
    }

def norm_classe(classe: str) -> str:
    c = (classe or "").strip()
    return "Tout" if c.upper() == "ALL" or c == "" else c

@router.get("/etat_stock_share")
def etat_stock_share(
    annee: int = Query(..., ge=2000),
    mois: int = Query(..., ge=1, le=12),
    classe: str = Query("ALL"),
    db: Session = Depends(get_db),
):
    classe_norm = norm_classe(classe)

    # format "YYYY-MM" comme dans ta vue (ex: 2025-01)
    ym = f"{annee:04d}-{mois:02d}"

    sql = text("""
        SELECT
            esm.etat AS etat,
            COUNT(*) AS nb
        FROM etat_stock_mensuel esm
        JOIN `0_products` p ON p.code = esm.code_prod
        WHERE LEFT(CAST(esm.mois AS CHAR), 7) = :ym
          AND p.statut = 'Actif'
          AND (:classe = 'Tout' OR p.classe = :classe)
        GROUP BY esm.etat
        ORDER BY nb DESC
    """)

    rows = db.execute(sql, {"ym": ym, "classe": classe_norm}).mappings().all()

    items = [{"name": r["etat"] or "Non défini", "value": int(r["nb"] or 0)} for r in rows]
    total = sum(i["value"] for i in items)

    return {"ym": ym, "classe": classe_norm, "total": total, "items": items}

@router.get("/movement_hist")
def movement_hist(
    annee: int = Query(..., ge=2000),
    mois: int = Query(..., ge=1, le=12),
    classe: str = Query("ALL"),
    db: Session = Depends(get_db),
):
    classe_norm = norm_classe(classe)

    sql = text("""
        SELECT
            d.mouvement AS mouvement,
            d.type_mouvement AS type_mouvement,
            COUNT(*) AS nb
        FROM tb_dashboard d
        JOIN `0_products` p ON p.code = d.code_produit
        WHERE YEAR(d.date_mvt) = :annee
          AND MONTH(d.date_mvt) = :mois
          AND p.statut = 'Actif'
          AND (:classe = 'Tout' OR p.classe = :classe)
          AND d.mouvement IS NOT NULL AND d.mouvement <> ''
          AND d.type_mouvement IN ('entree','sortie')
        GROUP BY d.mouvement, d.type_mouvement
        ORDER BY d.mouvement, d.type_mouvement
    """)

    rows = db.execute(sql, {"annee": annee, "mois": mois, "classe": classe_norm}).mappings().all()

    # Format simple pour le front:
    # items: [{mouvement:'achat', type:'entree', value: 12}, ...]
    items = [
        {
            "mouvement": r["mouvement"],
            "type": r["type_mouvement"],
            "value": int(r["nb"] or 0),
        }
        for r in rows
    ]

    return {"items": items}

# Requête pour avoir le tableau synthétique
SQL_TABLEAU_MENSUEL = text("""
WITH params AS (
  SELECT
    CONCAT(:annee, '-', LPAD(:mois,2,'0')) AS ym,
    DATE_FORMAT(
      DATE_SUB(STR_TO_DATE(CONCAT(:annee,'-',LPAD(:mois,2,'0'),'-01'), '%Y-%m-%d'), INTERVAL 1 MONTH),
      '%Y-%m'
    ) AS ym_prec
)

SELECT
  p.produit AS produit,
  p.dosage      AS dosage,
  p.forme       AS forme,
  p.unite       AS unite,
  p.cible       AS cible,                         

  prev.stock    AS quantite_initiale,
  COALESCE(mv.qte_entree, 0) AS quantite_entree,
  COALESCE(mv.qte_sortie, 0) AS quantite_sortie,

  cur.stock     AS sdu,
  cur.cmm       AS cmm,
  cur.etat      AS etat_stock

FROM etat_stock_mensuel cur
JOIN params pr
  ON LEFT(CAST(cur.mois AS CHAR), 7) = pr.ym
  OR CAST(cur.mois AS CHAR) = pr.ym

JOIN `0_products` p
  ON p.code = cur.code_prod

LEFT JOIN etat_stock_mensuel prev
  ON prev.code_prod = cur.code_prod
 AND (
      LEFT(CAST(prev.mois AS CHAR), 7) = pr.ym_prec
      OR CAST(prev.mois AS CHAR) = pr.ym_prec
 )

LEFT JOIN (
  SELECT
    d.code_produit,
    SUM(CASE WHEN d.type_mouvement='entree' THEN d.quantite ELSE 0 END) AS qte_entree,
    SUM(CASE WHEN d.type_mouvement='sortie' THEN d.quantite ELSE 0 END) AS qte_sortie
  FROM tb_dashboard d
  WHERE YEAR(d.date_mvt) = :annee
    AND MONTH(d.date_mvt) = :mois
  GROUP BY d.code_produit
) mv
  ON mv.code_produit = cur.code_prod

WHERE (:classe = 'Tout' OR p.classe = :classe)

ORDER BY produit ASC;
""")


@router.get("/tableau_mensuel")
def tableau_mensuel(
    annee: int = Query(..., ge=2000),
    mois: int = Query(..., ge=1, le=12),
    classe: str = Query("ALL"),
    db: Session = Depends(get_db),
):
    classe_norm = norm_classe(classe)  # même logique que les autres endpoints :contentReference[oaicite:4]{index=4}

    rows = db.execute(SQL_TABLEAU_MENSUEL, {
        "annee": annee,
        "mois": mois,
        "classe": classe_norm,
    }).mappings().all()

    return {"data": [dict(r) for r in rows]}

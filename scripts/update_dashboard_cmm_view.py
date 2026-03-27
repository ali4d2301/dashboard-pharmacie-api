from pathlib import Path
import re

import pymysql
from sqlalchemy.engine import make_url


VIEW_SQL = """
CREATE OR REPLACE VIEW `etat_stock_mensuel` AS
SELECT
  DATE_FORMAT(y.mois, '%Y-%m') AS mois,
  y.code_prod AS code_prod,
  y.cmm AS cmm,
  y.stock_fin AS stock,
  CASE
    WHEN y.cmm IS NULL OR y.cmm = 0 THEN NULL
    ELSE ROUND(y.stock_fin / y.cmm, 2)
  END AS msd,
  CASE
    WHEN y.stock_fin IS NULL THEN NULL
    WHEN y.stock_fin = 0 THEN 'Rupture'
    WHEN y.cmm = 0 AND y.stock_fin > 0 THEN 'Stock dormant'
    WHEN (y.stock_fin / y.cmm) < 2 THEN 'Sous-stock'
    WHEN (y.stock_fin / y.cmm) BETWEEN 2 AND 4 THEN 'Bon stock'
    WHEN (y.stock_fin / y.cmm) > 4 THEN 'Sur-stock'
    ELSE NULL
  END AS etat
FROM (
  SELECT
    x.mois,
    x.code_prod,
    x.stock_fin,
    CASE
      WHEN x.stock_fin IS NULL THEN NULL
      WHEN x.cnt_prev = 0 THEN 0
      WHEN x.cnt_prev < 3 THEN x.sum_prev / x.cnt_prev
      ELSE x.sum_prev / 3
    END AS cmm
  FROM (
    SELECT
      mp.mois AS mois,
      mp.code_prod AS code_prod,
      (
        SELECT t.stock_apres
        FROM `tb_dashboard` t
        WHERE t.code_produit = mp.code_prod
          AND t.date_mvt < (mp.mois + INTERVAL 1 MONTH)
        ORDER BY t.date_mvt DESC, t.id_mvt_source DESC
        LIMIT 1
      ) AS stock_fin,
      (
        SELECT COALESCE(SUM(s.sorties_mois), 0)
        FROM (
          SELECT
            DATE_FORMAT(d.date_mvt, '%Y-%m-01') AS mois_sortie,
            d.code_produit AS code_prod,
            SUM(d.quantite) AS sorties_mois
          FROM `tb_dashboard` d
          WHERE d.type_mouvement = 'sortie'
          GROUP BY DATE_FORMAT(d.date_mvt, '%Y-%m-01'), d.code_produit
        ) s
        WHERE s.code_prod = mp.code_prod
          AND s.mois_sortie >= (mp.mois - INTERVAL 3 MONTH)
          AND s.mois_sortie < mp.mois
      ) AS sum_prev,
      (
        SELECT COUNT(0)
        FROM (
          SELECT DISTINCT DATE_FORMAT(d.date_mvt, '%Y-%m-01') AS mois_any
          FROM `tb_dashboard` d
          WHERE d.code_produit = mp.code_prod
        ) hist
        WHERE hist.mois_any >= (mp.mois - INTERVAL 3 MONTH)
          AND hist.mois_any < mp.mois
      ) AS cnt_prev
    FROM (
      SELECT DISTINCT
        DATE_FORMAT(d.date_mvt, '%Y-%m-01') AS mois,
        d.code_produit AS code_prod
      FROM `tb_dashboard` d
    ) mp
  ) x
) y
WHERE y.stock_fin IS NOT NULL
"""


def load_database_url() -> str:
    env_text = Path(__file__).resolve().parents[1].joinpath(".env").read_text(encoding="utf-8")
    match = re.search(r'^DATABASE_URL="([^"]+)"', env_text, re.M)
    if not match:
        raise RuntimeError("DATABASE_URL introuvable dans backend/.env")
    return match.group(1)


def main() -> None:
    url = make_url(load_database_url())
    conn = pymysql.connect(
        host=url.host,
        port=url.port or 3306,
        user=url.username,
        password=url.password,
        database=url.database,
        autocommit=False,
        connect_timeout=15,
        read_timeout=30,
        write_timeout=30,
    )

    try:
        with conn.cursor() as cur:
            cur.execute(VIEW_SQL)
        conn.commit()
        print("Vue etat_stock_mensuel mise a jour: cnt_prev = 0 -> CMM = 0.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()

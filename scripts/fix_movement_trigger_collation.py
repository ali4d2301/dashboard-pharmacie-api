from pathlib import Path
import re

import pymysql
from sqlalchemy.engine import make_url


TRIGGER_CALC_STOCK_SQL = """
CREATE TRIGGER `trg_mvt_calc_stock`
BEFORE INSERT ON `0_mouvement_stock`
FOR EACH ROW
BEGIN
    DECLARE v_product_stock INT DEFAULT 0;
    DECLARE v_lot_stock INT DEFAULT 0;
    DECLARE v_lot_code VARCHAR(50)
        CHARACTER SET utf8mb4
        COLLATE utf8mb4_unicode_ci;

    SELECT code_prod, stock_lot
    INTO v_lot_code, v_lot_stock
    FROM `product_lots`
    WHERE id = NEW.lot_id
    FOR UPDATE;

    IF v_lot_code IS NULL THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Lot introuvable';
    END IF;

    IF v_lot_code COLLATE utf8mb4_unicode_ci <> NEW.code_prod COLLATE utf8mb4_unicode_ci THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Le lot ne correspond pas au produit';
    END IF;

    SELECT COALESCE(stock_actuel, 0)
    INTO v_product_stock
    FROM `0_products`
    WHERE code = NEW.code_prod
    FOR UPDATE;

    IF NEW.type_mvt = 'entree' THEN
        SET NEW.stock_apres = v_product_stock + NEW.quantite;
    ELSE
        IF v_lot_stock < NEW.quantite THEN
            SIGNAL SQLSTATE '45000'
                SET MESSAGE_TEXT = 'Stock insuffisant pour ce lot';
        END IF;
        SET NEW.stock_apres = v_product_stock - NEW.quantite;
    END IF;
END
"""

TRIGGER_TO_DASHBOARD_SQL = """
CREATE TRIGGER `trg_mvt_to_dashboard`
AFTER INSERT ON `0_mouvement_stock`
FOR EACH ROW
BEGIN
    IF NEW.type_mvt = 'entree' THEN
        UPDATE `product_lots`
        SET stock_lot = stock_lot + NEW.quantite
        WHERE id = NEW.lot_id;
    ELSE
        UPDATE `product_lots`
        SET stock_lot = stock_lot - NEW.quantite
        WHERE id = NEW.lot_id;
    END IF;

    UPDATE `0_products`
    SET stock_actuel = (
        SELECT COALESCE(SUM(stock_lot), 0)
        FROM `product_lots`
        WHERE code_prod = NEW.code_prod
    )
    WHERE code = NEW.code_prod;

    INSERT INTO `tb_dashboard` (
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
        NEW.id,
        NEW.date_mvt,
        NEW.code_prod,
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
        NEW.type_mvt,
        NEW.mouvement,
        NEW.quantite,
        NEW.stock_apres,
        NEW.commentaire
    FROM `0_products` p
    JOIN `product_lots` pl
        ON pl.id = NEW.lot_id
    WHERE p.code = NEW.code_prod;
END
"""


def load_database_url() -> str:
    env_text = Path(__file__).resolve().parents[1].joinpath(".env").read_text(encoding="utf-8")
    match = re.search(r'^DATABASE_URL="([^"]+)"', env_text, re.M)
    if not match:
        raise RuntimeError("DATABASE_URL introuvable dans backend/.env")
    return match.group(1)


def trigger_exists(cur, trigger_name: str) -> bool:
    cur.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.triggers
        WHERE trigger_schema = DATABASE()
          AND trigger_name = %s
        """,
        (trigger_name,),
    )
    return bool(cur.fetchone()[0])


def recreate_trigger(cur, trigger_name: str, sql: str) -> None:
    if trigger_exists(cur, trigger_name):
        print(f"[drop] {trigger_name}")
        cur.execute(f"DROP TRIGGER `{trigger_name}`")
    print(f"[create] {trigger_name}")
    cur.execute(sql)


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
        cur = conn.cursor()
        recreate_trigger(cur, "trg_mvt_calc_stock", TRIGGER_CALC_STOCK_SQL)
        recreate_trigger(cur, "trg_mvt_to_dashboard", TRIGGER_TO_DASHBOARD_SQL)
        conn.commit()
        print("Triggers recrees avec collation explicite.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()

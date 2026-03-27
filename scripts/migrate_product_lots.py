from pathlib import Path
import re

import pymysql
from sqlalchemy.engine import make_url


def load_database_url() -> str:
    env_text = Path(__file__).resolve().parents[1].joinpath(".env").read_text(encoding="utf-8")
    match = re.search(r'^DATABASE_URL="([^"]+)"', env_text, re.M)
    if not match:
        raise RuntimeError("DATABASE_URL introuvable dans backend/.env")
    return match.group(1)


def table_exists(cur, table_name: str) -> bool:
    cur.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.tables
        WHERE table_schema = DATABASE() AND table_name = %s
        """,
        (table_name,),
    )
    return bool(cur.fetchone()[0])


def column_exists(cur, table_name: str, column_name: str) -> bool:
    cur.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = %s
          AND column_name = %s
        """,
        (table_name, column_name),
    )
    return bool(cur.fetchone()[0])


def constraint_exists(cur, table_name: str, constraint_name: str) -> bool:
    cur.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.table_constraints
        WHERE table_schema = DATABASE()
          AND table_name = %s
          AND constraint_name = %s
        """,
        (table_name, constraint_name),
    )
    return bool(cur.fetchone()[0])


def index_exists(cur, table_name: str, index_name: str) -> bool:
    cur.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = %s
          AND index_name = %s
        """,
        (table_name, index_name),
    )
    return bool(cur.fetchone()[0])


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
        read_timeout=15,
        write_timeout=15,
    )

    try:
        cur = conn.cursor()
        has_legacy_numero_lot = column_exists(cur, "0_products", "numero_lot")
        has_legacy_date_peremption = column_exists(cur, "0_products", "date_peremption")

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS `product_lots` (
                `id` BIGINT NOT NULL AUTO_INCREMENT,
                `code_prod` VARCHAR(50) COLLATE utf8mb4_unicode_ci NOT NULL,
                `numero_lot` VARCHAR(100) COLLATE utf8mb4_unicode_ci NOT NULL,
                `date_peremption` DATE NOT NULL,
                `stock_lot` INT NOT NULL DEFAULT 0,
                `created_at` DATE DEFAULT NULL,
                PRIMARY KEY (`id`),
                UNIQUE KEY `uq_product_lots_code_lot_exp` (`code_prod`, `numero_lot`, `date_peremption`),
                KEY `idx_product_lots_code` (`code_prod`),
                KEY `idx_product_lots_peremption` (`date_peremption`),
                CONSTRAINT `fk_product_lots_product`
                    FOREIGN KEY (`code_prod`) REFERENCES `0_products` (`code`)
                    ON DELETE RESTRICT ON UPDATE CASCADE
            )
            ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )

        numero_lot_expr = (
            "COALESCE(NULLIF(TRIM(p.numero_lot), ''), CONCAT('LOT-', p.code, '-MIGRATION'))"
            if has_legacy_numero_lot
            else "CONCAT('LOT-', p.code, '-MIGRATION')"
        )
        date_peremption_expr = (
            "COALESCE(p.date_peremption, DATE('2026-12-31'))"
            if has_legacy_date_peremption
            else "DATE('2026-12-31')"
        )

        cur.execute(
            f"""
            INSERT INTO `product_lots` (code_prod, numero_lot, date_peremption, stock_lot, created_at)
            SELECT
                p.code,
                {numero_lot_expr},
                {date_peremption_expr},
                COALESCE(p.stock_actuel, 0),
                COALESCE(p.date_creation, CURDATE())
            FROM `0_products` p
            WHERE NOT EXISTS (
                SELECT 1
                FROM `product_lots` pl
                WHERE pl.code_prod = p.code
            )
            """
        )

        if column_exists(cur, "0_mouvement_stock", "lot_id") is False:
            cur.execute("ALTER TABLE `0_mouvement_stock` ADD COLUMN `lot_id` BIGINT NULL AFTER `code_prod`")

        cur.execute(
            """
            UPDATE `0_mouvement_stock` m
            JOIN (
                SELECT code_prod, MIN(id) AS lot_id
                FROM `product_lots`
                GROUP BY code_prod
            ) pl
                ON pl.code_prod = m.code_prod
            SET m.lot_id = pl.lot_id
            WHERE m.lot_id IS NULL
            """
        )

        if index_exists(cur, "0_mouvement_stock", "idx_mvt_lot") is False:
            cur.execute("ALTER TABLE `0_mouvement_stock` ADD KEY `idx_mvt_lot` (`lot_id`)")

        if constraint_exists(cur, "0_mouvement_stock", "fk_mvt_lot") is False:
            cur.execute(
                """
                ALTER TABLE `0_mouvement_stock`
                ADD CONSTRAINT `fk_mvt_lot`
                FOREIGN KEY (`lot_id`) REFERENCES `product_lots` (`id`)
                ON DELETE RESTRICT ON UPDATE CASCADE
                """
            )

        cur.execute("SELECT COUNT(*) FROM `0_mouvement_stock` WHERE lot_id IS NULL")
        null_lot_count = int(cur.fetchone()[0] or 0)
        if null_lot_count == 0:
            cur.execute("ALTER TABLE `0_mouvement_stock` MODIFY COLUMN `lot_id` BIGINT NOT NULL")

        if column_exists(cur, "tb_dashboard", "lot_id") is False:
            cur.execute("ALTER TABLE `tb_dashboard` ADD COLUMN `lot_id` BIGINT NULL AFTER `code_produit`")
        if column_exists(cur, "tb_dashboard", "numero_lot") is False:
            cur.execute("ALTER TABLE `tb_dashboard` ADD COLUMN `numero_lot` VARCHAR(100) NULL AFTER `lot_id`")
        if column_exists(cur, "tb_dashboard", "date_peremption") is False:
            cur.execute("ALTER TABLE `tb_dashboard` ADD COLUMN `date_peremption` DATE NULL AFTER `numero_lot`")

        if index_exists(cur, "tb_dashboard", "idx_dashboard_lot") is False:
            cur.execute("ALTER TABLE `tb_dashboard` ADD KEY `idx_dashboard_lot` (`lot_id`)")

        cur.execute(
            """
            UPDATE `tb_dashboard` d
            JOIN `0_mouvement_stock` m
                ON m.id = d.id_mvt_source
            JOIN `product_lots` pl
                ON pl.id = m.lot_id
            SET
                d.lot_id = pl.id,
                d.numero_lot = pl.numero_lot,
                d.date_peremption = pl.date_peremption
            WHERE d.lot_id IS NULL
               OR d.numero_lot IS NULL
               OR d.date_peremption IS NULL
            """
        )

        if trigger_exists(cur, "trg_0_products_require_fields_insert"):
            cur.execute("DROP TRIGGER `trg_0_products_require_fields_insert`")
        if trigger_exists(cur, "trg_0_products_require_fields_update"):
            cur.execute("DROP TRIGGER `trg_0_products_require_fields_update`")

        legacy_null_fields = []
        if has_legacy_numero_lot:
            legacy_null_fields.append("numero_lot = NULL")
        if has_legacy_date_peremption:
            legacy_null_fields.append("date_peremption = NULL")

        if legacy_null_fields:
            cur.execute(
                f"""
                UPDATE `0_products`
                SET {", ".join(legacy_null_fields)}
                """
            )

        if trigger_exists(cur, "trg_mvt_calc_stock"):
            cur.execute("DROP TRIGGER `trg_mvt_calc_stock`")
        if trigger_exists(cur, "trg_mvt_to_dashboard"):
            cur.execute("DROP TRIGGER `trg_mvt_to_dashboard`")

        cur.execute(
            """
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
        )

        cur.execute(
            """
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
        )

        conn.commit()

        cur.execute("SELECT COUNT(*) FROM `product_lots`")
        lots_count = int(cur.fetchone()[0] or 0)
        cur.execute("SELECT COUNT(*) FROM `0_mouvement_stock` WHERE lot_id IS NULL")
        missing_movement_lots = int(cur.fetchone()[0] or 0)
        cur.execute("SELECT COUNT(*) FROM `tb_dashboard` WHERE lot_id IS NULL")
        missing_dashboard_lots = int(cur.fetchone()[0] or 0)

        print(f"product_lots={lots_count}")
        print(f"movement_lot_null={missing_movement_lots}")
        print(f"dashboard_lot_null={missing_dashboard_lots}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

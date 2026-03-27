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


def table_type(cur, table_name: str) -> str | None:
    cur.execute(
        """
        SELECT table_type
        FROM information_schema.tables
        WHERE table_schema = DATABASE()
          AND table_name = %s
        LIMIT 1
        """,
        (table_name,),
    )
    row = cur.fetchone()
    return str(row[0]) if row else None


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


def ensure_index(cur, table_name: str, index_name: str, ddl: str) -> None:
    if not table_exists(cur, table_name):
        print(f"[skip] table absente: {table_name}")
        return
    current_table_type = table_type(cur, table_name)
    if current_table_type != "BASE TABLE":
        print(f"[skip] index impossible sur {table_name} ({current_table_type})")
        return
    if index_exists(cur, table_name, index_name):
        print(f"[ok] index deja present: {table_name}.{index_name}")
        return
    print(f"[add] creation index {table_name}.{index_name}")
    cur.execute(ddl)


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
        ensure_index(
            cur,
            "tb_dashboard",
            "idx_tb_dashboard_code_date_source",
            """
            ALTER TABLE `tb_dashboard`
            ADD INDEX `idx_tb_dashboard_code_date_source` (`code_produit`, `date_mvt`, `id_mvt_source`)
            """,
        )
        ensure_index(
            cur,
            "tb_dashboard",
            "idx_tb_dashboard_type_code_date",
            """
            ALTER TABLE `tb_dashboard`
            ADD INDEX `idx_tb_dashboard_type_code_date` (`type_mouvement`, `code_produit`, `date_mvt`)
            """,
        )
        ensure_index(
            cur,
            "product_lots",
            "idx_product_lots_code_peremption_stock",
            """
            ALTER TABLE `product_lots`
            ADD INDEX `idx_product_lots_code_peremption_stock` (`code_prod`, `date_peremption`, `stock_lot`)
            """,
        )
        conn.commit()
        print("Indexes dashboard appliques avec succes.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()

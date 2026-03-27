from pathlib import Path
import re

import pymysql
from sqlalchemy.engine import make_url


TEMP_ENUM_SQL = """
ENUM(
    'achat',
    'don',
    'acquision',
    'vente',
    'dispensation',
    'perte',
    'peremption',
    'ajustement',
    'ajustement positif',
    'ajustement negatif'
)
"""

FINAL_ENUM_SQL = """
ENUM(
    'acquision',
    'dispensation',
    'perte',
    'peremption',
    'ajustement',
    'ajustement positif',
    'ajustement negatif'
)
"""

TARGET_TABLES = ("0_mouvement_stock", "tb_dashboard")


def load_database_url() -> str:
    env_text = Path(__file__).resolve().parents[1].joinpath(".env").read_text(encoding="utf-8")
    match = re.search(r'^DATABASE_URL="([^"]+)"', env_text, re.M)
    if not match:
        raise RuntimeError("DATABASE_URL introuvable dans backend/.env")
    return match.group(1)


def show_counts(cur, title: str) -> None:
    print(title)
    for table_name in TARGET_TABLES:
        cur.execute(
            f"""
            SELECT mouvement, COUNT(*) AS nb
            FROM `{table_name}`
            GROUP BY mouvement
            ORDER BY nb DESC, mouvement ASC
            """
        )
        print(f"[{table_name}]")
        for mouvement, nb in cur.fetchall():
            print(f"  - {mouvement}: {nb}")


def widen_enum(cur, table_name: str) -> None:
    cur.execute(
        f"""
        ALTER TABLE `{table_name}`
        MODIFY COLUMN `mouvement` {TEMP_ENUM_SQL} NOT NULL
        """
    )


def narrow_enum(cur, table_name: str) -> None:
    cur.execute(
        f"""
        ALTER TABLE `{table_name}`
        MODIFY COLUMN `mouvement` {FINAL_ENUM_SQL} NOT NULL
        """
    )


def rename_values(cur, table_name: str) -> None:
    cur.execute(
        f"""
        UPDATE `{table_name}`
        SET mouvement = 'acquision'
        WHERE mouvement IN ('achat', 'don')
        """
    )
    print(f"[{table_name}] achat/don -> acquision: {cur.rowcount}")

    cur.execute(
        f"""
        UPDATE `{table_name}`
        SET mouvement = 'dispensation'
        WHERE mouvement = 'vente'
        """
    )
    print(f"[{table_name}] vente -> dispensation: {cur.rowcount}")


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
            show_counts(cur, "Avant migration")

            for table_name in TARGET_TABLES:
                widen_enum(cur, table_name)

            for table_name in TARGET_TABLES:
                rename_values(cur, table_name)

            for table_name in TARGET_TABLES:
                narrow_enum(cur, table_name)

            show_counts(cur, "Apres migration")

        conn.commit()
        print("Renommage des mouvements applique avec succes.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()

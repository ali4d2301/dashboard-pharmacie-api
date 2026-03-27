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
        columns_to_drop = [
            column_name
            for column_name in ("numero_lot", "date_peremption")
            if column_exists(cur, "0_products", column_name)
        ]

        print(f"columns_found={','.join(columns_to_drop) if columns_to_drop else 'none'}")

        if columns_to_drop:
            drop_clause = ", ".join(f"DROP COLUMN `{column_name}`" for column_name in columns_to_drop)
            cur.execute(f"ALTER TABLE `0_products` {drop_clause}")
            conn.commit()

        remaining = {
            column_name: column_exists(cur, "0_products", column_name)
            for column_name in ("numero_lot", "date_peremption")
        }
        print(f"numero_lot_exists={int(remaining['numero_lot'])}")
        print(f"date_peremption_exists={int(remaining['date_peremption'])}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

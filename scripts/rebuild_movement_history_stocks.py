import sys
from pathlib import Path

from sqlalchemy import text

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from db import SessionLocal  # noqa: E402
from movement_recalc import HistoricalStockError, recalculate_product_history  # noqa: E402


def main() -> None:
    requested_codes = [code.strip() for code in sys.argv[1:] if code.strip()]
    db = SessionLocal()
    try:
        if requested_codes:
            code_rows = requested_codes
        else:
            code_rows = db.execute(
                text(
                    """
                    SELECT DISTINCT code_prod
                    FROM `0_mouvement_stock`
                    WHERE code_prod IS NOT NULL AND code_prod <> ''
                    ORDER BY code_prod
                    """
                )
            ).scalars().all()

        db.close()

        results = []
        total = len(code_rows)
        for index, code_prod in enumerate(code_rows, start=1):
            product_db = SessionLocal()
            try:
                item = recalculate_product_history(code_prod, product_db)
                product_db.commit()
                results.append(item)
                if index <= 10 or index % 25 == 0 or index == total:
                    print(
                        f"[{index}/{total}] {item['code_prod']}: "
                        f"{item['movements']} mouvement(s), stock actuel={item['stock_actuel']}"
                    )
            except HistoricalStockError:
                product_db.rollback()
                raise
            except Exception:
                product_db.rollback()
                raise
            finally:
                product_db.close()

        print(f"Recalcul termine pour {len(results)} produit(s).")
    except HistoricalStockError as exc:
        raise RuntimeError(f"Recalcul interrompu: {exc}") from exc
    except Exception:
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()

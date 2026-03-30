from __future__ import annotations

import argparse
import secrets
import string
import sys
from pathlib import Path

from sqlalchemy import text

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from db import SessionLocal
from security import hash_password

ALLOWED_ROLES = ("admin", "viewer")
PASSWORD_ALPHABET = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
SUPPORTED_INSERT_COLUMNS = {"username", "password_hash", "role", "is_active"}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Outil d'administration pour lister et creer des acces utilisateurs.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create", help="Cree un acces utilisateur.")
    create_parser.add_argument("username", help="Nom d'utilisateur unique.")
    create_parser.add_argument("role", choices=ALLOWED_ROLES, help="Role a attribuer.")
    password_group = create_parser.add_mutually_exclusive_group()
    password_group.add_argument(
        "--password",
        help="Mot de passe en clair. Si absent, un mot de passe provisoire est genere.",
    )
    password_group.add_argument(
        "--password-stdin",
        action="store_true",
        help="Lit le mot de passe depuis l'entree standard.",
    )
    create_parser.add_argument(
        "--inactive",
        action="store_true",
        help="Cree le compte en inactif.",
    )

    list_parser = subparsers.add_parser("list", help="Liste les acces existants.")
    list_parser.add_argument(
        "--include-inactive",
        action="store_true",
        help="Inclut aussi les comptes inactifs.",
    )

    return parser


def _normalize_username(value: str) -> str:
    username = (value or "").strip()
    if not username:
        raise ValueError("Le nom d'utilisateur est obligatoire.")
    return username


def _generate_password(length: int = 16) -> str:
    if length < 12:
        raise ValueError("La longueur minimale du mot de passe genere est 12.")
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))


def _resolve_password(args: argparse.Namespace) -> tuple[str, bool]:
    if args.password_stdin:
        password = sys.stdin.read().strip()
        if not password:
            raise ValueError("Le mot de passe lu depuis stdin est vide.")
        return password, False

    if args.password:
        return args.password, False

    return _generate_password(), True


def _validate_users_schema(db) -> list[str]:
    rows = db.execute(
        text(
            """
            SELECT
                column_name AS column_name,
                is_nullable AS is_nullable,
                column_default AS column_default,
                extra AS column_extra
            FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name = 'users'
            ORDER BY ordinal_position
            """
        )
    ).mappings().all()

    if not rows:
        raise RuntimeError("La table users est introuvable.")

    required_columns = [
        str(row["column_name"])
        for row in rows
        if "auto_increment" not in str(row["column_extra"] or "").lower()
        and str(row["is_nullable"]) == "NO"
        and row["column_default"] is None
    ]

    missing_supported_columns = [
        column_name
        for column_name in ("username", "password_hash", "role", "is_active")
        if column_name not in {str(row["column_name"]) for row in rows}
    ]
    if missing_supported_columns:
        raise RuntimeError(
            "La table users ne contient pas toutes les colonnes attendues: "
            + ", ".join(missing_supported_columns)
        )

    unsupported_required_columns = [
        column_name
        for column_name in required_columns
        if column_name not in SUPPORTED_INSERT_COLUMNS
    ]
    if unsupported_required_columns:
        raise RuntimeError(
            "La table users exige des colonnes supplementaires non gerees par ce script: "
            + ", ".join(unsupported_required_columns)
        )

    return [str(row["column_name"]) for row in rows]


def _create_user(args: argparse.Namespace) -> int:
    username = _normalize_username(args.username)
    password, generated = _resolve_password(args)
    is_active = not args.inactive

    with SessionLocal() as db:
        existing_columns = _validate_users_schema(db)

        existing_user = db.execute(
            text(
                """
                SELECT id, username, role, is_active
                FROM users
                WHERE username = :username
                LIMIT 1
                """
            ),
            {"username": username},
        ).mappings().first()
        if existing_user:
            print(
                "Erreur: un utilisateur avec ce nom existe deja "
                f"(id={existing_user['id']}, role={existing_user['role']}, actif={int(bool(existing_user['is_active']))}).",
                file=sys.stderr,
            )
            return 1

        insertable_columns = [
            column_name
            for column_name in ("username", "password_hash", "role", "is_active")
            if column_name in existing_columns
        ]
        params = {
            "username": username,
            "password_hash": hash_password(password),
            "role": args.role,
            "is_active": is_active,
        }

        result = db.execute(
            text(
                f"""
                INSERT INTO users ({", ".join(insertable_columns)})
                VALUES ({", ".join(f":{column_name}" for column_name in insertable_columns)})
                """
            ),
            {column_name: params[column_name] for column_name in insertable_columns},
        )
        db.commit()

    print(f"id={result.lastrowid}")
    print(f"username={username}")
    print(f"role={args.role}")
    print(f"is_active={int(is_active)}")
    if generated:
        print(f"generated_password={password}")
    else:
        print("password=provided")
    return 0


def _list_users(args: argparse.Namespace) -> int:
    with SessionLocal() as db:
        _validate_users_schema(db)
        query = """
            SELECT id, username, role, is_active
            FROM users
        """
        params: dict[str, object] = {}
        if not args.include_inactive:
            query += " WHERE is_active = :is_active"
            params["is_active"] = True
        query += " ORDER BY username ASC, id ASC"

        rows = db.execute(text(query), params).mappings().all()

    if not rows:
        print("Aucun utilisateur trouve.")
        return 0

    for row in rows:
        print(
            f"id={row['id']};username={row['username']};role={row['role']};is_active={int(bool(row['is_active']))}"
        )
    return 0


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    try:
        if args.command == "create":
            return _create_user(args)
        if args.command == "list":
            return _list_users(args)
    except ValueError as exc:
        print(f"Erreur: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Erreur: {exc}", file=sys.stderr)
        return 1

    parser.error("Commande inconnue.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

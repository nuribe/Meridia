"""Siembra el schema demo (tienda_demo) en cualquier PostgreSQL existente,
sin necesitar psql ni Docker. Usa la misma dependencia psycopg del sidecar.

Uso (desde sidecar/ con el venv activo):
    python scripts/seed_demo.py --host HOST --port 5432 --user USUARIO --dbname MI_BD
    (la contraseña se pide de forma interactiva, nunca por argumento)

Crea los schemas `ventas` e `inventario` con tablas de ejemplo. Es idempotente:
si los schemas ya existen, aborta sin tocar nada salvo que pases --force.
"""
from __future__ import annotations

import argparse
import getpass
import pathlib
import sys

import psycopg

SQL_FILE = pathlib.Path(__file__).resolve().parents[2] / "db" / "init" / "01-schema.sql"


def main() -> int:
    parser = argparse.ArgumentParser(description="Siembra el schema demo de pg-diagrammer")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--user", required=True)
    parser.add_argument("--dbname", required=True)
    parser.add_argument("--sslmode", default="prefer")
    parser.add_argument("--force", action="store_true", help="Re-crea los schemas si ya existen (DROP CASCADE)")
    args = parser.parse_args()

    password = getpass.getpass(f"Contraseña para {args.user}@{args.host}: ")
    sql = SQL_FILE.read_text(encoding="utf-8")

    with psycopg.connect(
        host=args.host, port=args.port, user=args.user, password=password,
        dbname=args.dbname, sslmode=args.sslmode, connect_timeout=8,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM pg_namespace WHERE nspname IN ('ventas','inventario')"
            )
            existing = cur.fetchone()[0]
            if existing and not args.force:
                print("Los schemas ventas/inventario ya existen. Usa --force para re-crearlos.")
                return 1
            if existing:
                cur.execute("DROP SCHEMA IF EXISTS ventas CASCADE")
                cur.execute("DROP SCHEMA IF EXISTS inventario CASCADE")
            cur.execute(sql)
        conn.commit()

    print(f"Schema demo creado en {args.dbname} ({args.host}). Schemas: ventas, inventario.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

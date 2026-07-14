"""Benchmark de la meta del brief: introspección de 500 tablas en < 15 s.

Requiere PG_TEST_DSN (igual que la integración); crea un schema `perf500`
con 500 tablas encadenadas por FK y mide la introspección completa.
"""
import os
import time

import pytest

psycopg = pytest.importorskip("psycopg")

DSN = os.environ.get("PG_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="PG_TEST_DSN no definido")

N_TABLES = 500


@pytest.fixture(scope="module")
def perf_schema():
    ddl = ["DROP SCHEMA IF EXISTS perf500 CASCADE", "CREATE SCHEMA perf500"]
    for i in range(N_TABLES):
        fk = (
            f", padre_id integer REFERENCES perf500.t{i - 1} (id)" if i > 0 else ""
        )
        ddl.append(
            f"CREATE TABLE perf500.t{i} ("
            f"id serial PRIMARY KEY, nombre text NOT NULL, valor numeric(10,2), "
            f"activo boolean DEFAULT true, creado timestamptz DEFAULT now(){fk})"
        )
    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("; ".join(ddl))
        conn.commit()
    yield DSN
    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA IF EXISTS perf500 CASCADE")
        conn.commit()


def test_introspeccion_500_tablas_bajo_15s(perf_schema):
    from pg_diagrammer.introspection.introspector import introspect

    t0 = time.perf_counter()
    snap = introspect(perf_schema, "perf")
    elapsed = time.perf_counter() - t0

    perf_tables = [k for k in snap.tables if k.startswith("perf500.")]
    assert len(perf_tables) == N_TABLES
    fks = [r for r in snap.relationships if r.source.startswith("perf500.")]
    assert len(fks) == N_TABLES - 1  # cadena de FKs completa (meta >= 95%)
    assert elapsed < 15, f"introspección tardó {elapsed:.2f}s (meta < 15s)"
    print(f"\n[perf] {N_TABLES} tablas introspectadas en {elapsed:.2f}s")

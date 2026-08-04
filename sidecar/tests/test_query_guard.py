"""Barrera de escritura del editor de consultas.

Cubre las dos funciones puras que deciden qué se puede ejecutar: la frontera
lectura/escritura y el aviso de UPDATE/DELETE sin WHERE.
"""
import pytest

from pg_diagrammer.api.routes.db import is_read_statement, missing_where
from pg_diagrammer.domain.sql_script import split_statements


@pytest.mark.parametrize("sql", [
    "SELECT 1",
    "select * from rrhh.aviso",
    "  \n SELECT * FROM t",
    "-- comentario\nSELECT 1",
    "/* bloque */ SELECT 1",
    "(SELECT 1)",
    "WITH x AS (SELECT 1) SELECT * FROM x",
    "SHOW search_path",
    "EXPLAIN SELECT 1",
    "VALUES (1)",
    "TABLE rrhh.aviso",
])
def test_sentencias_de_lectura(sql):
    assert is_read_statement(sql)


@pytest.mark.parametrize("sql", [
    "UPDATE rrhh.aviso SET mensaje = 'x' WHERE idaviso = 1",
    "INSERT INTO rrhh.aviso (mensaje) VALUES ('x')",
    "DELETE FROM rrhh.aviso WHERE idaviso = 1",
    "TRUNCATE rrhh.aviso",
    "CREATE TABLE t (id int)",
    "DROP TABLE t",
    "ALTER TABLE t ADD COLUMN c int",
    "GRANT SELECT ON t TO nadie",
    "-- SELECT parece lectura\nDELETE FROM t",
])
def test_sentencias_de_escritura(sql):
    assert not is_read_statement(sql)


@pytest.mark.parametrize("sql", [
    "UPDATE rrhh.aviso SET mensaje = 'x'",
    "DELETE FROM rrhh.aviso",
    "update aviso set a = 1",
    # 'where' dentro de un literal no es una cláusula real
    "UPDATE t SET nota = 'dime where estas'",
    # ni dentro de un comentario
    "DELETE FROM t -- where id = 1",
])
def test_avisa_si_falta_where(sql):
    assert missing_where(sql)


@pytest.mark.parametrize("sql", [
    "UPDATE rrhh.aviso SET mensaje = 'x' WHERE idaviso = 1",
    "DELETE FROM rrhh.aviso WHERE fecha < now()",
    "delete from t where 1=1",
    "UPDATE t SET a = (SELECT max(b) FROM u) WHERE id = 3",
])
def test_no_avisa_si_hay_where(sql):
    assert not missing_where(sql)


@pytest.mark.parametrize("sql", [
    "SELECT * FROM t",
    "INSERT INTO t VALUES (1)",
    "TRUNCATE t",           # destructivo, pero no lleva WHERE por definición
    "DROP TABLE t",
])
def test_el_aviso_solo_aplica_a_update_y_delete(sql):
    assert not missing_where(sql)


# --- la barrera se aplica a TODAS las sentencias del script ------------------
# Comprobar solo la primera dejaría "SELECT 1; DELETE FROM t" como vía de
# escape, y en SQL Server no hay transacción READ ONLY que lo pare después.

def _script_es_de_lectura(script: str) -> bool:
    """Réplica de la comprobación previa de run_query."""
    return all(is_read_statement(s) for s in split_statements(script))


def test_script_de_solo_lecturas_pasa():
    assert _script_es_de_lectura("SELECT 1; SELECT 2; WITH x AS (SELECT 1) SELECT * FROM x")


@pytest.mark.parametrize("script", [
    "SELECT 1; DELETE FROM t",
    "SELECT 1; UPDATE t SET a = 1 WHERE id = 2",
    "SELECT 1; SELECT 2; DROP TABLE t",
    "SELECT 1; /* comentario */ TRUNCATE t",
])
def test_una_escritura_escondida_al_final_no_pasa(script):
    assert not _script_es_de_lectura(script)


def test_el_aviso_de_where_mira_todas_las_sentencias():
    script = "UPDATE t SET a = 1 WHERE id = 2; DELETE FROM u"
    assert any(missing_where(s) for s in split_statements(script))

"""Separación de un script en sentencias."""
from pg_diagrammer.domain.sql_script import is_only_comments, split_statements


def test_varias_sentencias():
    assert split_statements("SELECT 1; SELECT 2") == ["SELECT 1", "SELECT 2"]


def test_punto_y_coma_final_no_genera_sentencia_vacia():
    assert split_statements("SELECT 1;\nSELECT 2;\n") == ["SELECT 1", "SELECT 2"]


def test_sentencia_unica_sin_punto_y_coma():
    assert split_statements("SELECT * FROM rrhh.aviso") == ["SELECT * FROM rrhh.aviso"]


def test_punto_y_coma_dentro_de_literal():
    assert split_statements("SELECT 'a;b'; SELECT 2") == ["SELECT 'a;b'", "SELECT 2"]


def test_comilla_simple_escapada_dentro_de_literal():
    assert split_statements("SELECT 'no''va;aqui'; SELECT 2") == [
        "SELECT 'no''va;aqui'",
        "SELECT 2",
    ]


def test_punto_y_coma_dentro_de_identificador_entrecomillado():
    assert split_statements('SELECT * FROM "raro;nombre"; SELECT 2') == [
        'SELECT * FROM "raro;nombre"',
        "SELECT 2",
    ]


def test_punto_y_coma_dentro_de_corchetes_tsql():
    assert split_statements("SELECT * FROM [raro;nombre]; SELECT 2") == [
        "SELECT * FROM [raro;nombre]",
        "SELECT 2",
    ]


def test_punto_y_coma_dentro_de_comentario_de_linea():
    assert split_statements("SELECT 1 -- ; no corta\n; SELECT 2") == [
        "SELECT 1 -- ; no corta",
        "SELECT 2",
    ]


def test_punto_y_coma_dentro_de_comentario_de_bloque():
    assert split_statements("SELECT 1 /* ; no ; corta */; SELECT 2") == [
        "SELECT 1 /* ; no ; corta */",
        "SELECT 2",
    ]


def test_comentarios_de_bloque_anidados():
    assert split_statements("SELECT 1 /* a /* b ; */ c */; SELECT 2") == [
        "SELECT 1 /* a /* b ; */ c */",
        "SELECT 2",
    ]


def test_cuerpo_con_dollar_quoting_no_se_parte():
    script = (
        "CREATE FUNCTION f() RETURNS int AS $$ BEGIN a := 1; RETURN 2; END; $$ "
        "LANGUAGE plpgsql; SELECT 1"
    )
    assert split_statements(script) == [
        "CREATE FUNCTION f() RETURNS int AS $$ BEGIN a := 1; RETURN 2; END; $$ "
        "LANGUAGE plpgsql",
        "SELECT 1",
    ]


def test_dollar_quoting_con_etiqueta():
    script = "DO $body$ BEGIN PERFORM 1; END $body$; SELECT 9"
    assert split_statements(script) == [
        "DO $body$ BEGIN PERFORM 1; END $body$",
        "SELECT 9",
    ]


def test_script_vacio_o_solo_comentarios():
    assert split_statements("") == []
    assert split_statements("   \n  ") == []
    assert split_statements("-- solo un comentario") == []
    assert split_statements("/* nada */ ; -- tampoco") == []


def test_comentario_previo_se_queda_con_su_sentencia():
    assert split_statements("-- cabecera\nSELECT 1; SELECT 2") == [
        "-- cabecera\nSELECT 1",
        "SELECT 2",
    ]


def test_is_only_comments():
    assert is_only_comments("-- nada")
    assert is_only_comments("/* nada */")
    assert not is_only_comments("-- nada\nSELECT 1")

"""Serialización acotada de celdas en las respuestas de datos.

Sin tope, una página de una tabla de bitácora con payloads grandes puede
generar cientos de MB de JSON y agotar la memoria del sidecar antes de
responder (el frontend recibía entonces un "Failed to fetch" sin explicación).
"""
from pg_diagrammer.api.routes.db import MAX_CELL_CHARS, _jsonable


def test_short_values_pass_through():
    assert _jsonable("hola") == "hola"
    assert _jsonable(42) == 42
    assert _jsonable(True) is True
    assert _jsonable(None) is None


def test_long_strings_are_clipped():
    out = _jsonable("x" * (MAX_CELL_CHARS + 500))
    assert out.endswith("… (truncado)")
    assert len(out) == MAX_CELL_CHARS + len("… (truncado)")


def test_long_json_values_are_clipped():
    out = _jsonable({"payload": "y" * (MAX_CELL_CHARS * 2)})
    assert out.endswith("… (truncado)")


def test_non_string_objects_are_clipped_too():
    class Huge:
        def __str__(self):
            return "z" * (MAX_CELL_CHARS * 2)

    assert _jsonable(Huge()).endswith("… (truncado)")


def test_binary_still_shown_as_hex_prefix():
    out = _jsonable(b"\x01\x02" * 200)
    assert out.startswith("\\x0102")
    assert out.endswith("…")

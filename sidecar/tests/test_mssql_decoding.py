"""Decodificación tolerante de columnas CHAR/VARCHAR de SQL Server.

Una sola fila con un byte que la codepage del collation no define (0x81 en
cp1252, típico de texto UTF-8 metido en una columna varchar) hacía fallar la
consulta entera con "'charmap' codec can't decode byte 0x81". Como Meridia es
un visor de solo lectura, se prefiere mostrar la fila con el carácter
sustituido antes que perder todo el resultado.
"""
import codecs

import pytds.collate
import pytds.tds_base
import pytds.tds_reader

from pg_diagrammer.connections import mssql


def test_valid_text_decodes_exactly():
    codec = mssql.lenient(codecs.lookup("cp1252"))
    assert codec.decode("Bitácora ñandú".encode("cp1252"))[0] == "Bitácora ñandú"


def test_undefined_byte_is_replaced_instead_of_raising():
    codec = mssql.lenient(codecs.lookup("cp1252"))
    text, consumed = codec.decode(b"antes\x81despues")
    assert text.startswith("antes") and text.endswith("despues")
    assert "�" in text
    assert consumed == len(b"antes\x81despues")


def test_incremental_decoder_is_lenient():
    """Camino de varchar(max)/text: pytds usa el decodificador incremental."""
    codec = mssql.lenient(codecs.lookup("cp1252"))
    decoder = codec.incrementaldecoder()
    out = decoder.decode(b"antes\x81") + decoder.decode(b"despues", True)
    assert out.startswith("antes") and out.endswith("despues")
    assert "�" in out


def test_iterdecode_of_pytds_survives_undefined_bytes():
    """El helper real de pytds para columnas grandes, con el codec envuelto."""
    codec = mssql.lenient(codecs.lookup("cp1252"))
    chunks = [b"linea1\x81", b"linea2"]
    assert "".join(pytds.tds_base.iterdecode(chunks, codec)).count("�") == 1


def _cp1252_collation() -> pytds.collate.Collation:
    """Collation real de pytds equivalente a SQL_Latin1_General_CP1_CI_AS."""
    return pytds.collate.Collation(
        lcid=1033, sort_id=0, ignore_case=True, ignore_accent=False,
        ignore_width=False, ignore_kana=False, binary=False, binary2=False,
        version=0,
    )


def test_collation_codec_covers_both_read_paths():
    """Punto del que salen los codecs de TODAS las columnas de texto.

    Camino 1: char/varchar(n) → `decode` directo.
    Camino 2: varchar(max)/text → decodificador incremental vía `iterdecode`.
    """
    assert getattr(pytds.collate.Collation.get_codec, "_meridia_lenient", False)
    codec = _cp1252_collation().get_codec()
    assert codec.decode(b"a\x81b")[0] == "a�b"
    assert "".join(pytds.tds_base.iterdecode([b"a\x81", b"b"], codec)) == "a�b"


def test_patches_are_idempotent():
    reader_cls = mssql._reader_class()
    assert reader_cls is not None, "pytds cambió la clase lectora"
    before = (reader_cls.read_str, pytds.collate.Collation.get_codec)
    mssql._install_lenient_decoding()
    assert (reader_cls.read_str, pytds.collate.Collation.get_codec) == before


def test_read_str_survives_undefined_bytes():
    """El lector real de pytds, alimentado con un flujo en memoria."""

    class FakeReader:
        def __init__(self, data):
            self.data = data

        def recv(self, size):
            chunk, self.data = self.data[:size], self.data[size:]
            return chunk

    payload = b"log\x81line"
    out = mssql._reader_class().read_str(
        FakeReader(payload), len(payload), codecs.lookup("cp1252")
    )
    assert out.startswith("log") and out.endswith("line")
    assert "�" in out


def test_unknown_codec_names_fall_back_to_the_original():
    fake = codecs.CodecInfo(
        encode=lambda s, e="strict": (b"", 0),
        decode=lambda b, e="strict": ("", 0),
        name="collation-inexistente",
    )
    assert mssql.lenient(fake) is fake

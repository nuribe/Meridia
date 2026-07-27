"""Conexiones a SQL Server vía python-tds (puro Python, sin ODBC).

Espejo de manager.py para el motor sqlserver. Soporta:
- SQL auth (usuario/contraseña, keychain — igual que PostgreSQL).
- Windows integrada: SSPI (usuario actual, solo Windows, sin contraseña)
  o NTLM explícito (DOMINIO\\usuario + contraseña) desde cualquier SO.
"""
from __future__ import annotations

import codecs
import functools
import sys

import pytds
import pytds.collate
import pytds.login
import pytds.tds_reader
import pytds.tds_types

from pg_diagrammer.domain.models import AuthMethod, ConnectionParams

# Bases de sistema que no tiene sentido diagramar.
SYSTEM_DATABASES = ("master", "tempdb", "model", "msdb")


# --- Decodificación tolerante de columnas de texto (no quitar) ---
#
# Las columnas no-Unicode se decodifican con la codepage del collation. Es
# habitual encontrarse bytes que esa codepage no define (p. ej. 0x81 en
# cp1252, típico de texto UTF-8 insertado en una columna varchar). pytds usa
# el codec en modo estricto, así que UNA fila corrupta hace fallar la consulta
# entera con "'charmap' codec can't decode byte 0x81".
#
# Meridia es un visor de solo lectura: es preferible mostrar la fila con el
# carácter sustituido (U+FFFD) que perder todo el resultado. Para datos
# válidos, `errors="replace"` produce exactamente el mismo texto que el modo
# estricto, así que esto no altera nada de lo que ya se leía bien.
#
# pytds decodifica texto por DOS caminos distintos y hay que cubrir ambos:
#   1. `read_str(size, codec)` → columnas de tamaño fijo (char/varchar(n)).
#   2. `iterdecode(chunks, codec)` → columnas grandes (varchar(max), text),
#      que usan el DECODIFICADOR INCREMENTAL del codec, no su `decode`.
# El codec en ambos casos sale de `Collation.get_codec()`, así que ese es el
# punto que de verdad hay que interceptar.


@functools.lru_cache(maxsize=None)
def _lenient_codec(charset: str) -> codecs.CodecInfo:
    """Codec idéntico al original pero que nunca lanza UnicodeDecodeError."""
    base = codecs.lookup(charset)

    def decode(data, errors="strict"):
        try:
            return base.decode(data, errors)
        except UnicodeDecodeError:
            text, _ = base.decode(data, "replace")
            return text, len(data)

    def incrementaldecoder(errors="strict"):
        # Camino de varchar(max)/text: pytds construye el decodificador sin
        # argumentos, así que aquí es donde se fuerza la tolerancia.
        return base.incrementaldecoder("replace")

    return codecs.CodecInfo(
        encode=base.encode,
        decode=decode,
        streamreader=base.streamreader,
        streamwriter=base.streamwriter,
        incrementalencoder=base.incrementalencoder,
        incrementaldecoder=incrementaldecoder,
        name=base.name,
    )


def lenient(codec: codecs.CodecInfo) -> codecs.CodecInfo:
    """Envuelve un codec de pytds; si no se puede envolver, se deja tal cual."""
    try:
        return _lenient_codec(codec.name)
    except (LookupError, AttributeError, TypeError):
        return codec


def _reader_class():
    """Clase lectora de pytds: renombrada a `_TdsReader` a partir de 1.16."""
    for name in ("_TdsReader", "TdsReader"):
        cls = getattr(pytds.tds_reader, name, None)
        if cls is not None and hasattr(cls, "read_str"):
            return cls
    return None


def _patch_get_codec() -> None:
    """Camino principal: el codec del collation de cada columna de texto."""
    collation = pytds.collate.Collation
    if getattr(collation.get_codec, "_meridia_lenient", False):
        return
    original = collation.get_codec

    @functools.wraps(original)
    def get_codec(self):
        return lenient(original(self))

    get_codec._meridia_lenient = True
    collation.get_codec = get_codec


def _patch_read_str() -> None:
    """Red de seguridad para los codecs que no vienen del collation
    (`server_codec` en TDS 7.0, y el lector de `sql_variant`)."""
    reader = _reader_class()
    if reader is None or getattr(reader.read_str, "_meridia_lenient", False):
        return
    original = reader.read_str

    @functools.wraps(original)
    def read_str(self, size, codec):
        return original(self, size, lenient(codec))

    read_str._meridia_lenient = True
    reader.read_str = read_str


def _patch_ucs2() -> None:
    """nvarchar(max)/ntext usan un utf-16-le fijo, no el del collation."""
    for module in (pytds.tds_types, pytds.collate):
        current = getattr(module, "ucs2_codec", None)
        if current is not None:
            module.ucs2_codec = lenient(current)


def _install_lenient_decoding() -> None:
    """Instala los tres parches (idempotente)."""
    _patch_get_codec()
    _patch_read_str()
    _patch_ucs2()


_install_lenient_decoding()

LIST_DATABASES_SQL = """
    SELECT d.name,
           ISNULL(SUSER_SNAME(d.owner_sid), '') AS owner,
           ISNULL(d.collation_name, '') AS collation
    FROM sys.databases d
    WHERE d.state = 0
      AND HAS_DBACCESS(d.name) = 1
      AND d.name NOT IN ('master', 'tempdb', 'model', 'msdb')
    ORDER BY d.name
"""


def _auth_kwargs(user: str, password: str, auth_method: AuthMethod) -> dict:
    """Argumentos de autenticación para pytds.connect según el método."""
    if auth_method == AuthMethod.windows:
        if not password and sys.platform == "win32":
            # Usuario actual de Windows, sin contraseña (SSPI).
            return {"auth": pytds.login.SspiAuth()}
        # NTLM explícito: user "DOMINIO\\usuario" + contraseña.
        return {"auth": pytds.login.NtlmAuth(user_name=user, password=password)}
    return {"user": user, "password": password}


def connect(
    host: str,
    port: int,
    user: str,
    password: str,
    dbname: str,
    auth_method: AuthMethod = AuthMethod.sql,
    connect_timeout: int = 8,
    query_timeout: int = 0,
):
    """Abre una conexión DB-API a SQL Server (autocommit, uso de solo lectura)."""
    return pytds.connect(
        dsn=host,
        port=port,
        database=dbname,
        login_timeout=connect_timeout,
        timeout=query_timeout or None,
        autocommit=True,
        **_auth_kwargs(user, password, auth_method),
    )


def connect_params(params: ConnectionParams, query_timeout: int = 0):
    return connect(
        host=params.host,
        port=params.port,
        user=params.user,
        password=params.password,
        dbname=params.dbname,
        auth_method=params.auth_method,
        connect_timeout=params.connect_timeout,
        query_timeout=query_timeout,
    )


def test_connection(params: ConnectionParams) -> dict:
    """Abre una conexión efímera y devuelve datos básicos del servidor."""
    with connect_params(params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT @@VERSION, DB_NAME(), SUSER_SNAME()")
            version, dbname, user = cur.fetchone()
    return {"server_version": version, "database": dbname, "user": user}


def list_databases_conn(conn) -> list[dict]:
    """Bases de usuario accesibles y en línea (excluye las de sistema)."""
    with conn.cursor() as cur:
        cur.execute(LIST_DATABASES_SQL)
        rows = cur.fetchall()
    return [{"name": r[0], "owner": r[1], "encoding": r[2]} for r in rows]


def list_databases(params: ConnectionParams) -> list[dict]:
    with connect_params(params) as conn:
        return list_databases_conn(conn)

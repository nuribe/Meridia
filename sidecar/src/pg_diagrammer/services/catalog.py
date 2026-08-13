"""Servicios de catálogo: snapshot, objetos, tablas, relaciones y export.

Es la lógica que antes vivía dentro de las funciones de ruta de
`api/routes/db.py`. Aquí no hay `Request` ni `JSONResponse`: las funciones
reciben los stores y devuelven objetos de dominio, o lanzan una excepción de
`services.errors`. Eso es lo que permite que el servidor MCP use exactamente
el mismo código que la API REST sin duplicar una línea.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from pg_diagrammer.connections import manager, mssql
from pg_diagrammer.connections.profiles import PasswordUnavailable, ProfileStore
from pg_diagrammer.domain.models import (
    ConnectionProfile,
    Engine,
    ObjectSummary,
    Relationship,
    Routine,
    Snapshot,
    Table,
    TableKind,
)
from pg_diagrammer.errors import DB_EXCEPTIONS, classify_db_error
from pg_diagrammer.export.generators import to_dbml, to_mermaid
from pg_diagrammer.introspection import introspector, mssql_introspector
from pg_diagrammer.introspection.cache import SnapshotCache
from pg_diagrammer.introspection.introspector import diff_snapshots, routines_using
from pg_diagrammer.introspection.view_joins import collect_relations, parse_view_joins
from pg_diagrammer.services.errors import (
    DatabaseUnavailable,
    InvalidRequest,
    ObjectNotFound,
    PasswordRequired,
    ProfileNotFound,
)

EXPORT_FORMATS = {"mermaid": "mmd", "dbml": "dbml"}


@dataclass
class TableDetail:
    """Ficha completa de una tabla: la tabla y su vecindario en el snapshot."""

    table: Table
    referenced_by: list[Relationship] = field(default_factory=list)
    routines: list[Routine] = field(default_factory=list)
    views: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Perfiles y snapshots
# --------------------------------------------------------------------------

def get_profile(store: ProfileStore, profile_id: str) -> ConnectionProfile:
    profile = store.get(profile_id)
    if profile is None:
        raise ProfileNotFound(profile_id)
    return profile


def list_databases(store: ProfileStore, profile_id: str) -> list[dict]:
    """Bases de datos del servidor del perfil.

    Se conecta a la base del perfil (con pgbouncer, una que exista en su pool):
    desde cualquier conexión se pueden consultar `pg_database` / `sys.databases`
    y obtener TODAS las bases del servidor.
    """
    profile = get_profile(store, profile_id)
    default_db = "master" if profile.engine == Engine.sqlserver else "postgres"
    conn_db = getattr(profile, "dbname", None) or default_db
    try:
        if profile.engine == Engine.sqlserver:
            with manager.open_profile_connection(store, profile, conn_db) as conn:
                return mssql.list_databases_conn(conn)
        return manager.list_databases_conninfo(store.conninfo(profile, conn_db))
    except PasswordUnavailable:
        raise PasswordRequired(profile_id) from None
    except DB_EXCEPTIONS as exc:
        raise DatabaseUnavailable(classify_db_error(profile.engine, exc)) from exc


def get_snapshot(
    store: ProfileStore,
    cache: SnapshotCache,
    profile_id: str,
    dbname: str,
    *,
    force: bool = False,
) -> Snapshot:
    """Snapshot cacheado, o introspección bajo demanda.

    Traduce aquí —y solo aquí— los fallos del motor: el resto del código
    trabaja con `Snapshot` o con una excepción de servicio ya clasificada.
    """
    profile = get_profile(store, profile_id)
    if not force:
        cached = cache.get(profile_id, dbname)
        if cached is not None:
            return cached
    try:
        if profile.engine == Engine.sqlserver:
            with manager.open_profile_connection(store, profile, dbname) as conn:
                snapshot = mssql_introspector.introspect(conn, dbname)
        else:
            conninfo = store.conninfo(profile, dbname)
            snapshot = introspector.introspect(conninfo, dbname)
    except PasswordUnavailable:
        raise PasswordRequired(profile_id) from None
    except DB_EXCEPTIONS as exc:
        raise DatabaseUnavailable(classify_db_error(profile.engine, exc)) from exc
    cache.set(profile_id, dbname, snapshot)
    return snapshot


def refresh_snapshot(
    store: ProfileStore,
    cache: SnapshotCache,
    profile_id: str,
    dbname: str,
) -> tuple[Snapshot, dict]:
    """Re-introspecta la base y devuelve `(snapshot, diff)` contra el anterior."""
    old = cache.get(profile_id, dbname)
    snapshot = get_snapshot(store, cache, profile_id, dbname, force=True)
    return snapshot, diff_snapshots(old, snapshot)


# --------------------------------------------------------------------------
# Objetos
# --------------------------------------------------------------------------

def list_objects(
    snapshot: Snapshot,
    *,
    schema: str | None = None,
    kind: TableKind | None = None,
    q: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> tuple[int, list[ObjectSummary]]:
    """Objetos del snapshot filtrados y paginados. Devuelve `(total, página)`."""
    items = list(snapshot.tables.values())
    if schema:
        # Acepta uno o varios schemas separados por comas
        allowed = {sc.strip() for sc in schema.split(",") if sc.strip()}
        items = [t for t in items if t.schema_name in allowed]
    if kind:
        items = [t for t in items if t.kind == kind]
    if q:
        needle = q.lower()
        items = [
            t for t in items
            if needle in t.name.lower()
            or any(needle in c.name.lower() for c in t.columns)
        ]
    total = len(items)
    page = [
        ObjectSummary(
            schema_name=t.schema_name,
            name=t.name,
            kind=t.kind,
            comment=t.comment,
            estimated_rows=t.estimated_rows,
        )
        for t in items[offset : offset + limit]
    ]
    return total, page


def _detail(snapshot: Snapshot, key: str, table: Table) -> TableDetail:
    return TableDetail(
        table=table,
        referenced_by=[
            r for r in snapshot.relationships if r.target == key and r.source != key
        ],
        routines=routines_using(snapshot, key),
        views=snapshot.view_usage.get(key, []),
    )


def table_detail(snapshot: Snapshot, schema: str, table: str) -> TableDetail:
    key = f"{schema}.{table}"
    found = snapshot.tables.get(key)
    if found is None:
        raise ObjectNotFound(
            f"No existe {key} en el snapshot.",
            "Si la tabla es nueva, ejecuta refresh para re-introspectar.",
        )
    return _detail(snapshot, key, found)


def refresh_table(
    store: ProfileStore,
    cache: SnapshotCache,
    profile_id: str,
    dbname: str,
    schema: str,
    table: str,
) -> TableDetail | None:
    """Refresh granular de una tabla. `None` si la tabla ya no existe.

    PostgreSQL re-introspecta solo esa tabla; SQL Server re-introspecta la base
    completa (mismo resultado, más lento).
    """
    profile = get_profile(store, profile_id)
    snapshot = get_snapshot(store, cache, profile_id, dbname)
    key = f"{schema}.{table}"
    try:
        if profile.engine == Engine.sqlserver:
            snapshot = get_snapshot(store, cache, profile_id, dbname, force=True)
            found = snapshot.tables.get(key)
        else:
            conninfo = store.conninfo(profile, dbname)
            found = introspector.refresh_table(conninfo, snapshot, schema, table)
    except PasswordUnavailable:
        raise PasswordRequired(profile_id) from None
    except DB_EXCEPTIONS as exc:
        raise DatabaseUnavailable(classify_db_error(profile.engine, exc)) from exc
    if found is None:
        return None
    return _detail(snapshot, key, found)


# --------------------------------------------------------------------------
# Relaciones
# --------------------------------------------------------------------------

def relationships_between(snapshot: Snapshot, tables: list[str]) -> list[Relationship]:
    """Aristas cuyos dos extremos están en `tables` (las del lienzo)."""
    wanted = set(tables)
    return [
        r for r in snapshot.relationships
        if r.source in wanted and r.target in wanted
    ]


def related(
    snapshot: Snapshot,
    schema: str,
    table: str,
    direction: str = "both",
) -> list[str]:
    """Tablas relacionadas con la dada.

    direction:
      - "in"   → tablas que la referencian (dependientes, "debajo")
      - "out"  → tablas a las que apunta con sus FKs (referenciadas, "arriba")
      - "both" → ambas (por defecto)
    """
    if direction not in ("in", "out", "both"):
        raise InvalidRequest(
            f"direction inválida: {direction}",
            "Valores permitidos: in, out, both.",
        )
    key = f"{schema}.{table}"
    found: set[str] = set()
    for r in snapshot.relationships:
        if direction in ("out", "both") and r.source == key:
            found.add(r.target)
        if direction in ("in", "both") and r.target == key:
            found.add(r.source)
    found.discard(key)
    return sorted(found)


def known_names(snapshot: Snapshot) -> dict[str, str]:
    """Mapa de resolución de nombres: claves completas + nombres sueltos no
    ambiguos -> "schema.tabla". Mismo criterio para vistas y para el parser."""
    known: dict[str, str] = {}
    name_counts: dict[str, int] = {}
    for t in snapshot.tables:
        known[t] = t
        bare = t.split(".", 1)[1]
        name_counts[bare] = name_counts.get(bare, 0) + 1
    for t in snapshot.tables:
        bare = t.split(".", 1)[1]
        if name_counts[bare] == 1:
            known.setdefault(bare, t)
    return known


def view_dependencies(
    snapshot: Snapshot,
    schema: str,
    view: str,
) -> tuple[list[str], list[dict]]:
    """Tablas/vistas de las que depende una vista, y sus joins.

    Une dos fuentes: las dependencias registradas (pg_rewrite/pg_depend) y las
    relaciones que aparecen textualmente en el FROM/JOIN del SQL de la vista.
    """
    key = f"{schema}.{view}"
    found = snapshot.tables.get(key)
    if found is None:
        raise ObjectNotFound(f"No existe {key} en el snapshot.")
    if found.kind not in (TableKind.view, TableKind.matview):
        raise InvalidRequest(
            f"{key} no es una vista.",
            "Este endpoint solo aplica a vistas y vistas materializadas.",
        )
    known = known_names(snapshot)
    dep_tables = {t for t, views in snapshot.view_usage.items() if key in views}
    definition = found.definition or ""
    sql_rels = collect_relations(definition, known)
    sql_rels.discard(key)
    return sorted(dep_tables | sql_rels), parse_view_joins(definition, known)


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------

def export_model(snapshot: Snapshot, tables: list[str], fmt: str) -> tuple[str, str]:
    """Exporta las tablas indicadas. Devuelve `(contenido, extensión)`."""
    if fmt == "mermaid":
        return to_mermaid(snapshot, tables), EXPORT_FORMATS["mermaid"]
    if fmt == "dbml":
        return to_dbml(snapshot, tables), EXPORT_FORMATS["dbml"]
    raise InvalidRequest(
        f"Formato no soportado: {fmt}",
        f"Formatos disponibles: {', '.join(EXPORT_FORMATS)}.",
    )

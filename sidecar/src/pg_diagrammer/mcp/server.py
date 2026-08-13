"""Servidor MCP de Meridia — herramientas de catálogo, solo lectura.

Tres reglas gobiernan todo lo que hay aquí, y ninguna es negociable desde una
tool:

1. **Solo lectura.** Ninguna herramienta escribe. `allow_writes` del perfil se
   ignora por completo: ese permiso se concede frente al editor de consultas de
   la app, donde un `UPDATE` sin `WHERE` pide confirmación. Con un agente no hay
   a quién preguntar, así que no se hereda.
2. **Dos interruptores, no uno.** `enabled` abre el catálogo y el plan
   *estimado* —que no lee ni una fila—; `allow_query` abre además ejecutar
   `SELECT` y medir el plan *real*, que sí ejecuta. Cada llamada los relee de
   disco, así que apagarlos desde la app corta el uso siguiente sin reiniciar
   el cliente MCP.
3. **Todo queda registrado**: la conexión del cliente, cada llamada, los
   rechazos y los errores. La vista «Actividad IA» de la app lee esa bitácora.

El proceso mantiene su propia caché de snapshots —no comparte memoria con el
sidecar— con un TTL corto, para no servir un esquema de hace horas.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from pg_diagrammer import __version__
from pg_diagrammer.activity.log import (
    STATUS_DENIED,
    STATUS_ERROR,
    STATUS_OK,
    ActivityEvent,
    ActivityLog,
    sample_rows,
)
from pg_diagrammer.activity.settings import McpSettingsStore
from pg_diagrammer.connections.profiles import ProfileStore, default_data_dir
from pg_diagrammer.domain.models import TableKind
from pg_diagrammer.introspection.cache import SnapshotCache
from pg_diagrammer.services import catalog, query
from pg_diagrammer.services.errors import InvalidRequest, ServiceError

SERVER_NAME = "meridia"

# Un cliente MCP puede vivir horas. Pasado este tiempo el snapshot se
# re-introspecta en la siguiente llamada, para no describir un esquema viejo.
DEFAULT_TTL_SECONDS = 300

# Tope propio del MCP, más estricto que el de la API: un agente no debería
# poder volcarse el catálogo entero de una sentada.
MAX_OBJECTS = 500

ENABLE_HINT = (
    "Activa «Permitir acceso MCP» en la pestaña Actividad IA de Meridia."
)
QUERY_HINT = (
    "Activa «Permitir consultas SQL» en la pestaña Actividad IA de Meridia."
)

# Tope de tablas de un ERD: por encima, el diagrama deja de ser legible y el
# agente gasta contexto en algo que no puede razonar de una pieza.
MAX_ERD_TABLES = 200

# Anuncia a los clientes que ninguna tool modifica nada. No es la barrera
# —la barrera es que no existe código de escritura— pero deja que Copilot y
# Claude las traten como consultas seguras.
READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, open_world_hint=False
)


@dataclass
class Deps:
    """Todo lo que necesita el servidor, junto para poder inyectarlo en tests."""

    profiles: ProfileStore
    snapshots: SnapshotCache
    settings: McpSettingsStore
    activity: ActivityLog
    ttl_seconds: float = DEFAULT_TTL_SECONDS
    _fetched_at: dict[tuple[str, str], float] = field(default_factory=dict)

    @classmethod
    def build(cls, data_dir: Path | None = None) -> "Deps":
        data_dir = data_dir or default_data_dir()
        ttl = float(os.environ.get("PG_DIAGRAMMER_MCP_TTL", DEFAULT_TTL_SECONDS))
        return cls(
            profiles=ProfileStore(data_dir=data_dir),
            snapshots=SnapshotCache(),
            settings=McpSettingsStore(data_dir),
            activity=ActivityLog(data_dir),
            ttl_seconds=ttl,
        )

    def snapshot(self, profile_id: str, dbname: str, force: bool = False):
        """Snapshot con TTL propio de este proceso."""
        key = (profile_id, dbname)
        age = time.monotonic() - self._fetched_at.get(key, 0.0)
        stale = age > self.ttl_seconds
        snap = catalog.get_snapshot(
            self.profiles, self.snapshots, profile_id, dbname, force=force or stale
        )
        if force or stale:
            self._fetched_at[key] = time.monotonic()
        return snap


class AccessDenied(ServiceError):
    """El interruptor está apagado o el perfil no está en la allowlist."""

    code = "MCP_DISABLED"
    status = 403


class RegistroDeConexion:
    """Middleware que anota el handshake del cliente en la bitácora.

    Sin esto, el panel solo se entera de un cliente cuando este **llama** a una
    herramienta. Un Claude o un Copilot recién conectados, que todavía no han
    preguntado nada, eran indistinguibles de un servidor mal configurado que
    nunca arrancó: en los dos casos el panel decía «0 llamadas registradas».
    Ese hueco costó una tarde de depuración a ciegas.

    Se registra ANTES de mirar el interruptor a propósito: si el acceso está
    apagado, lo útil es ver que el cliente llegó y que se le está denegando,
    no un silencio idéntico al del cliente que no llegó nunca.
    """

    def __init__(self, deps: "Deps") -> None:
        self.deps = deps

    async def __call__(self, ctx, call_next):
        if ctx.method != "initialize":
            return await call_next(ctx)
        info = (ctx.params or {}).get("clientInfo") or {}
        ajustes = self.deps.settings.get()
        self.deps.activity.append(ActivityEvent(
            tool="conexión",
            client=info.get("name") or "unknown",
            target=f"{info.get('name', '?')} {info.get('version', '')}".strip(),
            args={
                "acceso_mcp": ajustes.enabled,
                "consultas_sql": ajustes.allow_query,
                "protocolo": (ctx.params or {}).get("protocolVersion"),
            },
            status=STATUS_OK,
        ))
        return await call_next(ctx)


def _client_name(ctx: Context | None) -> str:
    """Nombre que el cliente declaró en `initialize`, para la bitácora.

    Es lo que distingue «VS Code» de «Claude» en el panel. Si el cliente no lo
    envía, o el SDK cambia de forma, la llamada se registra igual como
    `unknown`: perder el nombre nunca debe impedir que quede el rastro.
    """
    try:
        params = ctx.session.client_params  # type: ignore[union-attr]
        if params is None:
            return "unknown"
        info = getattr(params, "client_info", None) or getattr(params, "clientInfo", None)
        return getattr(info, "name", None) or "unknown"
    except Exception:
        return "unknown"


def _guard(deps: Deps, profile_id: str | None, *, needs_query: bool = False) -> None:
    settings = deps.settings.get()
    if not settings.enabled:
        raise AccessDenied("El acceso MCP está desactivado en Meridia.", ENABLE_HINT)
    if profile_id and settings.allowed_profiles and profile_id not in settings.allowed_profiles:
        raise AccessDenied(
            "Este perfil no está habilitado para acceso MCP.",
            "Márcalo en la lista de perfiles visibles de la pestaña Actividad IA.",
        )
    if needs_query and not settings.allow_query:
        raise AccessDenied(
            "La ejecución de consultas SQL está desactivada en Meridia.",
            QUERY_HINT,
        )


def _profile_name(deps: Deps, profile_id: str | None) -> str | None:
    if not profile_id:
        return None
    profile = deps.profiles.get(profile_id)
    return profile.name if profile else None


def _execute(
    deps: Deps,
    ctx: Context | None,
    tool: str,
    args: dict[str, Any],
    fn: Callable[[], Any],
    *,
    profile_id: str | None = None,
    dbname: str | None = None,
    target: str | None = None,
    count: Callable[[Any], int | None] | None = None,
    needs_query: bool = False,
    sample: Callable[[Any], tuple[list[str] | None, list[list] | None]] | None = None,
) -> Any:
    """Ejecuta una tool con guarda, cronómetro y bitácora.

    Es el único camino de ejecución: si una tool no pasa por aquí, no queda
    registrada, y una llamada sin registrar derrota el propósito del panel.
    """
    event = ActivityEvent(
        tool=tool,
        client=_client_name(ctx),
        profile_id=profile_id,
        profile_name=_profile_name(deps, profile_id),
        dbname=dbname,
        target=target,
        args=args,
    )
    started = time.perf_counter()
    try:
        _guard(deps, profile_id, needs_query=needs_query)
        result = fn()
    except ServiceError as exc:
        event.status = STATUS_DENIED if isinstance(exc, AccessDenied) else STATUS_ERROR
        event.error = exc.to_api_error().model_dump()
        event.elapsed_ms = int((time.perf_counter() - started) * 1000)
        deps.activity.append(event)
        raise ToolError(_as_text(exc)) from None
    except Exception as exc:  # noqa: BLE001 — nada debe escapar sin registrarse
        event.status = STATUS_ERROR
        event.error = {"code": "UNEXPECTED", "message": str(exc)}
        event.elapsed_ms = int((time.perf_counter() - started) * 1000)
        deps.activity.append(event)
        raise ToolError(f"[UNEXPECTED] {exc}") from None
    event.status = STATUS_OK
    event.row_count = count(result) if count else None
    if sample is not None:
        event.sample_columns, event.sample = sample(result)
    event.elapsed_ms = int((time.perf_counter() - started) * 1000)
    deps.activity.append(event)
    return result


def _as_text(exc: ServiceError) -> str:
    """El envelope de error del proyecto, en una línea legible por el agente."""
    err = exc.to_api_error()
    return f"[{err.code}] {err.message}" + (f" — {err.hint}" if err.hint else "")


def _sql_target(sql: str) -> str:
    """Etiqueta corta de la consulta para la columna «Objetivo» del panel."""
    flat = " ".join(sql.split())
    return flat if len(flat) <= 120 else flat[:120] + "…"


def _table_text(detail: catalog.TableDetail) -> str:
    """Ficha de tabla en texto plano, que es lo que consume bien un modelo."""
    t = detail.table
    lines = [f"{t.schema_name}.{t.name} ({t.kind.value})"]
    if t.comment:
        lines.append(f"  {t.comment}")
    if t.estimated_rows is not None:
        lines.append(f"  filas estimadas: {t.estimated_rows}")
    lines.append("")
    lines.append("Columnas:")
    fk_cols = {c for fk in t.foreign_keys for c in fk.columns}
    for c in t.columns:
        flags = []
        if c.is_pk:
            flags.append("PK")
        if c.name in fk_cols:
            flags.append("FK")
        if not c.is_nullable:
            flags.append("NOT NULL")
        marca = f"  [{', '.join(flags)}]" if flags else ""
        nota = f"  -- {c.comment}" if c.comment else ""
        lines.append(f"  {c.name}: {c.data_type}{marca}{nota}")
    if t.foreign_keys:
        lines.append("")
        lines.append("Claves foráneas:")
        for fk in t.foreign_keys:
            lines.append(
                f"  {', '.join(fk.columns)} -> {fk.ref_schema}.{fk.ref_table}"
                f"({', '.join(fk.ref_columns)})"
            )
    if detail.referenced_by:
        lines.append("")
        lines.append("Referenciada por:")
        for r in detail.referenced_by:
            lines.append(f"  {r.source} ({r.cardinality.value}, {r.fk_name})")
    if detail.views:
        lines.append("")
        lines.append("Usada en vistas: " + ", ".join(detail.views))
    return "\n".join(lines) + "\n"


def create_server(deps: Deps | None = None) -> MCPServer:
    """Construye el servidor. `deps` explícito facilita las pruebas."""
    deps = deps or Deps.build()
    server = MCPServer(
        name=SERVER_NAME,
        version=__version__,
        middleware=[RegistroDeConexion(deps)],
        instructions=(
            "Catálogo de bases de datos PostgreSQL y SQL Server a través de "
            "Meridia. Todas las herramientas son de solo lectura y operan sobre "
            "los perfiles de conexión que el usuario ya configuró en la app; "
            "nunca pidas credenciales. Empieza por meridia_list_profiles para "
            "obtener un profile_id, y usa meridia_get_relationships para "
            "entender el modelo antes de describir tablas una por una."
        ),
    )

    @server.tool(annotations=READ_ONLY)
    def meridia_list_profiles(ctx: Context) -> dict[str, Any]:
        """Perfiles de conexión disponibles para MCP.

        Devuelve el `profile_id` que piden las demás herramientas. Nunca
        incluye contraseñas: las credenciales viven en el keychain del sistema.
        """
        def run() -> dict:
            allowed = deps.settings.get().allowed_profiles
            profiles = [
                {
                    "profile_id": p.id,
                    "name": p.name,
                    "engine": p.engine.value,
                    "host": p.host,
                    "port": p.port,
                    "dbname": p.dbname,
                }
                for p in deps.profiles.list()
                if not allowed or p.id in allowed
            ]
            return {"profiles": profiles}

        return _execute(
            deps, ctx, "meridia_list_profiles", {}, run,
            count=lambda r: len(r["profiles"]),
        )

    @server.tool(annotations=READ_ONLY)
    def meridia_list_databases(ctx: Context, profile_id: str) -> dict[str, Any]:
        """Bases de datos del servidor al que apunta un perfil."""
        return _execute(
            deps, ctx, "meridia_list_databases", {"profile_id": profile_id},
            lambda: {"databases": catalog.list_databases(deps.profiles, profile_id)},
            profile_id=profile_id,
            count=lambda r: len(r["databases"]),
        )

    @server.tool(annotations=READ_ONLY)
    def meridia_list_objects(
        ctx: Context,
        profile_id: str,
        dbname: str,
        schema: str | None = None,
        kind: str | None = None,
        q: str | None = None,
        limit: int = 200,
        offset: int = 0,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """Tablas y vistas de una base de datos.

        `schema` admite varios separados por comas. `kind` filtra por tipo
        (table, view, matview, partitioned, foreign). `q` busca en el nombre
        del objeto y en el de sus columnas. Usa `force_refresh` solo si sabes
        que el esquema acaba de cambiar: re-introspecta la base entera.
        """
        args = {
            "profile_id": profile_id, "dbname": dbname, "schema": schema,
            "kind": kind, "q": q, "limit": limit, "offset": offset,
        }
        target = f"schema={schema}" if schema else (f"q={q}" if q else "todos")

        def run() -> dict:
            snapshot = deps.snapshot(profile_id, dbname, force=force_refresh)
            total, page = catalog.list_objects(
                snapshot,
                schema=schema,
                kind=TableKind(kind) if kind else None,
                q=q,
                limit=max(1, min(limit, MAX_OBJECTS)),
                offset=max(0, offset),
            )
            return {
                "total": total,
                "items": [o.model_dump(mode="json") for o in page],
                "snapshot_at": snapshot.created_at.isoformat(),
            }

        return _execute(
            deps, ctx, "meridia_list_objects", args, run,
            profile_id=profile_id, dbname=dbname, target=target,
            count=lambda r: len(r["items"]),
        )

    @server.tool(annotations=READ_ONLY)
    def meridia_describe_table(
        ctx: Context,
        profile_id: str,
        dbname: str,
        schema: str,
        table: str,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """Ficha completa de una tabla o vista.

        Incluye columnas con tipo y nulabilidad, clave primaria, únicos,
        checks, índices, claves foráneas salientes, las tablas que la
        referencian (`referenced_by`) y las vistas y rutinas que la usan.
        """
        args = {"profile_id": profile_id, "dbname": dbname, "schema": schema, "table": table}

        def run() -> dict:
            snapshot = deps.snapshot(profile_id, dbname, force=force_refresh)
            detail = catalog.table_detail(snapshot, schema, table)
            return {
                "table": detail.table.model_dump(mode="json"),
                "referenced_by": [r.model_dump(mode="json") for r in detail.referenced_by],
                "routines": [r.model_dump(mode="json") for r in detail.routines],
                "views": detail.views,
            }

        return _execute(
            deps, ctx, "meridia_describe_table", args, run,
            profile_id=profile_id, dbname=dbname, target=f"{schema}.{table}",
        )

    @server.tool(annotations=READ_ONLY)
    def meridia_get_relationships(
        ctx: Context,
        profile_id: str,
        dbname: str,
        tables: list[str] | None = None,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """Relaciones entre tablas, con cardinalidad derivada de las claves foráneas.

        `tables` son claves "esquema.tabla"; se devuelven solo las aristas con
        ambos extremos en la lista. Sin `tables`, devuelve el grafo completo de
        la base. La cardinalidad (1:1, N:1, N:M) la deduce Meridia de las FKs y
        de la unicidad de sus columnas: no es una anotación manual.
        """
        args = {"profile_id": profile_id, "dbname": dbname, "tables": tables}

        def run() -> dict:
            snapshot = deps.snapshot(profile_id, dbname, force=force_refresh)
            edges = (
                catalog.relationships_between(snapshot, tables)
                if tables else list(snapshot.relationships)
            )
            return {"relationships": [r.model_dump(mode="json") for r in edges]}

        return _execute(
            deps, ctx, "meridia_get_relationships", args, run,
            profile_id=profile_id, dbname=dbname,
            target=f"{len(tables)} tablas" if tables else "toda la base",
            count=lambda r: len(r["relationships"]),
        )


    @server.tool(annotations=READ_ONLY)
    def meridia_export_erd(
        ctx: Context,
        profile_id: str,
        dbname: str,
        tables: list[str] | None = None,
        format: str = "mermaid",
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """Diagrama entidad-relación de un conjunto de tablas, como texto.

        `format` es "mermaid" (erDiagram, que se renderiza en GitHub, Notion y
        la mayoría de clientes) o "dbml" (dbdiagram.io). Sin `tables` exporta
        la base entera.

        Suele salir más a cuenta que describir tabla por tabla: una sola
        llamada trae columnas, claves y cardinalidades de todo el subconjunto.
        """
        args = {"profile_id": profile_id, "dbname": dbname, "tables": tables, "format": format}

        def run() -> dict[str, Any]:
            snapshot = deps.snapshot(profile_id, dbname, force=force_refresh)
            keys = list(tables) if tables else list(snapshot.tables)
            if len(keys) > MAX_ERD_TABLES:
                raise InvalidRequest(
                    f"El diagrama tendría {len(keys)} tablas (máximo {MAX_ERD_TABLES}).",
                    "Acota con `tables`, o pide primero meridia_list_objects "
                    "para elegir el subconjunto que te interesa.",
                )
            content, extension = catalog.export_model(snapshot, keys, format)
            return {
                "format": format,
                "extension": extension,
                "table_count": len(keys),
                "content": content,
            }

        return _execute(
            deps, ctx, "meridia_export_erd", args, run,
            profile_id=profile_id, dbname=dbname,
            target=f"{len(tables)} tablas" if tables else "toda la base",
            count=lambda r: r["table_count"],
        )

    @server.tool(annotations=READ_ONLY)
    def meridia_explain_query(
        ctx: Context,
        profile_id: str,
        dbname: str,
        sql: str,
        mode: str = "estimated",
    ) -> dict[str, Any]:
        """Plan de ejecución de un SELECT, para diagnosticar rendimiento.

        `mode="estimated"` (por defecto) **no ejecuta la consulta**: solo la
        planifica, así que se puede pedir siempre que haya acceso MCP.
        `mode="actual"` sí la ejecuta para medir tiempos y filas reales, y por
        eso exige el permiso de consultas SQL; la transacción termina en
        rollback pase lo que pase.

        Ni siquiera el plan real devuelve valores de columnas: solo recuentos.
        """
        args = {"profile_id": profile_id, "dbname": dbname, "sql": sql, "mode": mode}
        return _execute(
            deps, ctx, "meridia_explain_query", args,
            lambda: query.explain(deps.profiles, profile_id, dbname, sql, mode=mode),
            profile_id=profile_id, dbname=dbname,
            target=_sql_target(sql),
            # El plan real ejecuta: esa es la frontera, no el hecho de pedir plan.
            needs_query=(mode == "actual"),
            count=lambda r: len(r["nodes"]),
        )

    @server.tool(annotations=READ_ONLY)
    def meridia_run_select(
        ctx: Context,
        profile_id: str,
        dbname: str,
        sql: str,
        max_rows: int = query.MAX_ROWS,
    ) -> dict[str, Any]:
        """Ejecuta una consulta de SOLO LECTURA y devuelve sus filas.

        Admite una única sentencia `SELECT` o `WITH`; nada más, ni siquiera si
        el perfil tiene «Permitir escritura» activado. Tope de
        """ + str(query.MAX_ROWS) + """ filas y 15 s de timeout, y en PostgreSQL
        la transacción es READ ONLY.

        Requiere que el usuario haya activado «Permitir consultas SQL» en
        Meridia; con el acceso MCP a secas solo se ve el catálogo.
        """
        args = {"profile_id": profile_id, "dbname": dbname, "sql": sql, "max_rows": max_rows}
        limit = deps.settings.get().sample_rows
        return _execute(
            deps, ctx, "meridia_run_select", args,
            lambda: query.run_select(
                deps.profiles, profile_id, dbname, sql, max_rows=max_rows
            ),
            profile_id=profile_id, dbname=dbname,
            target=_sql_target(sql),
            needs_query=True,
            count=lambda r: r["row_count"],
            # La muestra deja en la bitácora QUÉ vio el agente, no solo qué
            # pidió. Es lo único que escribe datos del usuario en disco, y por
            # eso se puede apagar con sample_rows: 0.
            sample=lambda r: sample_rows(r["columns"], r["rows"], limit),
        )

    @server.resource(
        "meridia://{profile_id}/{dbname}/{key}",
        name="tabla",
        description=(
            "Ficha de una tabla en texto: columnas con tipo, clave primaria, "
            "claves foráneas y quién la referencia. `key` es «esquema.tabla»."
        ),
        mime_type="text/plain",
    )
    def tabla_resource(profile_id: str, dbname: str, key: str, ctx: Context) -> str:
        """Recurso por tabla, para que el cliente cargue fichas bajo demanda."""
        schema, _, table = key.partition(".")
        args = {"profile_id": profile_id, "dbname": dbname, "key": key}

        def run() -> str:
            snapshot = deps.snapshot(profile_id, dbname)
            return _table_text(catalog.table_detail(snapshot, schema, table))

        return _execute(
            deps, ctx, "resource:tabla", args, run,
            profile_id=profile_id, dbname=dbname, target=key,
        )

    return server


def main() -> None:
    """Entry point stdio. Lo invoca el cliente MCP, no el usuario."""
    create_server().run("stdio")


if __name__ == "__main__":
    main()

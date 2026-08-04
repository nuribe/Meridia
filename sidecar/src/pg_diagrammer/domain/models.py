"""Modelo de dominio de metadatos PostgreSQL (ver docs/pg-diagrammer-diseno.md §2)."""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, model_validator


class SslMode(str, Enum):
    disable = "disable"
    prefer = "prefer"
    require = "require"
    verify_ca = "verify-ca"
    verify_full = "verify-full"


class Engine(str, Enum):
    """Motor de base de datos del perfil. Default postgresql: los perfiles
    antiguos (sin el campo) cargan sin migración."""

    postgresql = "postgresql"
    sqlserver = "sqlserver"


class AuthMethod(str, Enum):
    """Método de autenticación (solo relevante para SQL Server).

    - sql: login de SQL Server (usuario/contraseña, keychain).
    - windows: autenticación integrada de Windows (SSPI si no hay contraseña;
      NTLM con DOMINIO\\usuario + contraseña en otro caso).
    """

    sql = "sql"
    windows = "windows"


class ConnectionParams(BaseModel):
    """Parámetros de conexión para pruebas puntuales (la contraseña nunca se persiste)."""

    host: str
    port: int = 5432
    user: str = ""
    password: str = Field(default="", repr=False)
    dbname: str = "postgres"
    ssl_mode: SslMode = SslMode.prefer
    connect_timeout: int = 8
    engine: Engine = Engine.postgresql
    auth_method: AuthMethod = AuthMethod.sql

    @model_validator(mode="after")
    def _require_user(self):
        # user solo puede omitirse con autenticación integrada de Windows (SSPI).
        if not self.user and self.auth_method != AuthMethod.windows:
            raise ValueError("user es obligatorio salvo con autenticación de Windows")
        return self


class ConnectionProfile(BaseModel):
    """Perfil persistido: la credencial vive en el keychain, aquí solo su referencia."""

    id: str
    name: str
    host: str
    port: int = 5432
    user: str = ""
    ssl_mode: SslMode = SslMode.prefer
    # Base a la que conecta el perfil. Con pgbouncer debe existir en su pool.
    # Default "postgres" para que los perfiles antiguos (sin este campo) carguen.
    dbname: str = "postgres"
    credential_ref: str
    engine: Engine = Engine.postgresql
    auth_method: AuthMethod = AuthMethod.sql
    # Permite ejecutar DDL/DML desde el editor de consultas. Apagado por
    # defecto: sin él, la app se comporta como un visor de solo lectura.
    allow_writes: bool = False


class ProfileCreate(BaseModel):
    name: str
    host: str
    port: int = 5432
    user: str = ""
    # Vacío al editar = conservar la contraseña existente.
    password: str = Field(default="", repr=False)
    dbname: str  # obligatorio: nombre de la base de datos a la que conectar
    ssl_mode: SslMode = SslMode.prefer
    engine: Engine = Engine.postgresql
    auth_method: AuthMethod = AuthMethod.sql
    allow_writes: bool = False

    @model_validator(mode="after")
    def _require_user(self):
        # user solo puede omitirse con autenticación integrada de Windows (SSPI).
        if not self.user and self.auth_method != AuthMethod.windows:
            raise ValueError("user es obligatorio salvo con autenticación de Windows")
        return self


class TableKind(str, Enum):
    table = "table"
    view = "view"
    matview = "matview"
    partitioned = "partitioned"
    foreign = "foreign"


class Column(BaseModel):
    name: str
    position: int
    data_type: str
    is_nullable: bool
    default: str | None = None
    is_pk: bool = False
    comment: str | None = None


class ForeignKey(BaseModel):
    name: str
    columns: list[str]
    ref_schema: str
    ref_table: str
    ref_columns: list[str]
    on_delete: str = "NO ACTION"
    on_update: str = "NO ACTION"


class Index(BaseModel):
    name: str
    columns: list[str]
    is_unique: bool = False
    method: str = "btree"


class Table(BaseModel):
    schema_name: str
    name: str
    oid: int | None = None
    kind: TableKind = TableKind.table
    comment: str | None = None
    estimated_rows: int | None = None
    definition: str | None = None  # SQL de la vista (pg_get_viewdef), solo v/m
    columns: list[Column] = []
    pk: list[str] = []
    unique_sets: list[list[str]] = []
    checks: list[str] = []
    foreign_keys: list[ForeignKey] = []
    indexes: list[Index] = []

    @property
    def key(self) -> str:
        return f"{self.schema_name}.{self.name}"


class Cardinality(str, Enum):
    one_to_one = "1:1"
    many_to_one = "N:1"
    many_to_many = "N:M"


class Relationship(BaseModel):
    """Relación derivada de una FK, con cardinalidad calculada."""

    source: str  # "schema.tabla" (lado FK)
    target: str  # "schema.tabla" (lado referenciado)
    fk_name: str
    columns: list[str] = []
    ref_columns: list[str] = []
    cardinality: Cardinality
    inferred: bool = False


class SchemaInfo(BaseModel):
    name: str
    comment: str | None = None
    table_count: int = 0
    view_count: int = 0


class ObjectSummary(BaseModel):
    schema_name: str
    name: str
    kind: TableKind
    comment: str | None = None
    estimated_rows: int | None = None


class Routine(BaseModel):
    """Función o procedimiento de usuario."""

    schema_name: str
    name: str
    kind: str  # "function" | "procedure"
    language: str
    args: str = ""
    body: str = Field(default="", exclude=True)  # solo para matching interno
    # Valor de `SET search_path` fijado en la propia rutina (vacío si no tiene).
    # Necesario para resolver referencias sin calificar; no se serializa.
    search_path: str = Field(default="", exclude=True)
    # Cómo se detectó el uso de la tabla consultada. Lo rellena `routines_using`;
    # vacío en el snapshot. Valores: "calificada" | "search_path" | "dinamico".
    match_kind: str = ""


class Snapshot(BaseModel):
    """Resultado completo de una introspección, cacheable y serializable."""

    snapshot_id: str
    dbname: str
    created_at: datetime
    schemas: list[SchemaInfo]
    tables: dict[str, Table]  # clave: "schema.nombre"
    relationships: list[Relationship]
    routines: list[Routine] = []
    # "schema.tabla" -> vistas que la referencian (dependencias de pg_rewrite)
    view_usage: dict[str, list[str]] = {}


class DiagramNodePos(BaseModel):
    """Posición y personalización de una tabla en el lienzo de un diagrama."""

    table: str  # "schema.nombre"
    x: float
    y: float
    color: str | None = None
    collapsed: bool = False
    hidden_columns: list[str] = []
    display: str = "default"  # "default" | "all" | "keys"


class DiagramNote(BaseModel):
    """Sticky note dentro de un diagrama."""

    id: str
    text: str = ""
    x: float
    y: float
    width: float = 180
    height: float = 120
    color: str = "#fff9b1"


class DiagramDoc(BaseModel):
    """Documento de diagrama persistido como archivo .pgdiag (JSON versionado)."""

    id: str
    name: str
    profile_id: str
    dbname: str
    format_version: int = 1
    nodes: list[DiagramNodePos] = []
    notes: list[DiagramNote] = []
    updated_at: datetime


class DiagramCreate(BaseModel):
    name: str
    profile_id: str
    dbname: str
    nodes: list[DiagramNodePos] = []
    notes: list[DiagramNote] = []


class QueryJoinSpec(BaseModel):
    """Una relación JOIN del constructor gráfico de consultas."""

    source: str  # "schema.tabla" (lado ya presente en el encadenado)
    target: str  # "schema.tabla" (tabla que se une)
    join_type: str = "INNER JOIN"  # INNER/LEFT/RIGHT/CROSS JOIN
    source_columns: list[str] = []
    target_columns: list[str] = []


class QuerySpec(BaseModel):
    """Diagrama de una consulta: tablas + joins traducibles a SQL."""

    tables: list[str] = []  # ["schema.tabla", ...]; la 1ª es la base sugerida
    aliases: dict[str, str] = {}  # "schema.tabla" -> alias (opcional)
    joins: list[QueryJoinSpec] = []
    select_sql: str | None = None  # lista de columnas del SELECT (default "*")
    tail_sql: str | None = None  # cláusulas tras el encadenado (WHERE/GROUP…)

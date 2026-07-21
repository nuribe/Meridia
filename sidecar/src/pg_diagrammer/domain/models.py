"""Modelo de dominio de metadatos PostgreSQL (ver docs/pg-diagrammer-diseno.md §2)."""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class SslMode(str, Enum):
    disable = "disable"
    prefer = "prefer"
    require = "require"
    verify_ca = "verify-ca"
    verify_full = "verify-full"


class ConnectionParams(BaseModel):
    """Parámetros de conexión para pruebas puntuales (la contraseña nunca se persiste)."""

    host: str
    port: int = 5432
    user: str
    password: str = Field(repr=False)
    dbname: str = "postgres"
    ssl_mode: SslMode = SslMode.prefer
    connect_timeout: int = 8


class ConnectionProfile(BaseModel):
    """Perfil persistido: la credencial vive en el keychain, aquí solo su referencia."""

    id: str
    name: str
    host: str
    port: int = 5432
    user: str
    ssl_mode: SslMode = SslMode.prefer
    # Base a la que conecta el perfil. Con pgbouncer debe existir en su pool.
    # Default "postgres" para que los perfiles antiguos (sin este campo) carguen.
    dbname: str = "postgres"
    credential_ref: str


class ProfileCreate(BaseModel):
    name: str
    host: str
    port: int = 5432
    user: str
    # Vacío al editar = conservar la contraseña existente.
    password: str = Field(default="", repr=False)
    dbname: str  # obligatorio: nombre de la base de datos a la que conectar
    ssl_mode: SslMode = SslMode.prefer


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

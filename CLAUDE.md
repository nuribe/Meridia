# CLAUDE.md — Meridia (pg-diagrammer)

Este documento fija la arquitectura del proyecto. **Cualquier IA o colaborador que trabaje en este repo debe respetarla.** No se debe cambiar el stack, ni la topología de procesos, ni los contratos entre piezas sin aprobación explícita del dueño del proyecto.

## Qué es

Meridia (nombre interno en código: `pg_diagrammer`) es una app de escritorio para conectarse a PostgreSQL y SQL Server, explorar su estructura (`pg_catalog` / `sys.*`) y construir diagramas ER arrastrando tablas a un lienzo, con relaciones y cardinalidad detectadas automáticamente.

## Arquitectura: 3 piezas, NO fusionar

```
app/src-tauri (Rust, Tauri 2)  →  shell nativo: ventana, keychain del SO, lanza el sidecar como proceso hijo
app/src        (React + TS)    →  interfaz: Explorer, DiagramView (React Flow / @xyflow), QueryTab, temas
sidecar/src/pg_diagrammer (Python, FastAPI + psycopg 3) → toda la lógica de metadatos y de negocio
```

- **Shell (Rust/Tauri):** solo ventana, diálogos de archivo, keychain, y arrancar/parar el sidecar. **No debe** contener lógica de introspección ni de dominio.
- **Sidecar (Python):** único dueño de la conexión a PostgreSQL, del modelo de dominio y de los exports. **No debe** desaparecer en favor de lógica en el frontend ni reescribirse en Rust sin decisión explícita (ver docs/pg-diagrammer-diseno.md, "trade-off del sidecar").
- **Frontend (React):** UI y render del canvas (incluye el export WYSIWYG de SVG/PNG, que se hace en el navegador, no en Python). **No debe** hablar directo con PostgreSQL: siempre pasa por la API HTTP del sidecar.

## Comunicación entre piezas — NO cambiar el mecanismo

- Sidecar escucha solo en `127.0.0.1`, puerto **efímero** (no fijo).
- El shell genera un **token de sesión** al arrancar; toda petición HTTP exige el header `X-Session-Token`.
- Nunca exponer el sidecar fuera de localhost. Nunca eliminar el token "para simplificar".

## Credenciales — regla no negociable

- Las contraseñas de conexión **solo** viven en el keychain del SO (vía `keyring` en Python / API nativa de Tauri).
- El archivo de proyecto `.pgdiag` guarda únicamente una referencia (`connection_id`/`credential_ref`), **jamás** la contraseña en texto plano ni cifrada con criptografía propia.
- La app es de **solo lectura** sobre la base del usuario: nunca ejecuta DDL/DML, solo `SELECT` sobre catálogos y datos.

## Persistencia

- Proyectos/diagramas se guardan como archivo **`.pgdiag`** (JSON versionado con `format_version`), no en SQLite ni en el keychain.
- Metadatos se leen en bloque (no `information_schema`, no query-por-tabla): `pg_catalog` en PostgreSQL, catálogos `sys.*` en SQL Server. Ambos motores adaptan sus filas a la MISMA función `assemble()` (introspector.py), que es la única fuente de la derivación de cardinalidad. Se cachean en `MetadataCache` por conexión+schema.
- El perfil lleva `engine` (`postgresql` | `sqlserver`, default postgresql para retrocompatibilidad) y `auth_method` (`sql` | `windows`; Windows integrada solo aplica a SQL Server: SSPI sin contraseña en Windows, NTLM con `DOMINIO\usuario`+contraseña en otro caso).

## Estructura de carpetas (mapa, no reorganizar sin razón)

```
app/                      → frontend + shell Tauri
  src/                     → React (App.tsx, DiagramView.tsx, Explorer.tsx, ObjectTree.tsx, QueryTab.tsx, TableNode.tsx, etc.)
  src/api/client.ts        → único punto de acceso HTTP al sidecar desde el frontend
  src-tauri/               → Rust: lib.rs, main.rs, capabilities/, tauri.conf.json
sidecar/
  src/pg_diagrammer/
    api/                   → FastAPI app + routes (contrato REST, ver docs)
    connections/           → manager.py (pool psycopg, timeouts), profiles.py (perfiles + keyring)
    introspection/         → introspector.py, queries.py (pg_catalog), cache.py, view_joins.py
    domain/                → models.py (Pydantic: ConnectionProfile, Table, Column, ForeignKey, Relationship, Diagram...)
    projects/              → store.py (serialización/migración .pgdiag)
    export/                → generators.py (Mermaid/DBML/JSON — SVG/PNG los genera el frontend)
  tests/                   → pytest (unitarias, integración con testcontainers, performance)
db/init/01-schema.sql      → schema demo para docker-compose
docs/pg-diagrammer-diseno.md → diseño completo, decisiones y trade-offs (fuente de verdad extendida)
```

## Convenciones de dominio que no deben romperse

- Cardinalidad de relaciones (`Relationship`) se **deriva** de FKs, no se pide al usuario:
  FK cuyas columnas = UNIQUE/PK propio → `1:1`; FK normal → `N:1`; FK compuesta parcial de PK (tabla puente) → `N:M`.
- Errores de la API siempre en el mismo *envelope*: `{code, message, hint, retriable}` con códigos como `AUTH_FAILED`, `NETWORK_UNREACHABLE`, `PERMISSION_DENIED`, `TIMEOUT`, `SSL_ERROR`. No introducir formatos de error ad-hoc.
- Base de la API: `http://127.0.0.1:{port}/api/v1`.

## Stack — no sustituir sin decisión explícita

| Capa | Tecnología fija |
|---|---|
| Shell | Tauri 2 (Rust) — no Electron |
| Backend | Python 3.10+, FastAPI, psycopg 3 (PostgreSQL), python-tds (SQL Server), Pydantic, keyring |
| Empaquetado backend | PyInstaller (CI matricial por plataforma) |
| Frontend | React 18 + TypeScript + Vite |
| Canvas de diagramas | @xyflow/react (React Flow) + @dagrejs/dagre para auto-layout |
| Editor SQL | CodeMirror (@codemirror/lang-sql) |
| Export de imagen | html-to-image (en frontend) |

## Metas de rendimiento a respetar en cualquier cambio

- Conexión + listado de bases: < 10 s.
- Introspección de 500 tablas: < 15 s (por eso: queries en bloque, no N+1).
- Diagrama inicial: < 5 s (metadatos ya cacheados).
- ≥ 95 % de FKs detectadas correctamente.
- Export sin pérdida (SVG WYSIWYG desde el propio canvas).

## Pruebas obligatorias antes de cambios estructurales

```bash
cd sidecar && pytest              # unitarias + integración (testcontainers-python con PG 13-17)
cd app && npx tsc --noEmit        # chequeo de tipos del frontend
```

## Regla para agentes de IA

Antes de proponer o aplicar un cambio que toque: el número de procesos/piezas, el mecanismo shell↔sidecar (HTTP+token en localhost), el manejo de credenciales (keychain), el formato de persistencia (`.pgdiag`), o el stack de alguna capa — **detente y pregunta**. Estos son los pilares de diseño documentados en `docs/pg-diagrammer-diseno.md`; cambiarlos sin consenso rompe la arquitectura acordada del proyecto.

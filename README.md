# Meridia

> Orientación y cartografía para tus bases de datos.

Utilidad de escritorio para explorar bases de datos PostgreSQL y construir diagramas ER editables (arrastrar tablas a un lienzo, relaciones con cardinalidad automática).

> Nota: el paquete interno del backend conserva el identificador técnico `pg_diagrammer` (módulo Python, variables `PG_DIAGRAMMER_*`); no lo cambies al renombrar el proyecto.

Arquitectura: **Tauri 2** (shell nativo) + **sidecar Python** (FastAPI + psycopg 3) + **React / React Flow** (lienzo).
Diseño completo: [`docs/pg-diagrammer-diseno.md`](docs/pg-diagrammer-diseno.md).

## Estructura

```
DiagramasBD/
├── sidecar/       # Backend Python: introspección PG, API local con token
├── app/           # Frontend React + shell Tauri (src-tauri/)
├── db/init/       # SQL semilla para el PostgreSQL de desarrollo
├── docker-compose.yml
├── docs/          # Documento de diseño
└── .github/workflows/ci.yml
```

## Requisitos de desarrollo

- Python 3.11+
- Node.js 20+ y npm
- Rust (stable) + prerequisitos de Tauri 2 (<https://tauri.app/start/prerequisites/>)
- Docker (para el PostgreSQL de pruebas)

## Quickstart

No necesitas instalar PostgreSQL ni Docker: la app se conecta a **cualquier servidor
PostgreSQL existente** (local o remoto) con los datos que ingreses en el formulario.

```bash
# 1. Sidecar (backend Python)
cd sidecar
python -m venv .venv && .venv/Scripts/activate   # Windows (Linux/macOS: source .venv/bin/activate)
pip install -e ".[dev]"
pytest                                            # tests
python -m pg_diagrammer                           # arranque manual (imprime {"port": ...})

# 2. App de escritorio (lanza el sidecar automáticamente)
cd ../app
npm install
npm run tauri dev
```

En la pantalla inicial ingresa host, puerto, usuario, contraseña y BD de tu servidor
PostgreSQL y pulsa **Probar**.

### Modo navegador (sin Rust)

Si aún no tienes Rust/cargo instalado, puedes desarrollar sin el shell Tauri:

```bash
# Terminal 1: sidecar (anota el puerto que imprime y el token que muestra por stderr)
cd sidecar
set PG_DIAGRAMMER_TOKEN=dev-token   # PowerShell: $env:PG_DIAGRAMMER_TOKEN="dev-token"
python -m pg_diagrammer

# Terminal 2: frontend
cd app
npm run dev
```

Abre `http://localhost:1420/?port=PUERTO&token=dev-token`. Los valores quedan
guardados en localStorage para las siguientes recargas.

Para la app de escritorio real instala Rust (`winget install Rustlang.Rustup`) y las
Build Tools de C++ (`winget install Microsoft.VisualStudio.2022.BuildTools`, carga
"Desktop development with C++"), y vuelve a `npm run tauri dev`.

En modo dev el shell lanza el sidecar con el intérprete indicado en `PG_DIAGRAMMER_PYTHON`
(por defecto `python`) ejecutando `python -m pg_diagrammer` desde `sidecar/`.
En producción lanza el binario PyInstaller empaquetado como sidecar de Tauri.

## Handshake shell ↔ sidecar

1. El shell genera un token aleatorio y lo pasa por la variable `PG_DIAGRAMMER_TOKEN`.
2. El sidecar escucha en `127.0.0.1` en un puerto efímero e imprime una línea JSON: `{"port": N, "pid": M}`.
3. El shell la lee de stdout y expone `{port, token}` al frontend vía el comando Tauri `sidecar_info`.
4. Toda petición a `/api/v1/*` exige el header `X-Session-Token`. `/health` queda abierto (solo localhost) para liveness.

## BD de ejemplo (opcional)

Para pruebas hay dos alternativas, ambas opcionales:

- **Sembrar el schema demo en un servidor tuyo** (sin psql ni Docker):
  `python sidecar/scripts/seed_demo.py --host HOST --user USUARIO --dbname MI_BD`
  Crea los schemas `ventas` e `inventario` con FKs compuestas, tabla puente, 1:1 y self-reference.
- **Docker** (si lo tienes): `docker compose up -d` levanta PostgreSQL 17 en el puerto 5477
  (usuario `pgdiag`, contraseña `pgdiag_dev`, BD `tienda_demo`). Es lo que usa el CI.

Prueba directa de la API del sidecar:

```bash
curl -s -X POST http://127.0.0.1:<PUERTO>/api/v1/connections/test \
  -H "X-Session-Token: <TOKEN>" -H "Content-Type: application/json" \
  -d '{"host":"HOST","port":5432,"user":"USUARIO","password":"...","dbname":"MI_BD"}'
```

## Fases

- **Fase 0 ✔:** monorepo, handshake con token, `/connections/test` con errores clasificados, BD semilla, CI multiplataforma con PyInstaller.
- **Fase 1 ✔:** perfiles de conexión persistentes (contraseña en keychain del SO, con
  fallback de sesión), listado de BDs, introspección en bloque de `pg_catalog`
  (schemas, tablas, vistas, columnas, PK, FK, índices, checks) con cache de snapshots,
  derivación de cardinalidad (1:1 / N:1), y explorador de objetos con búsqueda,
  filtro por tipo y panel de detalle.
- **Fase 2 ✔:** lienzo de diagramas (React Flow): arrastrar tablas desde el panel al
  lienzo (o doble clic), nodos con columnas y marcadores PK/FK, aristas automáticas
  con cardinalidad (1:1 / N:1) entre las tablas presentes, auto-layout (dagre),
  guardar/abrir diagramas como `.pgdiag`, y export PNG / SVG / JSON.
- **Fase 3 ✔:** personalización de nodos — colapsar, color de cabecera (◐) y
  ocultar/mostrar columnas (✎) — persistida en el `.pgdiag`; export a formatos
  editables Mermaid erDiagram (`.mmd`) y DBML (`.dbml`, compatible con dbdiagram.io).
- **Extras ✔:** pestañas múltiples (diagramas y detalle de tablas), datos con
  paginación/orden/filtros reales en BD, diagrama desde vista con tipos de join,
  4 temas visuales, guardar/abrir diagramas como archivo `.pgdiag` v2
  autocontenido (con diálogos nativos) y visor sin conexión a la BD.
- **Fase 4 ✔:** escala y pulido — refresh con diff visual (objetos nuevos «nueva»,
  modificados «±» y eliminados, señalados en el árbol), render incremental en
  schemas gigantes («Mostrar más» por grupos de 200), benchmark automatizado de
  la meta de rendimiento (500 tablas introspectadas en <15 s; medido ~0,05 s) y
  onboarding en el lienzo vacío.

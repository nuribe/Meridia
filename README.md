# 🐘 Meridia

> Orientación y cartografía para tus bases de datos.

**Meridia** es una aplicación de escritorio que te ayuda a **entender y documentar bases de datos PostgreSQL** sin escribir SQL complicado. Te conectas a tu base de datos, exploras sus tablas como si navegaras un mapa, y armas **diagramas visuales** arrastrando tablas a un lienzo. Meridia dibuja sola las relaciones entre ellas (quién apunta a quién, y con qué cardinalidad: 1:1, N:1, N:M).

Si alguna vez te preguntaste *"¿cómo está organizada esta base de datos?"* o *"¿qué tablas se relacionan con esta?"*, Meridia es para ti.

> ℹ️ El identificador técnico interno del backend sigue siendo `pg_diagrammer` (módulo Python y variables `PG_DIAGRAMMER_*`). Es solo el nombre en código; la app se llama Meridia.

---

## ✨ ¿Qué puedes hacer con Meridia?

- **Conectarte a cualquier PostgreSQL** — local o remoto — con solo llenar un formulario (host, puerto, usuario, contraseña y base de datos). Tu contraseña se guarda de forma segura en el llavero de tu sistema operativo, nunca en texto plano.
- **Explorar tu base de datos** como un árbol: esquemas, tablas, vistas, columnas, llaves primarias (🔑) y foráneas (→), índices y comentarios. Con buscador y filtro por tipo.
- **Ver el detalle de cada tabla**: sus columnas, qué otras tablas la referencian, y qué funciones o procedimientos la usan. Puedes navegar de una tabla a otra siguiendo sus relaciones.
- **Crear diagramas ER** arrastrando tablas al lienzo (o con doble clic). Meridia conecta las tablas automáticamente y marca la cardinalidad de cada relación, incluso las relaciones de una tabla consigo misma.
- **Personalizar el diagrama**: colapsar tablas, cambiar el color de su cabecera, ocultar columnas, añadir notas adhesivas y ordenar todo con un clic (auto-layout).
- **Escribir consultas SQL** de solo lectura, con un editor que colorea la sintaxis y te autocompleta nombres de tablas y columnas (pulsa **TAB** para aceptar la sugerencia).
- **Guardar y compartir**: exporta tus diagramas a **PNG**, **SVG**, **Mermaid** o **DBML**, o guárdalos como archivo `.pgdiag` que luego puedes abrir incluso sin conexión a la base de datos.
- **A tu gusto**: 4 temas visuales, zoom de toda la interfaz y ventana redimensionable.

---

## ✅ Requisitos previos

Meridia se compila desde el código fuente. Necesitas instalar estas herramientas una sola vez:

| Herramienta | Para qué | Cómo obtenerla |
|---|---|---|
| **Node.js 20+** y npm | La interfaz visual | <https://nodejs.org> |
| **Python 3.11+** | El motor que lee tu base de datos | <https://www.python.org/downloads/> |
| **Rust** (stable) | La app de escritorio nativa | <https://rustup.rs> |
| **Build Tools de C++** (solo Windows) | Compilar la app nativa | `winget install Microsoft.VisualStudio.2022.BuildTools` y marca *"Desktop development with C++"* |

> 💡 ¿No tienes Rust todavía? Puedes probar Meridia en modo navegador (ver [Alternativa sin Rust](#-alternativa-sin-rust)) y dejar la app de escritorio para después.

No necesitas instalar PostgreSQL: Meridia se conecta a **cualquier servidor PostgreSQL que ya tengas**.

---

## 🚀 Instalación paso a paso

Abre una terminal en la carpeta del proyecto y sigue estos pasos.

### 1. Prepara el motor (backend Python)

```bash
cd sidecar
python -m venv .venv                 # crea un entorno aislado
.venv\Scripts\activate               # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -e ".[dev]"              # instala las dependencias
```

### 2. Prepara e inicia la app de escritorio

```bash
cd ../app
npm install                          # instala la interfaz (la primera vez tarda un poco)
npm run tauri dev                    # ¡arranca Meridia!
```

La primera vez, `npm run tauri dev` compila la parte nativa (Rust) y puede tardar varios minutos. Las siguientes veces arranca en segundos. Meridia lanza el motor Python automáticamente, así que no tienes que ejecutarlo aparte.

### 3. Conéctate a tu base de datos

En la pantalla inicial, pulsa **➕ Nueva conexión** e ingresa:

- **Host** y **puerto** de tu servidor (por ejemplo `localhost` y `5432`).
- **Usuario** y **contraseña**.
- **Nombre de la base de datos** a la que conectar.

Guarda y ¡listo! Ya puedes explorar y diagramar.

> 🔌 ¿Usas **pgbouncer** u otro *pooler*? Indica en "Nombre de la base de datos" una base que exista en su pool; desde ella Meridia lista las demás del servidor.

---

## 🧭 Primeros pasos (tu primer diagrama en 1 minuto)

1. Entra en tu base de datos y ve a la pestaña **Diagramas**.
2. En el panel izquierdo, **arrastra una tabla** al lienzo (o haz **doble clic** sobre ella).
3. Arrastra otra tabla relacionada: Meridia dibujará sola la flecha con su cardinalidad.
4. ¿Quieres traer todo lo relacionado de golpe? Pulsa el icono **⇲** en un nodo y elige la dirección.
5. Pulsa **⬡ Auto-layout** para ordenar el diagrama, y **💾 Archivo** para guardarlo.
6. Usa el buscador **🔎 Buscar tabla en el diagrama…** para encontrar y centrar cualquier tabla del lienzo.

---

## 🛟 Solución de problemas comunes

- **El arrastrar-y-soltar no funciona en la app de escritorio (Windows).** Ya viene resuelto (`dragDropEnabled: false` en la config). Si compilaste una versión anterior, vuelve a ejecutar `npm run tauri dev` para recompilar. Alternativa siempre válida: **doble clic** en la tabla para agregarla.
- **La lista de bases de datos aparece vacía.** Suele pasar detrás de un *pooler* (pgbouncer). Usa el nombre exacto de tu base en el formulario, o el campo "Conectar por nombre".
- **No recuerda mi contraseña al reiniciar.** Si tu sistema no tiene llavero disponible, la contraseña solo dura la sesión; te la volverá a pedir (nunca se guarda en texto plano).

---

## 🔍 ¿Cómo funciona por dentro?

Meridia son tres piezas trabajando juntas:

- **Shell nativo (Tauri 2, Rust):** la ventana de la app; lanza el motor y hace de puente seguro.
- **Motor (Python + FastAPI + psycopg 3):** se conecta a PostgreSQL, lee su estructura con `pg_catalog` y expone una API local protegida con un token.
- **Interfaz (React + React Flow):** el explorador y el lienzo de diagramas que ves.

El shell y el motor se comunican por `127.0.0.1` con un *handshake*: el shell genera un token aleatorio, el motor abre un puerto efímero y lo anuncia, y toda petición a la API exige ese token. Nada queda expuesto fuera de tu equipo.

Diseño completo en [`docs/pg-diagrammer-diseno.md`](docs/pg-diagrammer-diseno.md).

### 💻 Alternativa sin Rust

¿Aún no instalas Rust? Puedes desarrollar en el navegador:

```bash
# Terminal 1: motor (anota el puerto que imprime)
cd sidecar
set PG_DIAGRAMMER_TOKEN=dev-token    # PowerShell: $env:PG_DIAGRAMMER_TOKEN="dev-token"
python -m pg_diagrammer

# Terminal 2: interfaz
cd app
npm run dev
```

Abre `http://localhost:1420/?port=PUERTO&token=dev-token`.

### 🧪 Base de datos de ejemplo (opcional)

- **Sembrar un schema demo en un servidor tuyo** (sin psql ni Docker):
  `python sidecar/scripts/seed_demo.py --host HOST --user USUARIO --dbname MI_BD`
  Crea los esquemas `ventas` e `inventario` con FKs compuestas, tabla puente, 1:1 y self-reference.
- **Docker** (si lo tienes): `docker compose up -d` levanta PostgreSQL en el puerto 5477 (usuario `pgdiag`, contraseña `pgdiag_dev`, BD `tienda_demo`).

---

## 🌱 Ideas para mejorar (¡anímate a contribuir!)

Meridia ya cubre lo esencial, y hay mucho espacio para crecer. Si quieres aprender o aportar, estas mejoras se apoyan en lo que ya existe y son buenas para empezar:

- **Exportar el diagrama a PDF** además de PNG/SVG.
- **Buscador global** que salte directamente a una tabla desde cualquier pantalla.
- **Guardar "vistas" de diagrama** (grupos de tablas favoritas) para reabrirlas rápido.
- **Comparar dos snapshots** y resaltar los cambios de estructura entre fechas.
- **Modo solo lectura de datos más rico**: paginación infinita, exportar el resultado de una consulta a CSV.
- **Plantillas de color por esquema** para distinguir módulos de un vistazo.
- **Atajos de teclado** para las acciones más usadas del lienzo.
- **Traducir la interfaz** a otros idiomas.

### Cómo contribuir

1. Haz un *fork* del repositorio y crea una rama para tu cambio.
2. Levanta el entorno con los pasos de instalación de arriba.
3. Ejecuta las pruebas antes de proponer cambios:
   ```bash
   cd sidecar && pytest          # pruebas del motor
   cd ../app && npx tsc --noEmit # chequeo de tipos de la interfaz
   ```
4. Abre un *Pull Request* describiendo qué mejora y por qué. ¡Toda idea es bienvenida!

---

## 📌 Estado del proyecto

Meridia atravesó varias fases y hoy es funcional de punta a punta:

- **Base:** conexión a PostgreSQL, introspección de `pg_catalog`, explorador con búsqueda y filtros, perfiles con contraseña en el llavero.
- **Diagramas:** lienzo con arrastrar-y-soltar, relaciones automáticas con cardinalidad (incluidas las reflexivas), auto-layout, personalización de nodos, notas y buscador de nodos.
- **Consultas:** editor SQL de solo lectura con resaltado y autocompletado.
- **Compartir:** guardar/abrir `.pgdiag` (autocontenido y visible sin conexión), export PNG / SVG / Mermaid / DBML, directorio de diagramas configurable.
- **Comodidad:** 4 temas, zoom de la interfaz y ventana redimensionable.

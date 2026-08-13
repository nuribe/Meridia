"""Empaquetado de Meridia como extensión de escritorio (`.mcpb`).

Por qué existe: el `claude_desktop_config.json` no es una vía fiable. En las
instalaciones de Claude Desktop desde la Microsoft Store (MSIX) el archivo se
ignora en silencio —ni un solo `mcp-server-*.log`, ningún error, el servidor
simplemente no aparece— y es un fallo cerrado sin corregir. Además obliga al
usuario a escribir rutas absolutas de Windows con barras dobles en un JSON,
que es justo donde más gente se queda atascada.

Un `.mcpb` es un ZIP con un `manifest.json` en la raíz y el servidor dentro.
Se instala con doble clic. Esa es la promesa que perseguimos: instalar Meridia
y que el conector quede disponible sin editar nada.

El módulo vive en el paquete —y no en `scripts/`— para que las pruebas puedan
importarlo y validar el manifiesto sin ejecutar un build entero.
"""
from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pg_diagrammer import __version__

# Nombre del ejecutable que produce PyInstaller (ver pg-diagrammer-sidecar.spec).
# El mismo binario sirve de sidecar HTTP y, con `--mcp`, de servidor MCP.
EXE_STEM = "pg-diagrammer-sidecar"

# Dentro del bundle todo cuelga de `server/`, como en los ejemplos oficiales.
DIR_SERVIDOR = "server"

# Las 8 herramientas, en el orden en que se usan. Claude Desktop las muestra
# en la pantalla de instalación, así que la descripción es lo que el usuario
# lee antes de decidir si concede acceso: se escribe para él, no para el modelo.
HERRAMIENTAS: list[dict[str, str]] = [
    {"name": "meridia_list_profiles",
     "description": "Lista los perfiles de conexión configurados en Meridia."},
    {"name": "meridia_list_databases",
     "description": "Lista las bases de datos del servidor de un perfil."},
    {"name": "meridia_list_objects",
     "description": "Lista las tablas y vistas de una base de datos."},
    {"name": "meridia_describe_table",
     "description": "Describe una tabla o vista: columnas, claves e índices."},
    {"name": "meridia_get_relationships",
     "description": "Devuelve las relaciones entre tablas con su cardinalidad."},
    {"name": "meridia_export_erd",
     "description": "Exporta como texto el diagrama entidad-relación de unas tablas."},
    {"name": "meridia_explain_query",
     "description": "Devuelve el plan de ejecución de un SELECT."},
    {"name": "meridia_run_select",
     "description": "Ejecuta un SELECT y devuelve sus filas. Desactivado salvo que "
                    "se encienda «consultas SQL» en la pestaña Actividad IA."},
]


@dataclass(frozen=True)
class Bundle:
    """Resultado de un empaquetado, para que quien llame no reconstruya rutas."""

    ruta: Path
    bytes: int
    manifiesto: dict[str, Any]


def manifest(version: str = __version__) -> dict[str, Any]:
    """El `manifest.json` del bundle.

    `command` lleva `.exe` explícito en el bloque de Windows a través de
    `platform_overrides`. La especificación dice que la extensión se añade
    sola en Windows para los servidores de tipo `binary`, pero no cuesta nada
    ser explícito y así el manifiesto describe exactamente lo que se ejecuta.
    El bloque base queda sin extensión para cuando haya binario de macOS.
    """
    base = f"${{__dirname}}/{DIR_SERVIDOR}/{EXE_STEM}"
    return {
        "manifest_version": "0.3",
        "name": "meridia",
        "display_name": "Meridia",
        "version": version,
        "description": "Explora el catálogo de tus bases de datos PostgreSQL y "
                       "SQL Server desde los perfiles de conexión de Meridia.",
        "long_description": (
            "Da acceso de solo lectura al catálogo de las bases de datos que ya "
            "tienes configuradas en Meridia: perfiles, bases, tablas, vistas, "
            "columnas, claves y relaciones con su cardinalidad, más el diagrama "
            "entidad-relación en texto.\n\n"
            "Nunca pide ni recibe contraseñas: las credenciales siguen en el "
            "llavero del sistema y solo las usa Meridia. El acceso está cerrado "
            "hasta que lo abres desde la pestaña «Actividad IA» de la app, donde "
            "además queda registrada cada acción que solicita el asistente."
        ),
        "author": {"name": "Meridia"},
        "icon": "icon.png",
        "keywords": ["postgresql", "sql server", "base de datos", "esquema", "erd"],
        "server": {
            "type": "binary",
            "entry_point": f"{DIR_SERVIDOR}/{EXE_STEM}.exe",
            "mcp_config": {
                "command": base,
                "args": ["--mcp"],
                # `args` se repite en el override: la especificación permite
                # bloques parciales, pero un cliente que sustituya el objeto
                # entero dejaría el servidor sin `--mcp` y arrancaría el
                # sidecar HTTP, que por stdio no habla el protocolo y se
                # quedaría colgado sin decir por qué.
                "platform_overrides": {
                    "win32": {"command": f"{base}.exe", "args": ["--mcp"]}
                },
            },
        },
        "tools": HERRAMIENTAS,
        "tools_generated": False,
        "compatibility": {"platforms": ["win32"]},
        # A propósito sin `user_config`. Se valoró exponer la carpeta de datos
        # como campo opcional, pero eso mete un `${user_config.…}` en la línea
        # de arranque, y si el cliente no resuelve un opcional vacío el
        # servidor no arranca —sin error visible, que es exactamente el modo
        # de fallo del que venimos huyendo—. La carpeta por defecto,
        # ~/.pg-diagrammer, es la que usa la app; quien la mueva puede fijar
        # PG_DIAGRAMMER_DATA_DIR en el entorno del sistema.
    }


def build(
    exe: Path,
    destino: Path,
    *,
    icono: Path | None = None,
    version: str = __version__,
) -> Bundle:
    """Escribe `destino` (.mcpb) con el manifiesto, el ejecutable y el icono.

    Se escribe el ZIP directamente en vez de preparar una carpeta y llamar a
    `mcpb pack`: así la build de Windows no necesita Node ni el CLI de mcpb,
    que es una dependencia más que instalar en cada máquina que compile.
    """
    if not exe.is_file():
        raise FileNotFoundError(
            f"no existe el ejecutable {exe}. Compílalo antes con PyInstaller "
            f"(scripts/build-standalone.ps1 lo hace en su primer paso)."
        )
    datos = manifest(version)
    if icono is None or not icono.is_file():
        # El icono es opcional; si falta, se retira la referencia en vez de
        # dejar un manifiesto que apunta a un archivo inexistente.
        datos.pop("icon", None)

    destino.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(datos, indent=2, ensure_ascii=False))
        z.write(exe, f"{DIR_SERVIDOR}/{EXE_STEM}.exe")
        if "icon" in datos and icono is not None:
            z.write(icono, "icon.png")
    return Bundle(ruta=destino, bytes=destino.stat().st_size, manifiesto=datos)

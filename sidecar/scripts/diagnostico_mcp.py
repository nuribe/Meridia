"""Diagnóstico del conector MCP: por qué el cliente no llega al servidor.

Recorre la cadena entera en el orden en que se rompe, y para en el primer
eslabón que falla en vez de dejarte adivinar:

  0. ¿Hay MÁS DE UN archivo de configuración? (Claude Desktop instalado desde
     la Microsoft Store virtualiza AppData: su botón «Edit Config» abre uno y
     la app lee otro, así que se puede editar el archivo equivocado durante
     horas sin un solo mensaje de error)
  1. ¿Existe el archivo de configuración del cliente?
  2. ¿Es JSON válido?  (el fallo nº 1: `C:\\ruta` con una sola barra invertida
     invalida el archivo y el cliente lo descarta ENTERO, sin avisar)
  3. ¿Tiene una entrada para Meridia?
  4. ¿El `command` que apunta existe en el disco?
  5. ¿Ese comando arranca de verdad y habla el protocolo?
  6. ¿Está encendido el acceso en Meridia, y hay rastro de conexiones previas?

Uso:
    python scripts/diagnostico_mcp.py
    python scripts/diagnostico_mcp.py --config <ruta al json del cliente>
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

OK, MAL, AVISO = "  ok  ", " FALLA", " aviso"


NOMBRE_CFG = "claude_desktop_config.json"


def _candidatas() -> list[tuple[Path, str]]:
    """Rutas donde puede vivir la configuración, la preferida primero.

    En Windows hay dos, y ahí está la trampa: una instalación desde la
    Microsoft Store (MSIX) corre en un contenedor que redirige `%APPDATA%`.
    La app lee la ruta virtualizada, pero su propio botón «Edit Config» abre
    la de siempre. Editar la que no es no da ningún error: simplemente no
    carga ningún servidor.
    """
    if sys.platform == "win32":
        fuera: list[tuple[Path, str]] = []
        local = os.environ.get("LOCALAPPDATA")
        if local:
            for paquete in sorted(Path(local, "Packages").glob("Claude_*")):
                fuera.append((paquete / "LocalCache/Roaming/Claude" / NOMBRE_CFG,
                              "instalación de Microsoft Store (MSIX)"))
        base = os.environ.get("APPDATA")
        if base:
            fuera.append((Path(base, "Claude", NOMBRE_CFG), "instalación de escritorio clásica"))
        return fuera
    if sys.platform == "darwin":
        return [(Path.home() / "Library/Application Support/Claude" / NOMBRE_CFG, "macOS")]
    return [(Path.home() / ".config/Claude" / NOMBRE_CFG, "Linux")]


def _elegir_config() -> tuple[Path | None, list[tuple[Path, str]]]:
    """Devuelve (la que lee la app, todas las que existen)."""
    existen = [(r, q) for r, q in _candidatas() if r.exists()]
    return (existen[0][0] if existen else None), existen


def _datos() -> Path:
    env = os.environ.get("PG_DIAGRAMMER_DATA_DIR")
    return Path(env) if env else Path.home() / ".pg-diagrammer"


def _carpetas_de_log() -> list[Path]:
    """Dónde deja Claude Desktop sus registros, junto a cada configuración."""
    return [ruta.parent / "logs" for ruta, _ in _candidatas()]


def _cola(ruta: Path, lineas: int) -> list[str]:
    try:
        contenido = ruta.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return [f"(no se pudo leer: {exc})"]
    return contenido[-lineas:]


def _buscar(ruta: Path, aguja: str, maximo: int) -> list[str]:
    """Las últimas `maximo` líneas que mencionan `aguja`, en TODO el archivo."""
    try:
        contenido = ruta.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return [f"(no se pudo leer: {exc})"]
    return [ln for ln in contenido if aguja in ln.lower()][-maximo:]


def _mostrar_logs() -> None:
    """Vuelca lo que el cliente registró al intentar lanzar el servidor.

    Es la única prueba directa de que el cliente lo intentó: si Meridia no
    aparece en ningún registro, el cliente nunca leyó la entrada, y el problema
    está en el archivo de configuración —no en el servidor—. Si aparece con un
    error, ese error dice exactamente qué falló.
    """
    print("\nRegistros de Claude Desktop")
    print("─" * 72)
    encontrado = False
    for carpeta in _carpetas_de_log():
        if not carpeta.is_dir():
            continue
        encontrado = True
        print(f"\n  {carpeta}")
        archivos = sorted(p for p in carpeta.iterdir() if p.is_file())
        if not archivos:
            print("      (carpeta vacía: el cliente no ha intentado lanzar")
            print("       NINGÚN servidor MCP desde aquí)")
            continue
        for archivo in archivos:
            info = archivo.stat()
            fecha = datetime.fromtimestamp(info.st_mtime).strftime("%Y-%m-%d %H:%M")
            print(f"      {fecha}  {info.st_size:>9,} B  {archivo.name}")

        for archivo in archivos:
            if archivo.suffix != ".log":
                continue
            propio = "meridia" in archivo.name.lower()
            # Ojo: en el log general hay que buscar en TODO el archivo, no solo
            # en la cola. Si Claude cargó Meridia al arrancar y luego escribió
            # miles de líneas de otros servidores, mirar solo el final da un
            # «sin menciones» que es falso, y manda a arreglar lo que no está roto.
            lineas = _cola(archivo, 60) if propio else _buscar(archivo, "meridia", 25)
            print(f"\n    ── {archivo.name} {'◄ el nuestro' if propio else ''}")
            if not lineas:
                print("       (sin una sola mención a meridia en todo el archivo)")
            for linea in lineas:
                print(f"       {linea[:300]}")
    if not encontrado:
        print("  no hay ninguna carpeta de registros; el cliente nunca ha arrancado")
        print("  desde estas rutas, o es otro cliente (VS Code, Claude Code).")


def _di(estado: str, texto: str) -> None:
    print(f"{estado}  {texto}")


def _entrada_meridia(ruta: Path) -> str:
    """Resume, en una línea, qué dice un archivo de configuración sobre Meridia."""
    try:
        cfg = json.loads(ruta.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        return f"ilegible ({exc})"
    except ValueError as exc:
        return f"JSON INVÁLIDO ({exc})"
    servidores = cfg.get("mcpServers") or cfg.get("servers") or {}
    entrada = servidores.get("meridia")
    if entrada is None:
        return f"SIN entrada «meridia» (hay: {', '.join(servidores) or 'ninguna'})"
    comando = " ".join([entrada.get("command", "")] + list(entrada.get("args") or []))
    return f"meridia → {comando}"


def main() -> int:
    try:
        return _revisar()
    finally:
        _mostrar_logs()


def _revisar() -> int:
    args = sys.argv[1:]
    if "--config" in args:
        ruta, existen = Path(args[args.index("--config") + 1]), []
    else:
        ruta, existen = _elegir_config()
    print("Diagnóstico del conector MCP de Meridia")
    print("─" * 72)

    if len(existen) > 1:
        _di(MAL, "hay VARIAS configuraciones y la app solo lee una:")
        for i, (r, quien) in enumerate(existen):
            marca = "◄ ESTA es la que lee la app" if i == 0 else "  (esta se ignora)"
            print(f"          {r}   [{quien}] {marca}")
            print(f"              {_entrada_meridia(r)}")
        print("\n  Es el fallo de las instalaciones desde la Microsoft Store: el botón")
        print("  «Edit Config» de Claude abre la ruta clásica, pero la app corre en un")
        print("  contenedor que redirige AppData y lee la otra. Copia tu entrada")
        print("  «meridia» a la primera ruta de la lista y reinicia el cliente.")
        print()

    if ruta is None or not ruta.exists():
        _di(MAL, f"no existe el archivo de configuración: {ruta}")
        print("\n  El cliente no tiene nada que leer. Créalo con el script de")
        print("  registro, o usa el botón «Conectar con…» de la pestaña Actividad IA.")
        return 1
    _di(OK, f"configuración encontrada: {ruta}")

    try:
        cfg = json.loads(ruta.read_text(encoding="utf-8-sig"))
    except ValueError as exc:
        _di(MAL, f"el archivo NO es JSON válido: {exc}")
        print("\n  El cliente descarta el archivo entero y no carga ningún servidor,")
        print("  sin mostrar ningún error. La causa habitual es una ruta de Windows")
        print("  con barras simples: hay que escribir C:\\\\ruta\\\\al\\\\exe, con dobles.")
        return 1
    _di(OK, "es JSON válido")

    servidores = cfg.get("mcpServers") or cfg.get("servers") or {}
    entrada = servidores.get("meridia")
    if entrada is None:
        _di(MAL, f"no hay entrada «meridia» (hay: {', '.join(servidores) or 'ninguna'})")
        return 1
    _di(OK, "existe la entrada «meridia»")

    comando = [entrada.get("command", "")] + list(entrada.get("args") or [])
    print(f"        → {' '.join(comando)}")
    if not comando[0] or not Path(comando[0]).exists():
        _di(MAL, f"el ejecutable no existe: {comando[0]!r}")
        print("\n  El cliente intenta lanzarlo, falla al instante y no deja rastro.")
        return 1
    _di(OK, "el ejecutable existe")

    humo = Path(__file__).with_name("smoke_mcp.py")
    # La prueba de humo necesita el SDK `mcp` instalado en el intérprete que la
    # ejecuta. Si la entrada apunta a un python concreto —el del entorno virtual
    # del proyecto—, ese es justo el que lo tiene; usar `sys.executable` haría
    # fallar la prueba por un ModuleNotFoundError que no tiene nada que ver con
    # el problema que estamos buscando.
    interprete = comando[0] if Path(comando[0]).stem.lower().startswith("python") else sys.executable
    res = subprocess.run(
        [interprete, str(humo), *comando],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if res.returncode != 0:
        _di(MAL, "ese comando NO arranca el servidor")
        print((res.stdout + res.stderr).strip()[-1500:])
        return 1
    _di(OK, "el comando arranca y responde el protocolo")

    datos = _datos()
    ajustes = datos / "mcp-settings.json"
    if ajustes.exists():
        s = json.loads(ajustes.read_text(encoding="utf-8"))
        estado = OK if s.get("enabled") else AVISO
        _di(estado, f"acceso MCP: {'encendido' if s.get('enabled') else 'APAGADO'}"
                    f" · consultas SQL: {'sí' if s.get('allow_query') else 'no'}")
    else:
        _di(AVISO, f"no hay ajustes en {datos} (se aplican los de por defecto: acceso cerrado)")

    bitacora = datos / "mcp-activity.jsonl"
    if bitacora.exists():
        n = sum(1 for _ in bitacora.open(encoding="utf-8"))
        _di(OK, f"la bitácora tiene {n} evento(s): algún cliente YA llegó al servidor")
    else:
        _di(AVISO, "la bitácora no existe: ningún cliente ha llegado nunca al servidor")

    print("\nTodo lo comprobable desde aquí está bien.")
    print("Si el cliente sigue sin conectarse, queda una sola causa: no ha releído")
    print("su configuración. Ciérralo del todo —incluido el icono de la bandeja del")
    print("sistema, junto al reloj— y vuelve a abrirlo. Cerrar la ventana no basta.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

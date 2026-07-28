"""Genera NOVEDADES.md: la documentación completa de una build de Meridia.

Es el cuerpo del release en GitHub, un archivo descargable suelto y además
viaja dentro del ZIP portable, así que tiene que explicarse solo: qué versión
es, qué cambió y con todo el detalle, qué trae la descarga y qué sabe hacer la
app. Se ejecuta desde el workflow `standalone.yml` y desde
`scripts/build-standalone.ps1`.

Uso:
    python scripts/release_notes.py --version 0.1.0+build.42 \
        --output dist-standalone/NOVEDADES.md

Sin `--version` compone una de desarrollo a partir de la versión base de
`app/src-tauri/tauri.conf.json`.
"""
from __future__ import annotations

import argparse
import json
import platform
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Tope del cuerpo de un release en GitHub (125 000); se deja margen.
MAX_CHARS = 110_000
# Commits detallados antes de pasar a un simple listado de títulos.
MAX_DETAILED_COMMITS = 40


def git(*args: str, default: str = "") -> str:
    """Ejecuta git en la raíz del repo; devuelve `default` si falla."""
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return default


def base_version() -> str:
    """Versión declarada en el manifiesto de Tauri (fuente de verdad)."""
    conf = ROOT / "app" / "src-tauri" / "tauri.conf.json"
    try:
        return json.loads(conf.read_text(encoding="utf-8")).get("version", "0.0.0")
    except (OSError, json.JSONDecodeError):
        return "0.0.0"


def previous_tag() -> str:
    """Última etiqueta v* anterior a HEAD, si existe."""
    return git("describe", "--tags", "--abbrev=0", "--match", "v*", "HEAD^")


def commits(rev_range: str) -> list[dict]:
    """Commits del rango, con su cuerpo completo (ahí está el porqué)."""
    sep, field = "\x1e", "\x1f"
    raw = git(
        "log",
        "--no-merges",
        f"--format=%H{field}%h{field}%an{field}%ad{field}%s{field}%b{sep}",
        "--date=short",
        rev_range,
    )
    result = []
    for chunk in raw.split(sep):
        chunk = chunk.strip("\n")
        if not chunk.strip():
            continue
        parts = chunk.split(field)
        if len(parts) < 6:
            continue
        result.append(
            {
                "sha": parts[1],
                "author": parts[2],
                "date": parts[3],
                "subject": parts[4].strip(),
                "body": parts[5].strip(),
            }
        )
    return result


# Largo máximo del título de un commit al usarlo como encabezado.
MAX_SUBJECT = 100


def shorten_subject(subject: str) -> tuple[str, str]:
    """Parte un título kilométrico en encabezado + resto.

    Algún commit antiguo trae el mensaje entero en el título; sin esto queda
    un encabezado de diez líneas que rompe la lectura de las notas.
    """
    if len(subject) <= MAX_SUBJECT:
        return subject, ""
    cut = subject.rfind(" ", 0, MAX_SUBJECT)
    if cut < MAX_SUBJECT // 2:
        cut = MAX_SUBJECT
    return subject[:cut].rstrip() + "…", subject[cut:].strip()


def readme_features() -> list[str]:
    """Resumen de funcionalidades tomado del README.

    Se lee de allí a propósito: una lista duplicada en este script quedaría
    desactualizada en dos semanas sin que nadie se diera cuenta.
    """
    readme = ROOT / "README.md"
    try:
        text = readme.read_text(encoding="utf-8")
    except OSError:
        return []
    match = re.search(
        r"^##\s+.*¿Qué puedes hacer con Meridia\?\s*$(.*?)^---\s*$",
        text,
        re.M | re.S,
    )
    if not match:
        return []
    return [
        # Los enlaces internos del README (#seccion) no resuelven fuera de él:
        # se dejan como texto plano.
        re.sub(r"\[([^\]]+)\]\(#[^)]*\)", r"\1", line.rstrip())
        for line in match.group(1).splitlines()
        if line.startswith("- ")
    ]


def artifacts() -> list[tuple[str, int]]:
    """Archivos publicables encontrados, con su tamaño en bytes."""
    patterns = [
        "app/src-tauri/target/release/bundle/nsis/*.exe",
        "app/src-tauri/target/release/bundle/msi/*.msi",
        "app/src-tauri/target/release/bundle/dmg/*.dmg",
        "app/src-tauri/target/release/bundle/appimage/*.AppImage",
        "app/src-tauri/target/release/bundle/deb/*.deb",
        "dist-standalone/*.zip",
    ]
    found: list[tuple[str, int]] = []
    for pattern in patterns:
        for path in sorted(ROOT.glob(pattern)):
            found.append((path.name, path.stat().st_size))
    return found


def human_size(num: int) -> str:
    size = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def tool_version(*cmd: str) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return (out.stdout or out.stderr).strip().splitlines()[0]
    except (subprocess.CalledProcessError, FileNotFoundError, OSError, IndexError):
        return "no disponible"


def build_notes(version: str) -> str:
    sha_full = git("rev-parse", "HEAD", default="desconocido")
    sha = git("rev-parse", "--short=7", "HEAD", default="0000000")
    branch = git("rev-parse", "--abbrev-ref", "HEAD", default="desconocido")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    tag = previous_tag()
    rev_range = f"{tag}..HEAD" if tag else "HEAD"
    since = f"desde la versión **{tag}**" if tag else "(historial completo)"
    log = commits(rev_range)

    out: list[str] = []
    add = out.append

    add(f"# Meridia {version}")
    add("")
    add(f"Compilada el {now} · commit `{sha}` · rama `{branch}`")
    add("")
    add(
        "Este archivo describe **exactamente** lo que incluye esta descarga. "
        "Viene también dentro del ZIP portable."
    )
    add("")

    # --- Qué descargar -----------------------------------------------------
    # Lo primero de todo: GitHub añade siempre "Source code (zip)" y
    # "(tar.gz)" al final de los assets y NO se pueden quitar (no son assets
    # de verdad, los deriva del tag). Como son los dos últimos de la lista y
    # se llaman "Source code", un usuario no técnico se los descarga y se
    # encuentra el repositorio en vez de la aplicación. Al menos, que estas
    # notas digan sin rodeos qué archivo hay que coger.
    files = artifacts()
    installer = next((n for n, _ in files if n.endswith("-setup.exe")), None)
    portable = next((n for n, _ in files if n.endswith(".zip")), None)
    add("## ⬇️ Qué descargar")
    add("")
    add("| Si quieres… | Descarga |")
    add("|---|---|")
    add(f"| **Instalar Meridia** | `{installer or 'Meridia_*_x64-setup.exe'}` |")
    add(f"| **Usarla sin instalar** | `{portable or 'Meridia-portable-win64.zip'}` |")
    add("")
    add(
        "> ⚠️ **`Source code (zip)` y `Source code (tar.gz)` NO son la "
        "aplicación.** Los añade GitHub automáticamente a toda release y "
        "contienen el código fuente del proyecto, no el programa. Ignóralos "
        "salvo que quieras compilar Meridia tú mismo."
    )
    add("")

    # --- Qué cambió --------------------------------------------------------
    add("## 🆕 Qué cambia en esta build")
    add("")
    if not log:
        add(f"Sin cambios registrados {since}.")
        add("")
    else:
        add(f"{len(log)} cambios {since}:")
        add("")
        for c in log[:MAX_DETAILED_COMMITS]:
            subject, overflow = shorten_subject(c["subject"])
            add(f"### {subject}")
            add("")
            add(f"`{c['sha']}` · {c['date']} · {c['author']}")
            body = "\n\n".join(p for p in (overflow, c["body"]) if p)
            if body:
                add("")
                add(body)
            add("")
        rest = log[MAX_DETAILED_COMMITS:]
        if rest:
            add(f"<details><summary>Y {len(rest)} cambios más</summary>")
            add("")
            for c in rest:
                add(f"- `{c['sha']}` {c['subject']}")
            add("")
            add("</details>")
            add("")

    # --- Qué trae la descarga ---------------------------------------------
    add("## 📦 Qué trae la descarga")
    add("")
    add(
        "Ninguna de las dos opciones necesita Python, Node ni Rust: el motor "
        "viaja empaquetado dentro."
    )
    add("")
    add("| Opción | Qué es | Cómo se usa |")
    add("|---|---|---|")
    add(
        "| **Instalador** (`Meridia_*_x64-setup.exe`) | Instalación normal con "
        "acceso directo en el menú de inicio. | Ejecutar y seguir el asistente. "
        "Descarga WebView2 solo si falta. |"
    )
    add(
        "| **ZIP portable** (`Meridia-portable-win64.zip`) | La app sin "
        "instalar. | Descomprimir y ejecutar `Meridia.exe`. Requiere WebView2, "
        "ya incluido en Windows 10 y 11. |"
    )
    add("")
    add("El ZIP portable contiene:")
    add("")
    add("- `Meridia.exe` — la aplicación.")
    add("- `pg-diagrammer-sidecar.exe` — el motor que lee tu base de datos.")
    add("- `NOVEDADES.md` — este archivo.")
    add(
        "- `diagrams/` — biblioteca inicial de diagramas `.pgdiag`, si la build "
        "incluyó alguno. Se abren contra tu propia conexión."
    )
    add("")

    if files:
        add("Archivos publicados en esta build:")
        add("")
        for name, size in files:
            add(f"- `{name}` — {human_size(size)}")
        add("")

    # --- Funcionalidades ---------------------------------------------------
    features = readme_features()
    if features:
        add("## ✨ Qué sabe hacer Meridia")
        add("")
        add("Si es tu primera descarga, esto es lo que encontrarás:")
        add("")
        out.extend(features)
        add("")

    # --- Trazabilidad ------------------------------------------------------
    add("## 🔎 Trazabilidad")
    add("")
    add("| | |")
    add("|---|---|")
    add(f"| Versión | `{version}` |")
    add(f"| Commit | `{sha_full}` |")
    add(f"| Rama | `{branch}` |")
    add(f"| Compilada | {now} |")
    add(f"| Sistema de compilación | {platform.system()} {platform.machine()} |")
    add(f"| Python | {sys.version.split()[0]} |")
    add(f"| Node | {tool_version('node', '--version')} |")
    add(f"| Rust | {tool_version('rustc', '--version')} |")
    add("")
    add(
        "¿Algo no funciona? Indica la versión y el commit de esta tabla al "
        "reportarlo: sin eso no se puede saber qué build estás usando."
    )
    add("")

    text = "\n".join(out)
    if len(text) > MAX_CHARS:
        text = (
            text[:MAX_CHARS]
            + "\n\n> ⚠️ Notas recortadas por longitud. El historial completo "
            "está en el repositorio.\n"
        )
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version",
        default="",
        help="Versión a mostrar. Por defecto, la base + 'dev'.",
    )
    parser.add_argument(
        "--output",
        default="dist-standalone/NOVEDADES.md",
        help="Ruta del archivo a escribir (relativa a la raíz del repo).",
    )
    parser.add_argument(
        "--print", action="store_true", help="Escribir también por stdout."
    )
    args = parser.parse_args()

    version = args.version or f"{base_version()}+dev"
    notes = build_notes(version)

    out_path = Path(args.output)
    if not out_path.is_absolute():
        out_path = ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(notes, encoding="utf-8", newline="\n")

    if args.print:
        sys.stdout.write(notes)
    print(f"\nNOVEDADES.md escrito en {out_path} ({len(notes)} caracteres)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

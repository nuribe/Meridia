"""Genera `dist/meridia.mcpb`: la extensión instalable de Claude Desktop.

Se ejecuta después de PyInstaller, que es quien produce el ejecutable que va
dentro. `scripts/build-standalone.ps1` lo llama solo; esto es para regenerar
el bundle sin repetir la build entera.

Uso:
    python scripts/build_mcpb.py
    python scripts/build_mcpb.py --exe dist/pg-diagrammer-sidecar.exe \
                                 --salida dist/meridia.mcpb --version 0.1.0
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pg_diagrammer import __version__  # noqa: E402
from pg_diagrammer.mcp.bundle import EXE_STEM, build  # noqa: E402

RAIZ = Path(__file__).resolve().parents[1]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--exe", type=Path, default=RAIZ / "dist" / f"{EXE_STEM}.exe",
                   help="ejecutable de PyInstaller a empaquetar")
    p.add_argument("--salida", type=Path, default=RAIZ / "dist" / "meridia.mcpb")
    p.add_argument("--icono", type=Path,
                   default=RAIZ.parent / "app/src-tauri/icons/icon.png")
    p.add_argument("--version", default=__version__,
                   help="versión del bundle; debe ser semver, sin sufijos de build")
    args = p.parse_args()

    try:
        bundle = build(args.exe, args.salida, icono=args.icono, version=args.version)
    except FileNotFoundError as exc:
        print(f"FALLA  {exc}", file=sys.stderr)
        return 1

    print(f"  ok    {bundle.ruta}  ({bundle.bytes / 1_048_576:.1f} MB)")
    print(f"        versión {bundle.manifiesto['version']} · "
          f"{len(bundle.manifiesto['tools'])} herramientas")
    print("\nPara instalarlo: doble clic en el archivo, o en Claude Desktop")
    print("Configuración → Extensiones → Advanced settings → Install Extension…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

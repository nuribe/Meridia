"""Punto de entrada para PyInstaller.

PyInstaller no acepta `python -m paquete`, así que este script mínimo
importa y ejecuta el main real. Se empaqueta con scripts/build-standalone.ps1
(o el workflow de CI) en un único ejecutable `pg-diagrammer-sidecar`.
"""
from pg_diagrammer.main import main

if __name__ == "__main__":
    main()

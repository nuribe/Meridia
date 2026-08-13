"""Ajustes del acceso MCP, compartidos entre la app y los procesos MCP.

El archivo es la única vía de control, y se **relee cuando cambia en disco**:
esa es justamente la función del interruptor. Si se cacheara en memoria, apagar
el acceso desde la app no tendría efecto sobre un VS Code ya abierto, que es el
caso en el que uno lo apaga.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from pydantic import BaseModel, Field

from pg_diagrammer.connections.profiles import default_data_dir

SETTINGS_FILE = "mcp-settings.json"


class McpSettings(BaseModel):
    """Qué puede ver y hacer un cliente MCP.

    Dos interruptores, no uno, porque son dos riesgos distintos: leer el
    catálogo revela la estructura; ejecutar SQL toca los datos. Se puede querer
    lo primero sin lo segundo. Los dos arrancan **apagados**, igual que
    `allow_writes` en los perfiles: conectar un agente a una base de datos es
    una decisión que se toma a propósito, no un efecto secundario de instalar
    el binario.
    """

    enabled: bool = False
    # Vacío = todos los perfiles. Con contenido, solo esos ids.
    allowed_profiles: list[str] = Field(default_factory=list)
    # Ejecutar SELECT y pedir el plan REAL. Aparte de `enabled` a propósito.
    allow_query: bool = False
    # Filas de muestra que `run_select` deja en la bitácora. 0 = ninguna, que
    # es lo sensato con datos regulados: la bitácora es texto plano en disco.
    sample_rows: int = Field(default=3, ge=0, le=20)


class McpSettingsStore:
    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or default_data_dir()
        self.path = self.data_dir / SETTINGS_FILE
        self._lock = threading.Lock()
        self._cached: McpSettings | None = None
        self._stamp: tuple[int, int] | None = None  # (mtime_ns, size)

    def _disk_stamp(self) -> tuple[int, int] | None:
        try:
            st = os.stat(self.path)
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def get(self) -> McpSettings:
        """Ajustes vigentes. Relee el archivo si cambió desde la última vez."""
        with self._lock:
            stamp = self._disk_stamp()
            if self._cached is not None and stamp == self._stamp:
                return self._cached
            settings = McpSettings()
            if stamp is not None:
                try:
                    settings = McpSettings(
                        **json.loads(self.path.read_text(encoding="utf-8"))
                    )
                except (OSError, ValueError):
                    # Archivo corrupto o a medio escribir: se ignora y se
                    # aplica el default, que es el lado seguro (acceso cerrado).
                    settings = McpSettings()
            self._cached = settings
            self._stamp = stamp
            return settings

    def set(self, settings: McpSettings) -> McpSettings:
        with self._lock:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(settings.model_dump(), indent=2), encoding="utf-8"
            )
            # Reemplazo atómico: un lector nunca ve el archivo a medias.
            os.replace(tmp, self.path)
            self._cached = settings
            self._stamp = self._disk_stamp()
            return settings

    def allows(self, profile_id: str) -> bool:
        """¿Puede un cliente MCP tocar este perfil ahora mismo?"""
        settings = self.get()
        if not settings.enabled:
            return False
        return not settings.allowed_profiles or profile_id in settings.allowed_profiles

    def allows_query(self, profile_id: str) -> bool:
        """¿Y ejecutar SQL sobre él? Exige los DOS interruptores."""
        return self.allows(profile_id) and self.get().allow_query

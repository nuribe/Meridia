"""Bitácora append-only de las llamadas MCP.

Formato: un evento JSON por línea en `mcp-activity.jsonl`. La elección importa,
así que conviene dejarla escrita:

- **Append-only y una línea por evento.** Varios procesos MCP escriben a la vez;
  `O_APPEND` serializa las escrituras a nivel de sistema operativo y cada evento
  cabe de sobra en un `write` corto, así que las líneas no se entrelazan. No hace
  falta un lock entre procesos, que sería lo primero que se rompería en Windows.
- **El número de secuencia lo pone el LECTOR, no el escritor.** Un contador
  compartido entre procesos exigiría coordinación; el índice de línea ya es un
  orden total, estable y cronológico, porque el archivo solo crece.
- **El cursor es un número de líneas consumidas.** Si el archivo tiene menos
  líneas que el cursor, hubo rotación: el lector lo detecta y responde con
  `reset=True` en vez de mentir por omisión.

De las llamadas de catálogo no se guarda ni un dato del usuario: ver
`redact_args`. La excepción es `meridia_run_select`, que puede dejar una muestra
acotada de filas — es una decisión consciente para poder auditar qué VIO el
agente, y se apaga con `sample_rows: 0`. Por eso el archivo se crea con permisos
0600.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from pg_diagrammer.connections.profiles import default_data_dir

ACTIVITY_FILE = "mcp-activity.jsonl"
ROTATED_FILE = "mcp-activity.1.jsonl"

# Por encima de este tamaño el archivo rota. Con un evento de ~300 bytes son
# del orden de 17.000 llamadas conservadas, más las del archivo rotado.
MAX_BYTES = 5 * 1024 * 1024

# El SQL se guarda recortado: sirve para auditar qué pidió el agente, no para
# reconstruir un script. El resto de argumentos son nombres de objetos.
MAX_SQL_CHARS = 2000

# Claves que jamás deben acabar en la bitácora, por si una tool futura las
# aceptara por descuido. Es una red de seguridad, no la barrera principal:
# las credenciales viajan por el keychain, nunca como argumento de una tool.
SECRET_KEYS = {"password", "passwd", "secret", "token", "credential", "conninfo"}

# La muestra de filas de `run_select` es para reconocer qué se consultó, no para
# tener una copia del resultado: pocas filas (lo fija `sample_rows` en los
# ajustes) y celdas cortas.
MAX_SAMPLE_CELL_CHARS = 200

STATUS_OK = "ok"
STATUS_ERROR = "error"
STATUS_DENIED = "denied"


class ActivityEvent(BaseModel):
    """Una llamada de un cliente MCP.

    Vive aquí y no en `domain/models.py` a propósito: aquel módulo es el modelo
    de metadatos de la base de datos, y esto es telemetría local de la app.
    """

    ts: str = ""
    client: str = "unknown"
    pid: int = 0
    tool: str
    profile_id: str | None = None
    profile_name: str | None = None
    dbname: str | None = None
    # Objeto sobre el que actúa, ya legible: "public.empleado", "schema=ventas"…
    target: str | None = None
    args: dict = Field(default_factory=dict)
    status: str = STATUS_OK
    elapsed_ms: int = 0
    row_count: int | None = None
    # Columnas y primeras filas de un `run_select`, para poder auditar qué VIO
    # el agente y no solo qué pidió. Ver `sample_rows` en los ajustes: es lo
    # único que mete datos del usuario en este archivo.
    sample_columns: list[str] | None = None
    sample: list[list] | None = None
    # Envelope de error de la API cuando status != "ok".
    error: dict | None = None


def redact_args(args: dict) -> dict:
    """Deja los argumentos en algo que se pueda enseñar sin riesgo.

    Recorta el SQL y elimina cualquier clave que huela a credencial. **Nunca**
    se registran filas de datos: de un resultado solo se guarda `row_count`.
    """
    clean: dict = {}
    for key, value in args.items():
        if key.lower() in SECRET_KEYS:
            clean[key] = "«omitido»"
        elif isinstance(value, str) and len(value) > MAX_SQL_CHARS:
            clean[key] = value[:MAX_SQL_CHARS] + "… (truncado)"
        else:
            clean[key] = value
    return clean


def sample_rows(columns: list[str], rows: list[list], limit: int) -> tuple[list[str] | None, list[list] | None]:
    """Recorta un resultado a una muestra publicable en la bitácora."""
    if limit <= 0 or not rows:
        return None, None
    trimmed = [
        [
            v if not isinstance(v, str) or len(v) <= MAX_SAMPLE_CELL_CHARS
            else v[:MAX_SAMPLE_CELL_CHARS] + "…"
            for v in row
        ]
        for row in rows[:limit]
    ]
    return list(columns), trimmed


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class ActivityLog:
    """Escritura desde los procesos MCP y lectura desde el sidecar.

    La instancia mantiene un índice incremental de offsets de línea para no
    releer y reparsear el archivo entero en cada sondeo del panel.
    """

    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or default_data_dir()
        self.path = self.data_dir / ACTIVITY_FILE
        self.rotated_path = self.data_dir / ROTATED_FILE
        self._lock = threading.Lock()
        self._lines: list[str] = []   # líneas ya leídas, en orden
        self._read_bytes = 0          # cuántos bytes del archivo ya se indexaron

    # -- escritura (proceso MCP) -------------------------------------------

    def append(self, event: ActivityEvent) -> ActivityEvent:
        """Añade un evento. Es el único punto de escritura."""
        if not event.ts:
            event.ts = _now()
        if not event.pid:
            event.pid = os.getpid()
        event.args = redact_args(event.args)
        line = json.dumps(event.model_dump(), ensure_ascii=False, default=str)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._rotate_if_needed()
        # Modo "a": cada write se sitúa al final de forma atómica, también
        # cuando hay varios procesos escribiendo. El modo 0600 solo aplica al
        # crear el archivo; desde que la bitácora puede llevar muestras de
        # filas, su contenido es tan sensible como los datos consultados.
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with open(fd, "w", encoding="utf-8", newline="\n", closefd=True) as fh:
            fh.write(line + "\n")
        return event

    def _rotate_if_needed(self) -> None:
        try:
            if self.path.stat().st_size < MAX_BYTES:
                return
        except OSError:
            return
        try:
            # Si dos procesos rotan a la vez uno de los dos gana y el otro
            # sobrescribe el rotado: se pierde el archivo antiguo, nunca el
            # activo. Es un caso raro y el precio de no usar locks entre
            # procesos, que es lo que se quería evitar.
            os.replace(self.path, self.rotated_path)
        except OSError:
            pass

    # -- lectura (sidecar) --------------------------------------------------

    def _refresh(self) -> bool:
        """Indexa las líneas nuevas. Devuelve True si detectó una rotación."""
        try:
            size = self.path.stat().st_size
        except OSError:
            rotated = self._read_bytes > 0
            self._lines, self._read_bytes = [], 0
            return rotated
        if size < self._read_bytes:
            # El archivo encogió: rotó (o lo limpiaron). Se reindexa entero.
            self._lines, self._read_bytes = [], 0
            rotated = True
        else:
            rotated = False
        if size == self._read_bytes:
            return rotated
        # Binario a propósito: el cursor es un offset de BYTES, y `seek` sobre
        # un archivo de texto solo admite marcas devueltas por su propio
        # `tell()`. Con acentos en los nombres de tabla, mezclar ambas cosas
        # desalinea la lectura.
        with open(self.path, "rb") as fh:
            fh.seek(self._read_bytes)
            chunk = fh.read()
        # Una línea a medio escribir (otro proceso escribiendo justo ahora) se
        # deja fuera del índice: entrará completa en el siguiente sondeo.
        complete, sep, tail = chunk.rpartition(b"\n")
        if sep:
            text = complete.decode("utf-8", errors="replace")
            self._lines.extend(ln for ln in text.split("\n") if ln.strip())
        self._read_bytes = size - len(tail)
        return rotated

    def read(self, since: int = 0, limit: int = 200) -> dict:
        """Eventos posteriores al cursor `since`.

        Devuelve `{events, next_cursor, total, reset}`. `reset=True` avisa al
        cliente de que el cursor que traía ya no es válido (hubo rotación o
        limpieza) y de que lo devuelto empieza desde el principio.
        """
        with self._lock:
            rotated = self._refresh()
            total = len(self._lines)
            start = 0 if (rotated or since > total or since < 0) else since
            reset = start != since
            events = []
            for offset, raw in enumerate(self._lines[start : start + limit], start=start):
                try:
                    data = json.loads(raw)
                except ValueError:
                    continue  # línea corrupta: se salta, no tumba el panel
                data["seq"] = offset + 1
                events.append(data)
            return {
                "events": events,
                "next_cursor": start + len(events),
                "total": total,
                "reset": reset,
            }

    def clear(self) -> None:
        """Vacía la bitácora (botón «Limpiar» del panel)."""
        with self._lock:
            for path in (self.path, self.rotated_path):
                try:
                    path.unlink()
                except OSError:
                    pass
            self._lines, self._read_bytes = [], 0

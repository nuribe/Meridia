"""Persistencia de diagramas como archivos .pgdiag (JSON versionado).

Un archivo por diagrama en <dir>/<id>.pgdiag. El directorio es configurable,
con esta precedencia:
1. Elección explícita del usuario (se recuerda en <data_dir>/settings.json).
2. Variable PG_DIAGRAMMER_DIAGRAMS_DIR (la fija main.py en modo portable:
   carpeta `diagrams` junto al ejecutable empaquetado).
3. Default: <data_dir>/diagrams.
El formato lleva format_version para poder migrar en el futuro.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pg_diagrammer.domain.models import DiagramCreate, DiagramDoc, DiagramNodePos, DiagramNote


class DiagramStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self._settings_path = data_dir / "settings.json"
        self._default_dir = data_dir / "diagrams"
        self.dir = self._load_dir()

    @property
    def default_dir(self) -> Path:
        return self._default_dir

    # --- Directorio configurable ---
    def _load_dir(self) -> Path:
        try:
            if self._settings_path.exists():
                data = json.loads(self._settings_path.read_text(encoding="utf-8"))
                d = data.get("diagrams_dir")
                if d:
                    return Path(d)
        except Exception:
            pass
        env = os.environ.get("PG_DIAGRAMMER_DIAGRAMS_DIR")
        if env:
            return Path(env)
        return self._default_dir

    def set_dir(self, path: str | Path) -> Path:
        """Cambia el directorio de diagramas (lo crea si no existe) y lo persiste."""
        p = Path(path).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        self.dir = p
        self._persist_dir(p)
        return p

    def _persist_dir(self, p: Path) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        data: dict = {}
        if self._settings_path.exists():
            try:
                data = json.loads(self._settings_path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        data["diagrams_dir"] = str(p)
        self._settings_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _path(self, diagram_id: str) -> Path:
        return self.dir / f"{diagram_id}.pgdiag"

    # --- CRUD ---
    def create(self, data: DiagramCreate) -> DiagramDoc:
        doc = DiagramDoc(
            id=uuid.uuid4().hex,
            name=data.name,
            profile_id=data.profile_id,
            dbname=data.dbname,
            nodes=data.nodes,
            notes=data.notes,
            updated_at=datetime.now(timezone.utc),
        )
        self._write(doc)
        return doc

    def update(
        self,
        diagram_id: str,
        name: str,
        nodes: list[DiagramNodePos],
        notes: list[DiagramNote] | None = None,
    ) -> DiagramDoc | None:
        doc = self.get(diagram_id)
        if doc is None:
            return None
        doc.name = name
        doc.nodes = nodes
        doc.notes = notes if notes is not None else doc.notes
        doc.updated_at = datetime.now(timezone.utc)
        self._write(doc)
        return doc

    def get(self, diagram_id: str) -> DiagramDoc | None:
        path = self._path(diagram_id)
        if not path.exists():
            return None
        return DiagramDoc(**json.loads(path.read_text(encoding="utf-8")))

    def list(self, profile_id: str | None = None, dbname: str | None = None) -> list[DiagramDoc]:
        if not self.dir.exists():
            return []
        docs = []
        for path in sorted(self.dir.glob("*.pgdiag")):
            try:
                doc = DiagramDoc(**json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue  # archivo corrupto: se ignora, no se rompe el listado
            if profile_id and doc.profile_id != profile_id:
                continue
            if dbname and doc.dbname != dbname:
                continue
            docs.append(doc)
        return sorted(docs, key=lambda d: d.updated_at, reverse=True)

    def delete(self, diagram_id: str) -> bool:
        path = self._path(diagram_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def _write(self, doc: DiagramDoc) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self._path(doc.id).write_text(
            doc.model_dump_json(indent=2), encoding="utf-8"
        )

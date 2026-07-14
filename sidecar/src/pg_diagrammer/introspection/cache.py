"""Cache de snapshots por (perfil, base de datos).

Un snapshot representa la introspección completa en un instante; la UI
muestra su timestamp y el endpoint /refresh lo invalida explícitamente.
"""
from __future__ import annotations

import threading

from pg_diagrammer.domain.models import Snapshot


class SnapshotCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snapshots: dict[tuple[str, str], Snapshot] = {}

    def get(self, profile_id: str, dbname: str) -> Snapshot | None:
        with self._lock:
            return self._snapshots.get((profile_id, dbname))

    def set(self, profile_id: str, dbname: str, snapshot: Snapshot) -> None:
        with self._lock:
            self._snapshots[(profile_id, dbname)] = snapshot

    def invalidate(self, profile_id: str, dbname: str | None = None) -> None:
        with self._lock:
            if dbname is not None:
                self._snapshots.pop((profile_id, dbname), None)
            else:
                for key in [k for k in self._snapshots if k[0] == profile_id]:
                    self._snapshots.pop(key, None)

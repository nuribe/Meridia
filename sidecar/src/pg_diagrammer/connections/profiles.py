"""Perfiles de conexión persistidos.

Los datos no sensibles van a un JSON en el directorio de datos del usuario;
la contraseña se guarda en el keychain del SO (keyring). Si no hay backend
de keychain disponible (p. ej. Linux sin Secret Service), se usa un
almacén en memoria: el perfil funciona durante la sesión y se pedirá
la contraseña de nuevo al reiniciar.
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import psycopg

from pg_diagrammer.domain.models import ConnectionProfile, ProfileCreate

KEYRING_SERVICE = "pg-diagrammer"


def default_data_dir() -> Path:
    env = os.environ.get("PG_DIAGRAMMER_DATA_DIR")
    if env:
        return Path(env)
    return Path.home() / ".pg-diagrammer"


def _keyring():
    try:
        import keyring
        from keyring.errors import KeyringError  # noqa: F401

        backend = keyring.get_keyring()
        if backend.__class__.__module__.startswith("keyring.backends.fail"):
            return None
        return keyring
    except Exception:
        return None


class ProfileStore:
    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or default_data_dir()
        self.path = self.data_dir / "profiles.json"
        self._profiles: dict[str, ConnectionProfile] = {}
        self._session_passwords: dict[str, str] = {}
        self.keyring_available = _keyring() is not None
        self._load()

    # --- persistencia ---
    def _load(self) -> None:
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for item in data.get("profiles", []):
                profile = ConnectionProfile(**item)
                self._profiles[profile.id] = profile

    def _save(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        payload = {"profiles": [p.model_dump() for p in self._profiles.values()]}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # --- API ---
    def create(self, data: ProfileCreate) -> ConnectionProfile:
        profile_id = uuid.uuid4().hex
        profile = ConnectionProfile(
            id=profile_id,
            name=data.name,
            host=data.host,
            port=data.port,
            user=data.user,
            ssl_mode=data.ssl_mode,
            dbname=data.dbname,
            credential_ref=f"{KEYRING_SERVICE}:{profile_id}",
        )
        self._set_password(profile_id, data.password)
        self._profiles[profile_id] = profile
        self._save()
        return profile

    def update(self, profile_id: str, data: ProfileCreate) -> ConnectionProfile | None:
        """Edita un perfil existente. La contraseña solo se cambia si viene."""
        existing = self._profiles.get(profile_id)
        if existing is None:
            return None
        profile = ConnectionProfile(
            id=profile_id,
            name=data.name,
            host=data.host,
            port=data.port,
            user=data.user,
            ssl_mode=data.ssl_mode,
            dbname=data.dbname,
            credential_ref=existing.credential_ref,
        )
        if data.password:
            self._set_password(profile_id, data.password)
        self._profiles[profile_id] = profile
        self._save()
        return profile

    def list(self) -> list[ConnectionProfile]:
        return sorted(self._profiles.values(), key=lambda p: p.name.lower())

    def get(self, profile_id: str) -> ConnectionProfile | None:
        return self._profiles.get(profile_id)

    def delete(self, profile_id: str) -> bool:
        profile = self._profiles.pop(profile_id, None)
        if profile is None:
            return False
        kr = _keyring()
        if kr is not None:
            try:
                kr.delete_password(KEYRING_SERVICE, profile_id)
            except Exception:
                pass
        self._session_passwords.pop(profile_id, None)
        self._save()
        return True

    def set_session_password(self, profile_id: str, password: str) -> None:
        self._set_password(profile_id, password)

    def _set_password(self, profile_id: str, password: str) -> None:
        kr = _keyring()
        if kr is not None:
            try:
                kr.set_password(KEYRING_SERVICE, profile_id, password)
                return
            except Exception:
                pass
        self._session_passwords[profile_id] = password

    def get_password(self, profile_id: str) -> str | None:
        kr = _keyring()
        if kr is not None:
            try:
                stored = kr.get_password(KEYRING_SERVICE, profile_id)
                if stored is not None:
                    return stored
            except Exception:
                pass
        return self._session_passwords.get(profile_id)

    def conninfo(self, profile: ConnectionProfile, dbname: str, connect_timeout: int = 8) -> str:
        password = self.get_password(profile.id)
        if password is None:
            raise PasswordUnavailable(profile.id)
        return psycopg.conninfo.make_conninfo(
            host=profile.host,
            port=profile.port,
            user=profile.user,
            password=password,
            dbname=dbname,
            sslmode=profile.ssl_mode.value,
            connect_timeout=connect_timeout,
        )


class PasswordUnavailable(Exception):
    """No hay contraseña para el perfil (sin keychain y sesión nueva)."""

    def __init__(self, profile_id: str) -> None:
        self.profile_id = profile_id
        super().__init__(f"Sin contraseña almacenada para el perfil {profile_id}")

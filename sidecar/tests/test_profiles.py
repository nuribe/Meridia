"""Tests del almacén de perfiles (con fallback sin keychain)."""
from pathlib import Path

from fastapi.testclient import TestClient

from pg_diagrammer.api.app import create_app

TOKEN = "t"
H = {"X-Session-Token": TOKEN}


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(TOKEN, data_dir=tmp_path), raise_server_exceptions=False)


def test_crud_perfil(tmp_path):
    c = make_client(tmp_path)
    r = c.post("/api/v1/profiles", headers=H, json={
        "name": "Local", "host": "localhost", "user": "postgres", "password": "s3cret",
    "dbname": "postgres"})
    assert r.status_code == 201
    body = r.json()
    profile = body["profile"]
    assert "password" not in profile            # nunca se expone
    assert "s3cret" not in r.text               # ni aparece serializada
    assert profile["credential_ref"].startswith("pg-diagrammer:")

    r = c.get("/api/v1/profiles", headers=H)
    assert [p["name"] for p in r.json()["profiles"]] == ["Local"]

    # El JSON en disco no contiene la contraseña
    on_disk = (tmp_path / "profiles.json").read_text(encoding="utf-8")
    assert "s3cret" not in on_disk

    r = c.delete(f"/api/v1/profiles/{profile['id']}", headers=H)
    assert r.json()["ok"] is True
    assert c.get("/api/v1/profiles", headers=H).json()["profiles"] == []


def test_persistencia_entre_sesiones(tmp_path):
    c1 = make_client(tmp_path)
    c1.post("/api/v1/profiles", headers=H, json={
        "name": "Prod", "host": "db.example.com", "user": "app", "password": "x",
    "dbname": "postgres"})
    c2 = make_client(tmp_path)  # nueva "sesión" sobre el mismo data_dir
    profiles = c2.get("/api/v1/profiles", headers=H).json()["profiles"]
    assert [p["name"] for p in profiles] == ["Prod"]


def test_password_requerida_sin_keychain(tmp_path):
    """Sin keychain, una sesión nueva no tiene la contraseña: error accionable."""
    c1 = make_client(tmp_path)
    pid = c1.post("/api/v1/profiles", headers=H, json={
        "name": "X", "host": "localhost", "user": "u", "password": "p",
    "dbname": "postgres"}).json()["profile"]["id"]

    c2 = make_client(tmp_path)
    if c2.app.state.profiles.keyring_available:
        return  # con keychain real la contraseña sí persiste
    r = c2.get(f"/api/v1/profiles/{pid}/databases", headers=H)
    assert r.status_code == 409
    assert r.json()["code"] == "PASSWORD_REQUIRED"

    # Reingreso de contraseña y el error desaparece (aunque la conexión falle después)
    r = c2.post(f"/api/v1/profiles/{pid}/password", headers=H, json={"password": "p"})
    assert r.json()["ok"] is True


def test_editar_perfil(tmp_path):
    c = make_client(tmp_path)
    pid = c.post("/api/v1/profiles", headers=H, json={
        "name": "Orig", "host": "h1", "user": "u1", "password": "p1", "dbname": "db1",
    }).json()["profile"]["id"]

    # PUT actualiza campos; sin password se conserva
    r = c.put(f"/api/v1/profiles/{pid}", headers=H, json={
        "name": "Editado", "host": "h2", "user": "u2", "password": "", "dbname": "db2",
    })
    assert r.status_code == 200
    p = r.json()["profile"]
    assert p["name"] == "Editado" and p["host"] == "h2" and p["dbname"] == "db2"
    assert p["id"] == pid  # mismo id

    # persiste en disco
    on_disk = (tmp_path / "profiles.json").read_text(encoding="utf-8")
    assert '"db2"' in on_disk and "Editado" in on_disk

    # PUT a inexistente → 404
    assert c.put("/api/v1/profiles/nope", headers=H, json={
        "name": "x", "host": "h", "user": "u", "password": "", "dbname": "d",
    }).status_code == 404

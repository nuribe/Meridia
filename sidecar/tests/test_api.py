"""Tests del esqueleto: health, token, CORS y clasificación de errores."""
from fastapi.testclient import TestClient

from pg_diagrammer.api.app import create_app

TOKEN = "token-de-prueba"


def make_client() -> TestClient:
    return TestClient(create_app(session_token=TOKEN), raise_server_exceptions=False)


def test_health_abierto_sin_token():
    client = make_client()
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_api_rechaza_sin_token():
    client = make_client()
    r = client.post("/api/v1/connections/test", json={})
    assert r.status_code == 401
    assert r.json()["code"] == "INVALID_TOKEN"


def test_api_rechaza_token_incorrecto():
    client = make_client()
    r = client.post(
        "/api/v1/connections/test",
        json={},
        headers={"X-Session-Token": "otro"},
    )
    assert r.status_code == 401


def test_connections_test_valida_payload():
    client = make_client()
    r = client.post(
        "/api/v1/connections/test",
        json={"host": "x"},
        headers={"X-Session-Token": TOKEN},
    )
    assert r.status_code == 422


def test_preflight_cors_sin_token_pasa():
    client = make_client()
    r = client.options(
        "/api/v1/connections/test",
        headers={
            "Origin": "http://localhost:1420",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-session-token,content-type",
        },
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "http://localhost:1420"


def test_connections_test_host_inexistente_devuelve_error_clasificado():
    client = make_client()
    r = client.post(
        "/api/v1/connections/test",
        json={
            "host": "host-que-no-existe.invalid",
            "user": "u",
            "password": "p",
            "connect_timeout": 2,
        },
        headers={"X-Session-Token": TOKEN},
    )
    assert r.status_code == 400
    body = r.json()
    assert body["ok"] is False
    assert body["code"] in {"HOST_NOT_FOUND", "NETWORK_UNREACHABLE", "TIMEOUT", "CONNECTION_ERROR"}
    assert body["message"]

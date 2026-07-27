"""Tests del CRUD de diagramas (.pgdiag)."""
from pathlib import Path

from fastapi.testclient import TestClient

from pg_diagrammer.api.app import create_app

TOKEN = "t"
H = {"X-Session-Token": TOKEN}

NODES = [
    {"table": "ventas.pedidos", "x": 100, "y": 50},
    {"table": "ventas.clientes", "x": 400, "y": 80},
]


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(TOKEN, data_dir=tmp_path), raise_server_exceptions=False)


def test_crud_diagrama(tmp_path):
    c = make_client(tmp_path)
    r = c.post("/api/v1/diagrams", headers=H, json={
        "name": "Ventas", "profile_id": "p1", "dbname": "tienda", "nodes": NODES,
    })
    assert r.status_code == 201
    doc = r.json()["diagram"]
    assert doc["format_version"] == 1

    # archivo .pgdiag en disco
    assert (tmp_path / "diagrams" / f"{doc['id']}.pgdiag").exists()

    # listar con filtros
    assert len(c.get("/api/v1/diagrams?profile_id=p1&dbname=tienda", headers=H).json()["diagrams"]) == 1
    assert c.get("/api/v1/diagrams?profile_id=otro", headers=H).json()["diagrams"] == []

    # actualizar
    r = c.put(f"/api/v1/diagrams/{doc['id']}", headers=H, json={
        "name": "Ventas v2",
        "nodes": NODES + [{"table": "inventario.productos", "x": 250, "y": 300}],
    })
    updated = r.json()["diagram"]
    assert updated["name"] == "Ventas v2" and len(updated["nodes"]) == 3

    # obtener y borrar
    assert c.get(f"/api/v1/diagrams/{doc['id']}", headers=H).json()["diagram"]["name"] == "Ventas v2"
    assert c.delete(f"/api/v1/diagrams/{doc['id']}", headers=H).json()["ok"] is True
    assert c.get(f"/api/v1/diagrams/{doc['id']}", headers=H).status_code == 404


def test_roundtrip_entre_sesiones(tmp_path):
    c1 = make_client(tmp_path)
    doc = c1.post("/api/v1/diagrams", headers=H, json={
        "name": "Persistente", "profile_id": "p1", "dbname": "d", "nodes": NODES,
    }).json()["diagram"]

    c2 = make_client(tmp_path)  # nueva sesión, mismo data_dir
    loaded = c2.get(f"/api/v1/diagrams/{doc['id']}", headers=H).json()["diagram"]
    assert loaded["nodes"] == doc["nodes"]


def test_personalizacion_persistida(tmp_path):
    """color / collapsed / hidden_columns viajan y persisten en el .pgdiag."""
    c = make_client(tmp_path)
    nodes = [{
        "table": "ventas.pedidos", "x": 10, "y": 20,
        "color": "#0e6b5c", "collapsed": True, "hidden_columns": ["estado", "fecha"],
        "display": "keys",
    }]
    doc = c.post("/api/v1/diagrams", headers=H, json={
        "name": "Custom", "profile_id": "p1", "dbname": "d", "nodes": nodes,
    }).json()["diagram"]
    loaded = c.get(f"/api/v1/diagrams/{doc['id']}", headers=H).json()["diagram"]
    n = loaded["nodes"][0]
    assert n["color"] == "#0e6b5c" and n["collapsed"] is True
    assert n["hidden_columns"] == ["estado", "fecha"]
    assert n["display"] == "keys"


def test_nodos_antiguos_sin_personalizacion(tmp_path):
    """Un .pgdiag guardado antes de la Fase 3 carga con defaults."""
    c = make_client(tmp_path)
    doc = c.post("/api/v1/diagrams", headers=H, json={
        "name": "Legacy", "profile_id": "p1", "dbname": "d",
        "nodes": [{"table": "t.a", "x": 0, "y": 0}],
    }).json()["diagram"]
    n = c.get(f"/api/v1/diagrams/{doc['id']}", headers=H).json()["diagram"]["nodes"][0]
    assert n["color"] is None and n["collapsed"] is False and n["hidden_columns"] == []
    assert n["display"] == "default"


def test_notas_roundtrip(tmp_path):
    """Las sticky notes viajan y persisten en el .pgdiag."""
    c = make_client(tmp_path)
    notes = [{"id": "n1", "text": "Revisar índice", "x": 50, "y": 60,
              "width": 220, "height": 140, "color": "#ffd6a5"}]
    doc = c.post("/api/v1/diagrams", headers=H, json={
        "name": "Con notas", "profile_id": "p1", "dbname": "d",
        "nodes": NODES, "notes": notes,
    }).json()["diagram"]
    loaded = c.get(f"/api/v1/diagrams/{doc['id']}", headers=H).json()["diagram"]
    assert loaded["notes"][0]["text"] == "Revisar índice"
    assert loaded["notes"][0]["color"] == "#ffd6a5"

    # update reemplaza notas
    c.put(f"/api/v1/diagrams/{doc['id']}", headers=H, json={
        "name": "Con notas", "nodes": NODES,
        "notes": [{"id": "n2", "text": "otra", "x": 0, "y": 0}],
    })
    loaded = c.get(f"/api/v1/diagrams/{doc['id']}", headers=H).json()["diagram"]
    assert [n["id"] for n in loaded["notes"]] == ["n2"]
    assert loaded["notes"][0]["width"] == 180  # defaults


def test_directorio_por_defecto(tmp_path):
    c = make_client(tmp_path)
    r = c.get("/api/v1/settings/diagrams-dir", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["is_default"] is True
    assert body["dir"].endswith("diagrams")


def test_cambiar_directorio_y_guardar_ahi(tmp_path):
    c = make_client(tmp_path)
    nuevo = tmp_path / "mis_diagramas"
    r = c.put("/api/v1/settings/diagrams-dir", headers=H, json={"dir": str(nuevo)})
    assert r.status_code == 200
    assert nuevo.exists()

    # se refleja en GET
    assert c.get("/api/v1/settings/diagrams-dir", headers=H).json()["is_default"] is False

    # un diagrama nuevo se guarda en el directorio elegido
    doc = c.post("/api/v1/diagrams", headers=H, json={
        "name": "EnNuevaCarpeta", "profile_id": "p1", "dbname": "d", "nodes": NODES,
    }).json()["diagram"]
    assert (nuevo / f"{doc['id']}.pgdiag").exists()
    assert not (tmp_path / "diagrams" / f"{doc['id']}.pgdiag").exists()


def test_directorio_persiste_entre_sesiones(tmp_path):
    nuevo = tmp_path / "carpeta_persistente"
    c1 = make_client(tmp_path)
    c1.put("/api/v1/settings/diagrams-dir", headers=H, json={"dir": str(nuevo)})
    c1.post("/api/v1/diagrams", headers=H, json={
        "name": "A", "profile_id": "p1", "dbname": "d", "nodes": NODES,
    })

    # nueva sesión: recuerda el directorio y lista lo que hay allí
    c2 = make_client(tmp_path)
    assert c2.get("/api/v1/settings/diagrams-dir", headers=H).json()["dir"] == str(nuevo)
    assert len(c2.get("/api/v1/diagrams", headers=H).json()["diagrams"]) == 1


def test_listar_todos_sin_filtro(tmp_path):
    c = make_client(tmp_path)
    c.post("/api/v1/diagrams", headers=H, json={"name": "A", "profile_id": "p1", "dbname": "d1", "nodes": NODES})
    c.post("/api/v1/diagrams", headers=H, json={"name": "B", "profile_id": "p2", "dbname": "d2", "nodes": NODES})
    # sin filtros: devuelve todos, sin importar profile/dbname
    assert len(c.get("/api/v1/diagrams", headers=H).json()["diagrams"]) == 2


def test_dir_portable_por_env(tmp_path, monkeypatch):
    """PG_DIAGRAMMER_DIAGRAMS_DIR (modo portable) fija la biblioteca por
    defecto, pero settings.json (eleccion del usuario) tiene prioridad."""
    from pg_diagrammer.projects.store import DiagramStore

    portable = tmp_path / "junto-al-exe" / "diagrams"
    portable.mkdir(parents=True)
    monkeypatch.setenv("PG_DIAGRAMMER_DIAGRAMS_DIR", str(portable))

    data_dir = tmp_path / "data"
    store = DiagramStore(data_dir)
    assert store.dir == portable

    # La eleccion explicita del usuario pisa el modo portable.
    elegido = tmp_path / "mis-diagramas"
    store.set_dir(elegido)
    store2 = DiagramStore(data_dir)
    assert store2.dir == elegido

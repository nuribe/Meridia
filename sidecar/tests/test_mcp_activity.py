"""Bitácora de actividad MCP y ajustes de acceso.

La prueba que de verdad importa aquí es la de concurrencia: la bitácora existe
para que VARIOS procesos escriban en ella mientras el sidecar lee. Se comprueba
con procesos reales (`subprocess`), no con hilos, porque el escenario que se
quiere garantizar es el de dos clientes MCP a la vez.
"""
import json
import pathlib
import subprocess
import sys
import tempfile

from fastapi.testclient import TestClient

from pg_diagrammer.activity.log import (
    MAX_SQL_CHARS,
    ActivityEvent,
    ActivityLog,
    redact_args,
)
from pg_diagrammer.activity.settings import McpSettings, McpSettingsStore
from pg_diagrammer.api.app import create_app

H = {"X-Session-Token": "t"}


def _dir() -> pathlib.Path:
    return pathlib.Path(tempfile.mkdtemp())


def _event(tool="meridia_list_objects", **kw) -> ActivityEvent:
    return ActivityEvent(tool=tool, **kw)


# --- bitácora -------------------------------------------------------------

def test_append_y_lectura_con_cursor():
    log = ActivityLog(_dir())
    for i in range(5):
        log.append(_event(target=f"public.t{i}"))

    first = log.read(since=0, limit=2)
    assert [e["seq"] for e in first["events"]] == [1, 2]
    assert first["next_cursor"] == 2
    assert first["total"] == 5
    assert first["reset"] is False

    rest = log.read(since=first["next_cursor"], limit=100)
    assert [e["target"] for e in rest["events"]] == ["public.t2", "public.t3", "public.t4"]
    assert rest["next_cursor"] == 5

    # Sin novedades: no repite lo ya entregado.
    assert log.read(since=5)["events"] == []


def test_lectura_incremental_ve_lo_escrito_despues():
    log = ActivityLog(_dir())
    log.append(_event(target="a"))
    cursor = log.read()["next_cursor"]
    log.append(_event(target="b"))
    nuevo = log.read(since=cursor)
    assert [e["target"] for e in nuevo["events"]] == ["b"]


def test_cursor_invalido_devuelve_reset():
    log = ActivityLog(_dir())
    log.append(_event())
    out = log.read(since=99)
    assert out["reset"] is True
    assert [e["seq"] for e in out["events"]] == [1]


def test_clear_vacia_y_avisa_del_reset():
    log = ActivityLog(_dir())
    log.append(_event())
    cursor = log.read()["next_cursor"]
    log.clear()
    out = log.read(since=cursor)
    assert out["total"] == 0 and out["events"] == [] and out["reset"] is True


def test_rotacion_conserva_el_activo_y_resetea_el_cursor(monkeypatch):
    monkeypatch.setattr("pg_diagrammer.activity.log.MAX_BYTES", 400)
    log = ActivityLog(_dir())
    for i in range(20):
        log.append(_event(target=f"public.tabla_{i}"))
    assert log.rotated_path.exists()
    out = log.read(since=19)
    # El archivo activo es corto tras rotar: el cursor viejo ya no vale.
    assert out["reset"] is True
    assert out["total"] < 20
    assert out["events"], "tras rotar debe seguir habiendo eventos legibles"


def test_linea_a_medio_escribir_no_se_indexa():
    """Un evento incompleto entra en el sondeo siguiente, no roto."""
    log = ActivityLog(_dir())
    log.append(_event(target="completo"))
    log.data_dir.mkdir(parents=True, exist_ok=True)
    with open(log.path, "a", encoding="utf-8") as fh:
        fh.write('{"tool": "a_medias"')  # sin \n final
    assert [e["target"] for e in log.read()["events"]] == ["completo"]
    with open(log.path, "a", encoding="utf-8") as fh:
        fh.write(', "target": "ya_completo", "status": "ok"}\n')
    assert [e["target"] for e in log.read()["events"]] == ["completo", "ya_completo"]


def test_linea_corrupta_no_tumba_la_lectura():
    log = ActivityLog(_dir())
    log.append(_event(target="bueno"))
    with open(log.path, "a", encoding="utf-8") as fh:
        fh.write("{esto no es json}\n")
    log.append(_event(target="otro_bueno"))
    assert [e["target"] for e in log.read()["events"]] == ["bueno", "otro_bueno"]


# --- redacción ------------------------------------------------------------

def test_redaccion_recorta_sql_y_oculta_secretos():
    args = redact_args({
        "sql": "SELECT " + "x" * (MAX_SQL_CHARS + 500),
        "password": "hunter2",
        "conninfo": "host=x password=y",
        "schema": "public",
    })
    assert args["password"] == "«omitido»"
    assert args["conninfo"] == "«omitido»"
    assert args["schema"] == "public"
    assert args["sql"].endswith("… (truncado)")
    assert len(args["sql"]) < MAX_SQL_CHARS + 30


def test_el_evento_registrado_no_lleva_filas_de_datos():
    log = ActivityLog(_dir())
    log.append(_event(
        tool="meridia_run_select",
        args={"sql": "SELECT * FROM empleado", "password": "x"},
        row_count=214,
    ))
    raw = log.path.read_text(encoding="utf-8")
    assert "hunter" not in raw and '"password": "«omitido»"' in raw
    evento = json.loads(raw.strip())
    assert evento["row_count"] == 214
    assert "rows" not in evento


def test_append_rellena_ts_y_pid():
    log = ActivityLog(_dir())
    evento = log.append(_event())
    assert evento.ts and evento.pid > 0


# --- concurrencia entre procesos -----------------------------------------

WRITER = """
import sys
from pg_diagrammer.activity.log import ActivityEvent, ActivityLog
log = ActivityLog(__import__("pathlib").Path(sys.argv[1]))
etiqueta, total = sys.argv[2], int(sys.argv[3])
for i in range(total):
    log.append(ActivityEvent(tool="meridia_list_objects", client=etiqueta, target=str(i)))
"""


def test_dos_procesos_escribiendo_a_la_vez():
    """1000 eventos desde dos procesos: 1000 líneas JSON válidas, sin mezclarse."""
    data_dir = _dir()
    por_proceso = 500
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", WRITER, str(data_dir), etiqueta, str(por_proceso)]
        )
        for etiqueta in ("vscode", "claude")
    ]
    for p in procs:
        assert p.wait(timeout=120) == 0

    log = ActivityLog(data_dir)
    out = log.read(since=0, limit=10_000)
    assert out["total"] == 2 * por_proceso
    assert len(out["events"]) == 2 * por_proceso
    por_cliente: dict[str, int] = {}
    for e in out["events"]:
        por_cliente[e["client"]] = por_cliente.get(e["client"], 0) + 1
    assert por_cliente == {"vscode": por_proceso, "claude": por_proceso}
    # Toda línea del archivo es JSON completo (ninguna escritura se entrelazó).
    for linea in log.path.read_text(encoding="utf-8").splitlines():
        assert json.loads(linea)["tool"] == "meridia_list_objects"


# --- ajustes --------------------------------------------------------------

def test_acceso_mcp_arranca_apagado():
    store = McpSettingsStore(_dir())
    assert store.get().enabled is False
    assert store.allows("cualquiera") is False


def test_el_interruptor_se_ve_desde_otra_instancia():
    """Lo que apaga la app tiene que verlo el proceso MCP, que es otro."""
    data_dir = _dir()
    app_side = McpSettingsStore(data_dir)
    mcp_side = McpSettingsStore(data_dir)
    app_side.set(McpSettings(enabled=True))
    assert mcp_side.allows("perfil-1") is True
    app_side.set(McpSettings(enabled=False))
    assert mcp_side.allows("perfil-1") is False


def test_allowlist_acota_los_perfiles():
    store = McpSettingsStore(_dir())
    store.set(McpSettings(enabled=True, allowed_profiles=["p1"]))
    assert store.allows("p1") is True
    assert store.allows("p2") is False


def test_ajustes_corruptos_cierran_el_acceso():
    data_dir = _dir()
    store = McpSettingsStore(data_dir)
    store.set(McpSettings(enabled=True))
    store.path.write_text("{roto", encoding="utf-8")
    store._cached = None  # fuerza relectura como haría un proceso recién nacido
    assert store.get().enabled is False


# --- rutas ----------------------------------------------------------------

def _client():
    return TestClient(create_app("t", data_dir=_dir()))


def test_rutas_mcp_exigen_token():
    assert _client().get("/api/v1/mcp/activity").status_code == 401


def test_ruta_actividad_devuelve_envelope_y_cursor():
    c = _client()
    r = c.get("/api/v1/mcp/activity", headers=H).json()
    assert r["ok"] is True and r["events"] == [] and r["next_cursor"] == 0


def test_ruta_ajustes_ida_y_vuelta():
    c = _client()
    assert c.get("/api/v1/mcp/settings", headers=H).json()["settings"]["enabled"] is False
    r = c.put("/api/v1/mcp/settings", headers=H, json={"enabled": True}).json()
    assert r["settings"]["enabled"] is True
    assert c.get("/api/v1/mcp/settings", headers=H).json()["settings"]["enabled"] is True


def test_ajuste_parcial_no_pisa_el_otro_campo():
    c = _client()
    c.put("/api/v1/mcp/settings", headers=H, json={"enabled": True})
    r = c.put("/api/v1/mcp/settings", headers=H, json={"allowed_profiles": []}).json()
    assert r["settings"]["enabled"] is True


def test_allowlist_rechaza_perfiles_inexistentes():
    c = _client()
    r = c.put("/api/v1/mcp/settings", headers=H, json={"allowed_profiles": ["fantasma"]})
    assert r.status_code == 422
    assert r.json()["code"] == "VALIDATION"


def test_limpiar_bitacora_desde_la_api():
    c = _client()
    c.app.state.mcp_activity.append(_event())
    assert c.get("/api/v1/mcp/activity", headers=H).json()["total"] == 1
    assert c.delete("/api/v1/mcp/activity", headers=H).json()["ok"] is True
    assert c.get("/api/v1/mcp/activity", headers=H).json()["total"] == 0

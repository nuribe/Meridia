//! Shell Tauri: lanza el sidecar Python, hace el handshake y expone
//! `{port, token}` al frontend mediante el comando `sidecar_info`.
//!
//! Handshake (ver README):
//! 1. Generamos un token aleatorio y lo pasamos por PG_DIAGRAMMER_TOKEN.
//! 2. El sidecar escucha en 127.0.0.1:0 e imprime `{"port": N, "pid": M}` en stdout.
//! 3. Leemos esa línea y guardamos el estado.
//!
//! Modo dev: ejecuta `python -m pg_diagrammer` desde ../sidecar
//!   (intérprete configurable con PG_DIAGRAMMER_PYTHON).
//! Modo prod: ejecuta el binario indicado en PG_DIAGRAMMER_SIDECAR_BIN
//!   (empaquetado por CI con PyInstaller como recurso de la app).

use std::io::{BufRead, BufReader};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use rand::distributions::Alphanumeric;
use rand::Rng;
use serde::Serialize;
use tauri::{Manager, RunEvent, State};

#[derive(Clone, Serialize)]
pub struct SidecarInfo {
    pub port: u16,
    pub token: String,
}

struct SidecarState {
    info: SidecarInfo,
    child: Mutex<Option<Child>>,
}

fn generate_token() -> String {
    rand::thread_rng()
        .sample_iter(&Alphanumeric)
        .take(48)
        .map(char::from)
        .collect()
}

fn spawn_sidecar(token: &str) -> Result<(Child, u16), String> {
    let mut cmd = if let Ok(bin) = std::env::var("PG_DIAGRAMMER_SIDECAR_BIN") {
        Command::new(bin)
    } else {
        // Desarrollo: python -m pg_diagrammer con cwd en ../sidecar
        let python = std::env::var("PG_DIAGRAMMER_PYTHON").unwrap_or_else(|_| "python".into());
        let mut c = Command::new(python);
        c.args(["-m", "pg_diagrammer"]);
        // src-tauri/ -> app/ -> raíz del repo -> sidecar/src en PYTHONPATH
        c.current_dir("../../sidecar");
        c.env("PYTHONPATH", "src");
        c
    };

    let mut child = cmd
        .env("PG_DIAGRAMMER_TOKEN", token)
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|e| format!("No se pudo lanzar el sidecar: {e}"))?;

    let stdout = child
        .stdout
        .take()
        .ok_or("Sin stdout del sidecar".to_string())?;
    let mut line = String::new();
    BufReader::new(stdout)
        .read_line(&mut line)
        .map_err(|e| format!("No se pudo leer el handshake: {e}"))?;

    let value: serde_json::Value = serde_json::from_str(line.trim())
        .map_err(|e| format!("Handshake inválido ({line:?}): {e}"))?;
    let port = value["port"]
        .as_u64()
        .ok_or(format!("Handshake sin puerto: {line:?}"))? as u16;

    Ok((child, port))
}

#[tauri::command]
fn sidecar_info(state: State<SidecarState>) -> SidecarInfo {
    state.info.clone()
}

/// Exporta un archivo con diálogo nativo "Guardar como".
/// El webview no tiene gestor de descargas, así que el frontend envía el
/// contenido en base64 y aquí se elige la ruta y se escribe a disco.
#[tauri::command]
async fn export_file(
    app: tauri::AppHandle,
    filename: String,
    data_base64: String,
) -> Result<Option<String>, String> {
    use base64::Engine;
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(&data_base64)
        .map_err(|e| format!("base64 inválido: {e}"))?;

    tauri::async_runtime::spawn_blocking(move || {
        use tauri_plugin_dialog::DialogExt;
        match app.dialog().file().set_file_name(&filename).blocking_save_file() {
            Some(picked) => {
                let path = picked
                    .into_path()
                    .map_err(|e| format!("ruta inválida: {e}"))?;
                std::fs::write(&path, &bytes).map_err(|e| format!("no se pudo escribir: {e}"))?;
                Ok(Some(path.to_string_lossy().into_owned()))
            }
            None => Ok(None), // usuario canceló
        }
    })
    .await
    .map_err(|e| e.to_string())?
}

#[derive(Clone, Serialize)]
struct ImportedFile {
    name: String,
    data_base64: String,
}

/// Abre un diálogo nativo para elegir un archivo y devuelve su contenido.
#[tauri::command]
async fn import_file(
    app: tauri::AppHandle,
    extensions: Vec<String>,
) -> Result<Option<ImportedFile>, String> {
    use base64::Engine;
    tauri::async_runtime::spawn_blocking(move || {
        use tauri_plugin_dialog::DialogExt;
        let exts: Vec<&str> = extensions.iter().map(|s| s.as_str()).collect();
        match app
            .dialog()
            .file()
            .add_filter("Archivo", &exts)
            .blocking_pick_file()
        {
            Some(picked) => {
                let path = picked.into_path().map_err(|e| format!("ruta inválida: {e}"))?;
                let name = path
                    .file_name()
                    .map(|n| n.to_string_lossy().into_owned())
                    .unwrap_or_default();
                let bytes = std::fs::read(&path).map_err(|e| format!("no se pudo leer: {e}"))?;
                Ok(Some(ImportedFile {
                    name,
                    data_base64: base64::engine::general_purpose::STANDARD.encode(bytes),
                }))
            }
            None => Ok(None),
        }
    })
    .await
    .map_err(|e| e.to_string())?
}

/// Zoom nativo del webview (como Ctrl +/- del navegador). Escala TODA la UI
/// de forma uniforme, de modo que React Flow y sus mediciones de nodo siguen
/// siendo coherentes (a diferencia de la propiedad CSS `zoom`).
#[tauri::command]
fn set_ui_zoom(window: tauri::WebviewWindow, factor: f64) -> Result<(), String> {
    window.set_zoom(factor).map_err(|e| e.to_string())
}

/// Abre un diálogo nativo para elegir una CARPETA. Devuelve la ruta o None.
#[tauri::command]
async fn pick_directory(app: tauri::AppHandle) -> Result<Option<String>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        use tauri_plugin_dialog::DialogExt;
        match app.dialog().file().blocking_pick_folder() {
            Some(picked) => {
                let path = picked
                    .into_path()
                    .map_err(|e| format!("ruta inválida: {e}"))?;
                Ok(Some(path.to_string_lossy().into_owned()))
            }
            None => Ok(None), // usuario canceló
        }
    })
    .await
    .map_err(|e| e.to_string())?
}

pub fn run() {
    let token = generate_token();
    let (child, port) = match spawn_sidecar(&token) {
        Ok(ok) => ok,
        Err(e) => {
            eprintln!("[pg-diagrammer] ERROR: {e}");
            std::process::exit(1);
        }
    };

    let state = SidecarState {
        info: SidecarInfo { port, token },
        child: Mutex::new(Some(child)),
    };

    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(state)
        .invoke_handler(tauri::generate_handler![
            sidecar_info,
            export_file,
            import_file,
            pick_directory,
            set_ui_zoom
        ])
        .build(tauri::generate_context!())
        .expect("error al construir la app Tauri")
        .run(|app_handle, event| {
            if let RunEvent::Exit = event {
                // Apagar el sidecar al salir. El guard del mutex debe soltarse
                // antes de que `state` salga de scope (E0597), por eso el take
                // se hace en una sentencia separada.
                let state: State<SidecarState> = app_handle.state();
                let child = state.child.lock().unwrap().take();
                if let Some(mut child) = child {
                    let _ = child.kill();
                    let _ = child.wait();
                }
            }
        });
}

// PerCell4 Tauri shell.
//
// Spawns the Python backend as a sidecar process on app start, pipes its
// stdout/stderr through Rust logging, and kills it cleanly on window
// close. The frontend talks to the backend over HTTP/WebSocket at
// http://127.0.0.1:8765.

use std::sync::Arc;
use tauri::Manager;
use tauri_plugin_shell::ShellExt;
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tokio::sync::Mutex;

const BACKEND_PORT: &str = "8765";

struct BackendHandle {
    child: Arc<Mutex<Option<CommandChild>>>,
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            let handle = app.handle().clone();

            // Spawn the bundled `percell4-backend` sidecar. The binary
            // is registered under `bundle.externalBin` in tauri.conf.json
            // and must exist at desktop/src-tauri/binaries/ before
            // running `tauri dev` (build it with PyInstaller; see
            // backend/README.md).
            let sidecar = app
                .shell()
                .sidecar("percell4-backend")
                .expect("`percell4-backend` sidecar not found — see backend/README.md")
                .env("PERCELL4_PORT", BACKEND_PORT);

            let (mut rx, child) = sidecar
                .spawn()
                .expect("failed to spawn `percell4-backend` sidecar");

            let child_holder: Arc<Mutex<Option<CommandChild>>> =
                Arc::new(Mutex::new(Some(child)));
            app.manage(BackendHandle { child: child_holder.clone() });

            // Stream stdout / stderr so backend logs surface in the
            // Tauri dev console.
            tauri::async_runtime::spawn(async move {
                while let Some(event) = rx.recv().await {
                    match event {
                        CommandEvent::Stdout(line) => {
                            println!("[backend] {}", String::from_utf8_lossy(&line));
                        }
                        CommandEvent::Stderr(line) => {
                            eprintln!("[backend!] {}", String::from_utf8_lossy(&line));
                        }
                        CommandEvent::Terminated(payload) => {
                            eprintln!("[backend] terminated: {:?}", payload);
                            break;
                        }
                        _ => {}
                    }
                }
                let _ = handle;
            });

            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                let app = window.app_handle();
                if let Some(state) = app.try_state::<BackendHandle>() {
                    let child_holder = state.child.clone();
                    tauri::async_runtime::block_on(async move {
                        if let Some(child) = child_holder.lock().await.take() {
                            let _ = child.kill();
                        }
                    });
                }
            }
        })
        .invoke_handler(tauri::generate_handler![])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

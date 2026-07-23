// delegation-core dashboard — Tauri backend.
//
// Spawns delegation-core's own dashboard_api.py (a small stdlib http.server
// JSON API, see src/delegation_core/dashboard_api.py) as a child process on
// startup, using plain std::process::Command from Rust setup code rather than
// tauri-plugin-shell — this doesn't need the frontend/webview to be allowed to
// execute shell commands, since it's the Rust backend spawning a fixed,
// hardcoded program, not the JS side invoking one. Keeps the capabilities
// file (src-tauri/capabilities/default.json) at its default permissions.
//
// dashboard_api.py prints "dashboard_api listening on http://127.0.0.1:<port>"
// as its first stdout line (it's told to bind port 0, i.e. "pick a free one",
// so the actual port isn't known ahead of time) — that line is parsed to learn
// which port to hand the frontend via the get_api_port command.

use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use tauri::{Manager, State};

struct SidecarState {
    port: u16,
    child: Mutex<Option<Child>>,
}

fn venv_python() -> PathBuf {
    let home = std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .expect("could not determine home directory");
    let mut path = PathBuf::from(home);
    path.push(".delegation_core");
    path.push("venv");
    if cfg!(windows) {
        path.push("Scripts");
        path.push("python.exe");
    } else {
        path.push("bin");
        path.push("python3");
    }
    path
}

fn spawn_dashboard_api() -> (u16, Child) {
    let python = venv_python();
    if !python.exists() {
        panic!(
            "delegation-core venv python not found at {:?}. \
             Run `pip install -e \".[graph]\"` in ~/.delegation_core/venv first.",
            python
        );
    }

    let mut child = Command::new(&python)
        .args(["-m", "delegation_core.dashboard_api", "--port", "0"])
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .expect("failed to spawn delegation_core.dashboard_api");

    let stdout = child.stdout.take().expect("sidecar has no stdout");
    let mut reader = BufReader::new(stdout);
    let mut first_line = String::new();
    reader
        .read_line(&mut first_line)
        .expect("failed to read sidecar's first stdout line");

    // "dashboard_api listening on http://127.0.0.1:<port>"
    let port: u16 = first_line
        .trim()
        .rsplit(':')
        .next()
        .and_then(|p| p.parse().ok())
        .unwrap_or_else(|| panic!("could not parse port from sidecar output: {first_line:?}"));

    (port, child)
}

#[tauri::command]
fn get_api_port(state: State<SidecarState>) -> u16 {
    state.port
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // Works around a WebKitGTK + NVIDIA + Wayland crash observed directly on
    // this project's dev machine ("Error 71: Protocol error dispatching to
    // Wayland display", the whole process dying within ~1s of window creation).
    // WEBKIT_DISABLE_COMPOSITING_MODE=1 also "fixes" the crash but forces a
    // software-rendering fallback that broke this app's flexbox layout (the
    // sidebar silently failed to render) — confirmed by A/B testing both
    // combinations directly. DMABUF-only avoids the crash without that
    // regression. Must be set before the webview is created, so this runs
    // first thing in run(), not conditionally on the platform: harmless on
    // setups that don't need it, matching the standard Tauri-on-Linux advice
    // for this exact NVIDIA/Wayland crash class.
    #[cfg(target_os = "linux")]
    unsafe {
        std::env::set_var("WEBKIT_DISABLE_DMABUF_RENDERER", "1");
    }

    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![get_api_port])
        .setup(|app| {
            let (port, child) = spawn_dashboard_api();
            app.manage(SidecarState {
                port,
                child: Mutex::new(Some(child)),
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            // Kill the sidecar when the window closes — otherwise it's an
            // orphaned process every time the dashboard is closed.
            if let tauri::WindowEvent::Destroyed = event {
                let state = window.state::<SidecarState>();
                let taken = state.child.lock().unwrap().take();
                if let Some(mut child) = taken {
                    let _ = child.kill();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

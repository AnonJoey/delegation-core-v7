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
use tauri_plugin_dialog::{DialogExt, MessageDialogKind};

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

/// Everything that can go wrong starting the sidecar, as data rather than a
/// panic — a packaged app launched by double-clicking an icon has no attached
/// terminal, so a panic is just a silent crash with nothing to show the user.
enum SidecarError {
    VenvMissing(PathBuf),
    SpawnFailed(std::io::Error),
    NoStdout,
    ReadFailed(std::io::Error),
    PortUnparseable(String),
}

impl SidecarError {
    /// Text for the dialog a double-click launch can actually see. Kept generic
    /// for the NoStdout/ReadFailed/PortUnparseable cases (a user with no
    /// terminal attached can't act on a raw io::Error anyway) — the specifics
    /// still go to stderr via log_details(), which matters when there IS a
    /// terminal (npm run tauri dev).
    fn user_message(&self) -> String {
        match self {
            SidecarError::VenvMissing(path) => format!(
                "delegation-core isn't set up yet.\n\n\
                 Expected a Python environment at:\n{}\n\n\
                 Run the delegation-core installer (install.sh / install.bat / \
                 install.command), or `delegation-core setup` in a terminal, \
                 then relaunch this app.",
                path.display()
            ),
            SidecarError::SpawnFailed(e) => format!(
                "Could not start delegation-core's local API process.\n\n{e}\n\n\
                 Try running `delegation-core setup` in a terminal to check for \
                 a broken install, then relaunch this app."
            ),
            SidecarError::NoStdout | SidecarError::ReadFailed(_) | SidecarError::PortUnparseable(_) => {
                "delegation-core's local API process started but didn't report \
                 its port correctly. This usually means the install is corrupted.\n\n\
                 Try re-running the installer, then relaunch this app."
                    .to_string()
            }
        }
    }

    /// The specifics user_message() deliberately omits, for whoever does have
    /// a terminal attached (`npm run tauri dev`, or stderr redirected to a log).
    fn log_details(&self) {
        match self {
            SidecarError::NoStdout => eprintln!("sidecar error: child process has no stdout"),
            SidecarError::ReadFailed(e) => eprintln!("sidecar error: failed to read first stdout line: {e}"),
            SidecarError::PortUnparseable(line) => {
                eprintln!("sidecar error: could not parse port from sidecar output: {line:?}")
            }
            SidecarError::VenvMissing(_) | SidecarError::SpawnFailed(_) => {} // already in user_message()
        }
    }
}

fn spawn_dashboard_api() -> Result<(u16, Child), SidecarError> {
    // NOTE: dashboard_api.py hanging during its own startup (BGE/ChromaDB init)
    // would still block this function forever on read_line() below, with no
    // timeout — the error dialog here only covers "fails fast", not "hangs".
    // Acceptable for now (startup has consistently been ~1-2s in testing); a
    // real fix needs a timeout thread, which is more machinery than this pass
    // covers.
    let python = venv_python();
    if !python.exists() {
        return Err(SidecarError::VenvMissing(python));
    }

    let mut child = Command::new(&python)
        .args(["-m", "delegation_core.dashboard_api", "--port", "0"])
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(SidecarError::SpawnFailed)?;

    let stdout = child.stdout.take().ok_or(SidecarError::NoStdout)?;
    let mut reader = BufReader::new(stdout);
    let mut first_line = String::new();
    reader
        .read_line(&mut first_line)
        .map_err(SidecarError::ReadFailed)?;

    // "dashboard_api listening on http://127.0.0.1:<port>"
    let port: u16 = first_line
        .trim()
        .rsplit(':')
        .next()
        .and_then(|p| p.parse().ok())
        .ok_or_else(|| SidecarError::PortUnparseable(first_line.clone()))?;

    Ok((port, child))
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
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![get_api_port])
        .setup(|app| {
            match spawn_dashboard_api() {
                Ok((port, child)) => {
                    app.manage(SidecarState {
                        port,
                        child: Mutex::new(Some(child)),
                    });
                    Ok(())
                }
                Err(e) => {
                    e.log_details();
                    // blocking_show() waits for the user to dismiss the dialog
                    // before returning — no window exists yet at this point, so
                    // there's nothing else to keep alive; exit right after.
                    app.dialog()
                        .message(e.user_message())
                        .title("delegation-core Dashboard")
                        .kind(MessageDialogKind::Error)
                        .blocking_show();
                    std::process::exit(1);
                }
            }
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

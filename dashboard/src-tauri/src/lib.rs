// delegation-core dashboard — Tauri backend.
//
// Finds the JSON API the frontend talks to. Since v0.11 the delegation-core
// daemon serves that API itself (see daemon_dashboard_port below), because it
// already holds the VaultManager the API needs; this app only spawns its own
// copy when no daemon answers.
//
// That fallback spawns delegation-core's own dashboard_api.py (a small stdlib
// http.server JSON API, see src/delegation_core/dashboard_api.py) as a child
// process, using plain std::process::Command from Rust setup code rather than
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
use std::sync::{mpsc, Mutex};
use std::time::Duration;

use tauri::{Manager, State};
use tauri_plugin_dialog::{DialogExt, MessageDialogKind};

struct SidecarState {
    port: u16,
    child: Mutex<Option<Child>>,
}

fn venv_python() -> Result<PathBuf, SidecarError> {
    let home = std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .map_err(|_| SidecarError::NoHomeDir)?;
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
    Ok(path)
}

/// Port the daemon serves the dashboard API on, when it is serving it.
///
/// Since v0.11 delegation-core runs as one HTTP daemon that already holds a
/// warm VaultManager, and it serves these same routes itself on
/// `dashboard_port`. Spawning the sidecar anyway would put a second copy of
/// BGE-m3 on the GPU (2314 MiB, measured) to answer the same questions, so the
/// daemon is tried first and the sidecar is the fallback for a machine running
/// no daemon.
///
/// The probe is a real GET of /api/status rather than a bare TCP connect: the
/// point is to identify the service, and something else holding the port would
/// pass a connect check and then fail every request the dashboard makes.
fn daemon_dashboard_port() -> Option<u16> {
    let home = std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .ok()?;
    let mut cfg_path = PathBuf::from(home);
    cfg_path.push(".delegation_core");
    cfg_path.push("config.json");

    let raw = std::fs::read_to_string(&cfg_path).ok()?;
    let cfg: serde_json::Value = serde_json::from_str(&raw).ok()?;
    // Absent means a config written before this existed, which the Python side
    // reads as its own default rather than as "off". 0 is the explicit "off".
    let port = cfg
        .get("dashboard_port")
        .and_then(|v| v.as_u64())
        .unwrap_or(8788);
    if port == 0 || port > u16::MAX as u64 {
        return None;
    }
    let port = port as u16;

    if daemon_answers(port) {
        Some(port)
    } else {
        None
    }
}

fn daemon_answers(port: u16) -> bool {
    use std::io::{Read, Write};
    use std::net::{SocketAddr, TcpStream};

    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let Ok(mut stream) = TcpStream::connect_timeout(&addr, DAEMON_PROBE_TIMEOUT) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(DAEMON_PROBE_TIMEOUT));
    let _ = stream.set_write_timeout(Some(DAEMON_PROBE_TIMEOUT));

    let req = format!(
        "GET /api/status HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n"
    );
    if stream.write_all(req.as_bytes()).is_err() {
        return false;
    }
    // Only the status line is needed, and the body is the whole vault status —
    // read a bounded prefix instead of to_end().
    let mut buf = [0u8; 64];
    let Ok(n) = stream.read(&mut buf) else {
        return false;
    };
    String::from_utf8_lossy(&buf[..n]).starts_with("HTTP/1.0 200")
        || String::from_utf8_lossy(&buf[..n]).starts_with("HTTP/1.1 200")
}

/// Short on purpose: this runs before any window exists, against a loopback
/// port that is either answering or not. A daemon too busy to answer in this
/// window is treated as absent, which costs a sidecar, not a failure.
const DAEMON_PROBE_TIMEOUT: Duration = Duration::from_secs(2);

/// Everything that can go wrong starting the sidecar, as data rather than a
/// panic — a packaged app launched by double-clicking an icon has no attached
/// terminal, so a panic is just a silent crash with nothing to show the user.
enum SidecarError {
    NoHomeDir,
    VenvMissing(PathBuf),
    SpawnFailed(std::io::Error),
    NoStdout,
    ReadFailed(std::io::Error),
    PortUnparseable(String),
    StartupTimeout,
}

impl SidecarError {
    /// Text for the dialog a double-click launch can actually see. Kept generic
    /// for the NoStdout/ReadFailed/PortUnparseable cases (a user with no
    /// terminal attached can't act on a raw io::Error anyway) — the specifics
    /// still go to stderr via log_details(), which matters when there IS a
    /// terminal (npm run tauri dev).
    fn user_message(&self) -> String {
        match self {
            SidecarError::NoHomeDir => "delegation-core could not determine your home \
                 directory (neither HOME nor USERPROFILE is set), so it doesn't know \
                 where to find its Python environment.\n\n\
                 Try relaunching this app from a normal desktop session or terminal."
                .to_string(),
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
            SidecarError::StartupTimeout => format!(
                "delegation-core's local API process didn't finish starting up \
                 within {} seconds — it normally takes a few seconds.\n\n\
                 Try relaunching this app; if it keeps happening, re-run the \
                 delegation-core installer.",
                SIDECAR_STARTUP_TIMEOUT.as_secs()
            ),
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
            SidecarError::StartupTimeout => eprintln!(
                "sidecar error: no port line on stdout within {}s (BGE/ChromaDB init wedged?)",
                SIDECAR_STARTUP_TIMEOUT.as_secs()
            ),
            SidecarError::NoHomeDir | SidecarError::VenvMissing(_) | SidecarError::SpawnFailed(_) => {} // already in user_message()
        }
    }
}

/// Generous vs. the observed ~2-4s BGE/ChromaDB init, so it only ever fires on
/// a genuinely wedged sidecar, never as a false positive on a slow disk.
const SIDECAR_STARTUP_TIMEOUT: Duration = Duration::from_secs(120);

fn spawn_dashboard_api() -> Result<(u16, Child), SidecarError> {
    let python = venv_python()?;
    if !python.exists() {
        return Err(SidecarError::VenvMissing(python));
    }

    // --parent-pid arms a watchdog in dashboard_api.py that exits when this
    // process dies — the on_window_event(Destroyed) kill below never fires on
    // a hard kill (SIGKILL/crash), and each orphaned sidecar holds ~600MB of
    // GPU memory. Chosen over Linux-only prctl(PR_SET_PDEATHSIG) because it's
    // one portable mechanism for all three shipped platforms and needs no new
    // crate dependency (prctl would require libc + unsafe pre_exec).
    let mut child = Command::new(&python)
        .args(["-m", "delegation_core.dashboard_api", "--port", "0", "--parent-pid"])
        .arg(std::process::id().to_string())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(SidecarError::SpawnFailed)?;

    match read_sidecar_port(&mut child) {
        Ok(port) => Ok((port, child)),
        Err(e) => {
            // Every post-spawn failure exits via the error dialog — reap the
            // child here or it outlives the app as an orphan holding the GPU.
            let _ = child.kill();
            let _ = child.wait();
            Err(e)
        }
    }
}

/// Reads the "dashboard_api listening on http://127.0.0.1:<port>" line, but
/// bounded: read_line() has no timeout of its own, so a sidecar that wedges
/// during BGE/ChromaDB init would otherwise hang this (pre-window) startup
/// forever with nothing on screen. The reader thread is left parked on a
/// timeout; the caller's kill() closes the pipe, which unblocks and ends it.
fn read_sidecar_port(child: &mut Child) -> Result<u16, SidecarError> {
    let stdout = child.stdout.take().ok_or(SidecarError::NoStdout)?;
    let (tx, rx) = mpsc::channel();
    std::thread::spawn(move || {
        let mut reader = BufReader::new(stdout);
        let mut first_line = String::new();
        let _ = tx.send(reader.read_line(&mut first_line).map(|_| first_line));
    });

    let first_line = match rx.recv_timeout(SIDECAR_STARTUP_TIMEOUT) {
        Ok(Ok(line)) => line,
        Ok(Err(e)) => return Err(SidecarError::ReadFailed(e)),
        Err(_) => return Err(SidecarError::StartupTimeout),
    };

    first_line
        .trim()
        .rsplit(':')
        .next()
        .and_then(|p| p.parse().ok())
        .ok_or(SidecarError::PortUnparseable(first_line))
}

#[tauri::command]
fn get_api_port(state: State<SidecarState>) -> u16 {
    state.port
}

#[tauri::command]
fn minimize_window(window: tauri::Window) {
    let _ = window.minimize();
}

#[tauri::command]
fn toggle_maximize_window(window: tauri::Window) {
    if let Ok(is_maximized) = window.is_maximized() {
        if is_maximized {
            let _ = window.unmaximize();
        } else {
            let _ = window.maximize();
        }
    }
}

#[tauri::command]
fn close_window(window: tauri::Window) {
    let _ = window.close();
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
        .invoke_handler(tauri::generate_handler![
            get_api_port,
            minimize_window,
            toggle_maximize_window,
            close_window
        ])
        .setup(|app| {
            // The daemon serving these routes is the normal case; the sidecar
            // is what happens when there is no daemon to ask.
            if let Some(port) = daemon_dashboard_port() {
                app.manage(SidecarState {
                    port,
                    child: Mutex::new(None),
                });
                return Ok(());
            }
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
                    // Reap, don't just signal — an unreaped child is a zombie
                    // for as long as this process lives, which matters in dev
                    // (webview reloads / multi-window churn), and wait() after
                    // SIGKILL returns promptly.
                    let _ = child.wait();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

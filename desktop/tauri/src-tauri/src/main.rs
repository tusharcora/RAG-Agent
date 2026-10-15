// No console window alongside the app on Windows release builds.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU16, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::thread;
use std::time::Duration;

use knowledge_agent_lib::backend::{self, Backend, LogFile};
use serde::Serialize;
use tauri::webview::NewWindowResponse;
use tauri::{AppHandle, Emitter, Manager, RunEvent, Url, WebviewWindowBuilder, WindowEvent};

/// Global constraint: health poll timeout in the shell.
const HEALTH_TIMEOUT: Duration = Duration::from_secs(90);
const MAIN: &str = "main";

#[derive(Clone, Serialize)]
#[serde(tag = "state", rename_all = "lowercase")]
enum Status {
    Starting,
    Ready { url: String },
    Failed { lines: Vec<String>, log_path: Option<String> },
}

struct Shell {
    backend: Mutex<Option<Arc<Backend>>>,
    status: Mutex<Status>,
    /// The backend port the window may navigate to; 0 until it's healthy.
    port: Arc<AtomicU16>,
    quitting: AtomicBool,
    /// <app log dir>/backend.log, truncated at each app start. Unset if it
    /// couldn't be created; logging then goes to stderr only.
    log: OnceLock<LogFile>,
}

/// A shell message, to stderr and the persistent log.
fn note(app: &AppHandle, message: &str) {
    let line = format!("[shell] {message}");
    eprintln!("{line}");
    if let Some(log) = app.state::<Shell>().log.get() {
        log.line(&line);
    }
}

/// http(s) and mailto links leave the app for the system's handler.
fn open_outside(app: &AppHandle, url: &Url) {
    if matches!(url.scheme(), "http" | "https" | "mailto") {
        note(app, &format!("opening outside the app: {url}"));
        if let Err(e) = tauri_plugin_opener::open_url(url.as_str(), None::<&str>) {
            note(app, &format!("couldn't open {url}: {e}"));
        }
    }
}

fn lock<T>(m: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|e| e.into_inner())
}

fn backend_dir(app: &AppHandle) -> Result<PathBuf, String> {
    let dir = app.path().resource_dir().map_err(|e| format!("no resource dir: {e}"))?;
    Ok(dir.join("resources").join("backend"))
}

/// Start the backend on a background thread so the loading page paints
/// first; navigate to it when healthy, else report `backend-failed`.
fn start_backend(app: AppHandle) {
    *lock(&app.state::<Shell>().status) = Status::Starting;
    thread::spawn(move || {
        let shell = app.state::<Shell>();
        let started = backend_dir(&app).and_then(|dir| {
            let b = Arc::new(backend::spawn_logged(&dir, shell.log.get().cloned())?);
            *lock(&shell.backend) = Some(b.clone());
            // Exit may have run between spawn and the store above, finding
            // nothing to stop. Whichever of us takes it from the slot stops it.
            if shell.quitting.load(Ordering::SeqCst) {
                if let Some(b) = lock(&shell.backend).take() {
                    b.shutdown();
                }
                return Err("quitting".to_string());
            }
            let healthy = b.wait_healthy(HEALTH_TIMEOUT);
            Ok((b, healthy))
        });
        if shell.quitting.load(Ordering::SeqCst) {
            return; // exit handler owns shutdown now
        }
        let lines = match started {
            Ok((b, Ok(()))) => {
                let url = b.url();
                shell.port.store(b.port(), Ordering::SeqCst);
                *lock(&shell.status) = Status::Ready { url: url.clone() };
                if let (Some(window), Ok(parsed)) = (app.get_webview_window(MAIN), Url::parse(&url)) {
                    if let Err(e) = window.navigate(parsed) {
                        note(&app, &format!("navigate failed: {e}"));
                    }
                }
                return;
            }
            Ok((b, Err(err))) => {
                let mut lines = b.last_log_lines();
                lines.push(format!("Error: {err}"));
                // Stop it before offering a retry, so two launchers never
                // share the data folder.
                if let Some(b) = lock(&shell.backend).take() {
                    b.shutdown();
                }
                lines
            }
            Err(err) => vec![format!("Error: {err}")],
        };
        if let Some(last) = lines.last() {
            note(&app, &format!("backend failed to start: {last}"));
        }
        let log_path = shell.log.get().map(|l| l.path().display().to_string());
        let failed = Status::Failed { lines, log_path };
        *lock(&shell.status) = failed.clone();
        let _ = app.emit("backend-failed", failed);
    });
}

#[tauri::command]
fn retry_backend(app: AppHandle) {
    {
        // Check and claim under one lock so a double click starts one backend.
        let shell = app.state::<Shell>();
        let mut status = lock(&shell.status);
        if !matches!(*status, Status::Failed { .. }) {
            return;
        }
        *status = Status::Starting;
    }
    start_backend(app);
}

/// Lets the loading page catch a failure that happened before it subscribed.
#[tauri::command]
fn backend_status(app: AppHandle) -> Status {
    lock(&app.state::<Shell>().status).clone()
}

/// Allowed in-window: the bundled loading page and the backend's own origin.
fn is_internal(url: &Url, port: u16) -> bool {
    match url.scheme() {
        "tauri" => true,
        "about" => url.as_str() == "about:blank",
        "http" | "https" => match url.host_str() {
            Some("tauri.localhost") => true, // the bundled page on Windows
            Some("127.0.0.1") => port != 0 && url.scheme() == "http" && url.port() == Some(port),
            _ => false,
        },
        _ => false,
    }
}

fn shutdown_backend(app: &AppHandle) {
    let shell = app.state::<Shell>();
    shell.quitting.store(true, Ordering::SeqCst);
    let backend = lock(&shell.backend).take();
    if let Some(b) = backend {
        note(app, "stopping the backend");
        let started = std::time::Instant::now();
        b.shutdown();
        note(app, &format!("backend stopped after {:.1}s", started.elapsed().as_secs_f32()));
    }
}

fn main() {
    let port = Arc::new(AtomicU16::new(0));
    let app = tauri::Builder::default()
        // Must be the first plugin: a second launch just focuses this one.
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window(MAIN) {
                let _ = window.unminimize();
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        // No injected click handler: it would route target=_blank links on
        // the backend's pages through IPC, which those pages don't get.
        // on_new_window below handles them natively instead.
        .plugin(tauri_plugin_opener::Builder::new().open_js_links_on_click(false).build())
        .manage(Shell {
            backend: Mutex::new(None),
            status: Mutex::new(Status::Starting),
            port: port.clone(),
            quitting: AtomicBool::new(false),
            log: OnceLock::new(),
        })
        .invoke_handler(tauri::generate_handler![retry_backend, backend_status])
        .setup(move |app| {
            match app.path().app_log_dir() {
                Ok(dir) => match LogFile::create(&dir.join("backend.log")) {
                    Ok(log) => {
                        let _ = app.state::<Shell>().log.set(log);
                    }
                    Err(e) => eprintln!("[shell] no log file: {e}"),
                },
                Err(e) => eprintln!("[shell] no log dir: {e}"),
            }
            let config = app
                .config()
                .app
                .windows
                .iter()
                .find(|w| w.label == MAIN)
                .cloned()
                .ok_or("no main window in tauri.conf.json")?;
            let nav_port = port.clone();
            let nav_app = app.handle().clone();
            let popup_app = app.handle().clone();
            WebviewWindowBuilder::from_config(app.handle(), &config)?
                .on_navigation(move |url| {
                    if is_internal(url, nav_port.load(Ordering::SeqCst)) {
                        return true;
                    }
                    open_outside(&nav_app, url);
                    false
                })
                // target="_blank" / window.open: never a second app window.
                .on_new_window(move |url, _features| {
                    open_outside(&popup_app, &url);
                    NewWindowResponse::Deny
                })
                .build()?;

            // Installers don't always keep exec bits on resources. A missing
            // interpreter is reported by spawn() on the loading page instead.
            if let Ok(dir) = backend_dir(app.handle()) {
                if let Err(e) = backend::ensure_executable(&backend::python_path(&dir)) {
                    note(app.handle(), &e);
                }
            }
            start_backend(app.handle().clone());
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Knowledge Agent");

    app.run(|app, event| match event {
        // Closing the window quits the app everywhere, macOS included.
        RunEvent::WindowEvent { label, event: WindowEvent::Destroyed, .. } if label == MAIN => {
            app.exit(0);
        }
        RunEvent::Exit => shutdown_backend(app),
        _ => {}
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn navigation_policy() {
        let ok = |s: &str, p| is_internal(&Url::parse(s).unwrap(), p);
        assert!(ok("tauri://localhost/loading.html", 0));
        assert!(ok("http://tauri.localhost/loading.html", 0));
        assert!(ok("http://127.0.0.1:5000/chat", 5000));
        assert!(!ok("http://127.0.0.1:5000/", 0));
        assert!(!ok("http://127.0.0.1:5001/", 5000));
        assert!(!ok("https://127.0.0.1:5000/", 5000));
        assert!(!ok("http://localhost:5000/", 5000));
        assert!(!ok("https://www.notion.so/", 5000));
        assert!(!ok("file:///etc/passwd", 5000));
    }
}

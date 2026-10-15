//! Starts and supervises the bundled Python backend (`launcher.py`).
//!
//! The launcher runs Postgres, the API and the worker. We hold its stdin open
//! as a lifeline (`--lifeline`): closing it asks the launcher to stop its
//! children and Postgres and exit. Its stdout/stderr are drained continuously
//! so a full pipe can never stall it, and the last lines are kept for the
//! error screen.
//!
//! Plain std only (no Tauri), so it is unit-testable without a webview.

use std::collections::VecDeque;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU16, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

/// How long the launcher gets to stop cleanly after its stdin closes.
pub const QUIT_GRACE: Duration = Duration::from_secs(15);
/// Lines of launcher output kept for the error screen.
pub const LOG_LINES: usize = 40;

const READY_PREFIX: &str = "Ready: http://127.0.0.1:";
/// Size cap for the persistent log; lines past it are dropped until the next
/// app start truncates the file.
pub const LOG_FILE_CAP: u64 = 5 * 1024 * 1024;

/// A persistent copy of the launcher's output (and the shell's own notes),
/// so a packaged app, which has no visible stderr, still leaves a log to send.
/// Cheap to clone; shared across retries.
#[derive(Clone)]
pub struct LogFile {
    path: PathBuf,
    inner: Arc<Mutex<LogState>>,
}

struct LogState {
    file: Option<std::fs::File>,
    written: u64,
}

impl LogFile {
    /// Create (or truncate) `path`, making its parent dirs.
    pub fn create(path: &Path) -> Result<LogFile, String> {
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
        }
        let file = std::fs::File::create(path).map_err(|e| format!("{}: {e}", path.display()))?;
        Ok(LogFile { path: path.to_path_buf(), inner: Arc::new(Mutex::new(LogState { file: Some(file), written: 0 })) })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    /// Append one line. Write errors are ignored: logging must never stop
    /// the readers from draining the pipes.
    pub fn line(&self, line: &str) {
        let mut guard = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        let st = &mut *guard;
        let len = line.len() as u64 + 1;
        let Some(file) = st.file.as_mut() else { return };
        if st.written + len > LOG_FILE_CAP {
            let _ = file.write_all(b"[log truncated: size cap reached]\n");
            st.file = None;
            return;
        }
        if file.write_all(line.as_bytes()).and_then(|_| file.write_all(b"\n")).is_ok() {
            st.written += len;
        }
    }
}

pub struct Backend {
    /// Behind a mutex so the health wait, a retry and app exit can all reach
    /// it from different threads.
    child: Mutex<Child>,
    /// The port we asked for.
    port: u16,
    /// The port the launcher announced ("Ready: …"); it may differ if ours
    /// was taken between picking and binding. 0 until announced.
    ready_port: Arc<AtomicU16>,
    logs: Arc<Mutex<VecDeque<String>>>,
}

/// A free loopback port: bind port 0, read what the OS gave us, release it.
pub fn pick_port() -> Result<u16, String> {
    let listener = TcpListener::bind(("127.0.0.1", 0)).map_err(|e| format!("no free port: {e}"))?;
    let port = listener.local_addr().map_err(|e| e.to_string())?.port();
    drop(listener);
    Ok(port)
}

/// The bundled interpreter inside `resources/backend`.
pub fn python_path(backend_dir: &Path) -> PathBuf {
    if cfg!(windows) {
        backend_dir.join("python").join("python.exe")
    } else {
        backend_dir.join("python").join("bin").join("python3")
    }
}

/// Installers don't always keep the exec bit on resources; restore it on the
/// interpreter. No-op on Windows.
pub fn ensure_executable(path: &Path) -> Result<(), String> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let meta = std::fs::metadata(path).map_err(|e| format!("{}: {e}", path.display()))?;
        if meta.permissions().mode() & 0o111 != 0o111 {
            std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o755))
                .map_err(|e| format!("chmod {}: {e}", path.display()))?;
        }
    }
    #[cfg(not(unix))]
    let _ = path;
    Ok(())
}

/// Start `launcher.py` from the bundle at `backend_dir` on a fresh port.
pub fn spawn(backend_dir: &Path) -> Result<Backend, String> {
    spawn_logged(backend_dir, None)
}

/// `spawn`, also appending the launcher's output to `log`.
pub fn spawn_logged(backend_dir: &Path, log: Option<LogFile>) -> Result<Backend, String> {
    let python = python_path(backend_dir);
    if !python.is_file() {
        return Err(format!("bundled Python not found at {}", python.display()));
    }
    let port = pick_port()?;
    let mut cmd = Command::new(&python);
    // -I: isolated (ignores PYTHON* env vars and the user site), which also
    // ignores the two env vars below for the launcher itself, hence -B and -u.
    cmd.args(["-I", "-B", "-u", "launcher.py", "--port"])
        .arg(port.to_string())
        .args(["--no-browser", "--lifeline", "--resources"])
        .arg(backend_dir)
        .current_dir(backend_dir)
        // Inherited by the api/worker children, which don't run isolated.
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .env("PYTHONUNBUFFERED", "1");
    for var in ["PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE"] {
        cmd.env_remove(var);
    }
    spawn_command_logged(cmd, port, log)
}

/// Spawn `cmd` with piped stdio and start the output readers. Split out from
/// `spawn` so tests can supervise a stand-in process.
pub fn spawn_command(cmd: Command, port: u16) -> Result<Backend, String> {
    spawn_command_logged(cmd, port, None)
}

pub fn spawn_command_logged(mut cmd: Command, port: u16, log: Option<LogFile>) -> Result<Backend, String> {
    cmd.stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    let mut child = cmd.spawn().map_err(|e| format!("couldn't start the backend: {e}"))?;
    let logs = Arc::new(Mutex::new(VecDeque::with_capacity(LOG_LINES)));
    let ready_port = Arc::new(AtomicU16::new(0));
    if let Some(out) = child.stdout.take() {
        start_reader(out, logs.clone(), ready_port.clone(), log.clone());
    }
    if let Some(err) = child.stderr.take() {
        start_reader(err, logs.clone(), ready_port.clone(), log.clone());
    }
    let note = format!("[shell] launcher pid {} on port {port}", child.id());
    eprintln!("{note}");
    if let Some(log) = &log {
        log.line(&note);
    }
    Ok(Backend { child: Mutex::new(child), port, ready_port, logs })
}

fn start_reader<R: Read + Send + 'static>(
    stream: R,
    logs: Arc<Mutex<VecDeque<String>>>,
    ready_port: Arc<AtomicU16>,
    log: Option<LogFile>,
) {
    thread::spawn(move || {
        let mut reader = BufReader::new(stream);
        let mut buf = Vec::new();
        loop {
            buf.clear();
            match reader.read_until(b'\n', &mut buf) {
                Ok(0) | Err(_) => break,
                Ok(_) => {}
            }
            let line = String::from_utf8_lossy(&buf).trim_end_matches(['\r', '\n']).to_string();
            if let Some(rest) = line.strip_prefix(READY_PREFIX) {
                if let Ok(p) = rest.trim().trim_end_matches('/').parse::<u16>() {
                    ready_port.store(p, Ordering::SeqCst);
                }
            }
            eprintln!("[backend] {line}");
            if let Some(log) = &log {
                log.line(&line);
            }
            let mut logs = logs.lock().unwrap_or_else(|e| e.into_inner());
            if logs.len() == LOG_LINES {
                logs.pop_front();
            }
            logs.push_back(line);
        }
    });
}

impl Backend {
    /// The port the backend is serving on.
    pub fn port(&self) -> u16 {
        match self.ready_port.load(Ordering::SeqCst) {
            0 => self.port,
            p => p,
        }
    }

    pub fn url(&self) -> String {
        format!("http://127.0.0.1:{}/", self.port())
    }

    /// Poll `/api/health` until it answers 200. Fails early if the launcher
    /// exits, or after `timeout`.
    pub fn wait_healthy(&self, timeout: Duration) -> Result<(), String> {
        let deadline = Instant::now() + timeout;
        loop {
            if let Some(status) = self.exit_status() {
                return Err(format!("the backend exited during startup ({status})"));
            }
            if health_ok(self.port()) {
                return Ok(());
            }
            if Instant::now() >= deadline {
                return Err(format!("the backend didn't answer within {}s", timeout.as_secs()));
            }
            thread::sleep(Duration::from_millis(250));
        }
    }

    /// Exit status once the launcher has exited.
    pub fn exit_status(&self) -> Option<std::process::ExitStatus> {
        let mut child = self.child.lock().unwrap_or_else(|e| e.into_inner());
        child.try_wait().ok().flatten()
    }

    /// Block until the launcher exits on its own, checking every `poll`.
    /// Returns `None` once `stop` is set (a normal quit), so a deliberate
    /// shutdown is never reported as a crash.
    pub fn watch_exit(&self, stop: &AtomicBool, poll: Duration) -> Option<std::process::ExitStatus> {
        loop {
            if stop.load(Ordering::SeqCst) {
                return None;
            }
            if let Some(status) = self.exit_status() {
                // A quit that began while we waited for the child lock.
                return if stop.load(Ordering::SeqCst) { None } else { Some(status) };
            }
            thread::sleep(poll);
        }
    }

    /// Close the lifeline, give the launcher `QUIT_GRACE` to stop Postgres
    /// and its children, then kill it. Idempotent; safe from any thread.
    pub fn shutdown(&self) {
        self.shutdown_with_grace(QUIT_GRACE)
    }

    pub fn shutdown_with_grace(&self, grace: Duration) {
        let mut child = self.child.lock().unwrap_or_else(|e| e.into_inner());
        drop(child.stdin.take()); // EOF on the launcher's stdin = stop
        let deadline = Instant::now() + grace;
        loop {
            match child.try_wait() {
                Ok(Some(_)) => return,
                Ok(None) if Instant::now() < deadline => thread::sleep(Duration::from_millis(50)),
                _ => break,
            }
        }
        eprintln!(
            "[backend] warning: launcher (pid {}) didn't stop within {}s; killing it. \
             Its api/worker/Postgres processes may outlive it.",
            child.id(),
            grace.as_secs()
        );
        let _ = child.kill();
        let _ = child.wait();
    }

    /// The last `LOG_LINES` lines of launcher output (stdout and stderr).
    pub fn last_log_lines(&self) -> Vec<String> {
        self.logs.lock().unwrap_or_else(|e| e.into_inner()).iter().cloned().collect()
    }
}

fn health_ok(port: u16) -> bool {
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let Ok(mut stream) = TcpStream::connect_timeout(&addr, Duration::from_secs(2)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(2)));
    let request = format!("GET /api/health HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n");
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }
    let mut status_line = String::new();
    if BufReader::new(stream).read_line(&mut status_line).is_err() {
        return false;
    }
    // "HTTP/1.1 200 OK"
    status_line.split_whitespace().nth(1) == Some("200")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn python() -> Command {
        Command::new(if cfg!(windows) { "python" } else { "python3" })
    }

    #[test]
    fn log_file_gets_output_and_is_truncated_on_create() {
        let dir = std::env::temp_dir().join(format!("ka-logtest-{}", std::process::id()));
        let path = dir.join("nested").join("backend.log");
        let log = LogFile::create(&path).unwrap();
        log.line("stale");
        let log = LogFile::create(&path).unwrap(); // a new app start truncates
        let mut cmd = python();
        cmd.args(["-u", "-c", "import sys; print('hello'); print('oops', file=sys.stderr); sys.stdin.read()"]);
        let backend = spawn_command_logged(cmd, 1, Some(log.clone())).unwrap();
        let deadline = Instant::now() + Duration::from_secs(5);
        while backend.last_log_lines().len() < 2 && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(20));
        }
        backend.shutdown();
        let text = std::fs::read_to_string(&path).unwrap();
        assert!(!text.contains("stale"), "{text}");
        assert!(text.contains("hello\n") && text.contains("oops\n") && text.contains("launcher pid"), "{text}");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn log_file_stops_at_cap() {
        let dir = std::env::temp_dir().join(format!("ka-logcap-{}", std::process::id()));
        let path = dir.join("backend.log");
        let log = LogFile::create(&path).unwrap();
        let line = "x".repeat(1023);
        for _ in 0..(LOG_FILE_CAP / 1024 + 10) {
            log.line(&line);
        }
        let size = std::fs::metadata(&path).unwrap().len();
        assert!(size <= LOG_FILE_CAP + 64, "{size}");
        assert!(std::fs::read_to_string(&path).unwrap().ends_with("[log truncated: size cap reached]\n"));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn port_is_free_after_pick() {
        let port = pick_port().unwrap();
        assert_ne!(port, 0);
        TcpListener::bind(("127.0.0.1", port)).expect("picked port should be bindable");
    }

    #[test]
    fn shutdown_closes_lifeline_and_returns_within_2s() {
        let mut cmd = python();
        cmd.args(["-c", "import sys; sys.stdin.read()"]);
        let backend = spawn_command(cmd, 1).unwrap();
        thread::sleep(Duration::from_millis(300));
        assert!(backend.exit_status().is_none(), "stand-in should be waiting on stdin");
        let started = Instant::now();
        backend.shutdown();
        assert!(started.elapsed() < Duration::from_secs(2), "took {:?}", started.elapsed());
        assert!(backend.exit_status().is_some(), "process should be gone");
        backend.shutdown(); // idempotent
    }

    #[test]
    fn shutdown_kills_after_grace() {
        let mut cmd = python();
        // Ignores stdin EOF entirely.
        cmd.args(["-c", "import time; time.sleep(60)"]);
        let backend = spawn_command(cmd, 1).unwrap();
        let started = Instant::now();
        backend.shutdown_with_grace(Duration::from_millis(500));
        assert!(started.elapsed() < Duration::from_secs(3));
        assert!(backend.exit_status().is_some());
    }

    #[test]
    fn keeps_last_lines_from_both_streams_and_reads_ready_port() {
        let mut cmd = python();
        cmd.args([
            "-u",
            "-c",
            "import sys, time\n\
             for i in range(60): print(f'out {i}')\n\
             time.sleep(0.5)  # streams have separate readers; keep the order\n\
             print('err line', file=sys.stderr)\n\
             print('Ready: http://127.0.0.1:4321')\n\
             sys.stdin.read()",
        ]);
        let backend = spawn_command(cmd, 1).unwrap();
        let deadline = Instant::now() + Duration::from_secs(5);
        // Two reader threads: wait for both streams, not just the Ready line.
        let has_err = |b: &Backend| b.last_log_lines().iter().any(|l| l == "err line");
        while (backend.port() != 4321 || !has_err(&backend)) && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(20));
        }
        assert_eq!(backend.port(), 4321);
        assert_eq!(backend.url(), "http://127.0.0.1:4321/");
        let lines = backend.last_log_lines();
        assert_eq!(lines.len(), LOG_LINES);
        assert!(lines.iter().any(|l| l == "err line"));
        assert!(!lines.iter().any(|l| l == "out 0"), "oldest lines are dropped");
        backend.shutdown();
    }

    #[test]
    fn wait_healthy_fails_fast_when_process_exits() {
        let mut cmd = python();
        cmd.args(["-c", "raise SystemExit(3)"]);
        let backend = spawn_command(cmd, pick_port().unwrap()).unwrap();
        let started = Instant::now();
        let err = backend.wait_healthy(Duration::from_secs(30)).unwrap_err();
        assert!(err.contains("exited"), "{err}");
        assert!(started.elapsed() < Duration::from_secs(5));
    }

    #[test]
    fn watch_exit_reports_an_unexpected_exit() {
        let mut cmd = python();
        cmd.args(["-c", "import time; time.sleep(0.5); raise SystemExit(4)"]);
        let backend = spawn_command(cmd, 1).unwrap();
        let stop = AtomicBool::new(false);
        let started = Instant::now();
        let status = backend.watch_exit(&stop, Duration::from_millis(50)).expect("exit should be reported");
        assert_eq!(status.code(), Some(4));
        assert!(started.elapsed() < Duration::from_secs(5));
    }

    #[test]
    fn watch_exit_is_silent_on_a_normal_quit() {
        let mut cmd = python();
        cmd.args(["-c", "import sys; sys.stdin.read()"]);
        let backend = Arc::new(spawn_command(cmd, 1).unwrap());
        let stop = Arc::new(AtomicBool::new(false));
        let watcher = {
            let (backend, stop) = (backend.clone(), stop.clone());
            thread::spawn(move || backend.watch_exit(&stop, Duration::from_millis(50)))
        };
        thread::sleep(Duration::from_millis(200));
        // The shell's quit path: flag first, then stop the launcher.
        stop.store(true, Ordering::SeqCst);
        backend.shutdown();
        assert!(watcher.join().unwrap().is_none());
    }

    #[test]
    fn wait_healthy_succeeds_on_200() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let port = listener.local_addr().unwrap().port();
        thread::spawn(move || {
            for stream in listener.incoming().flatten() {
                let mut stream = stream;
                let mut buf = [0u8; 1024];
                let _ = stream.read(&mut buf);
                let _ = stream.write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok");
            }
        });
        let mut cmd = python();
        cmd.args(["-c", "import sys; sys.stdin.read()"]);
        let backend = spawn_command(cmd, port).unwrap();
        backend.wait_healthy(Duration::from_secs(5)).unwrap();
        backend.shutdown();
    }
}

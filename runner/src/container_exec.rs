//! Container check executor: fixed Docker CLI lifecycle behind an injected command boundary.
//!
//! This module builds the accepted isolation plan itself and then drives a bounded
//! `docker` CLI lifecycle. It never supplies arbitrary caller argv, never pulls an
//! image, never forwards the host environment, and never executes candidate code on
//! the host. The production CLI uses `ProcessRunner`; offline tests inject a
//! scripted boundary or an owned fake executable. Evidence remains
//! `synthetic_executor_control` unless a production invocation actually runs Docker,
//! which this source-preparation assignment does not do.

#[path = "isolation_plan.rs"]
pub mod isolation_plan;

use isolation_plan::{build_plan, Plan, IMAGE};
use serde::Serialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    fs::{self, OpenOptions},
    io::{Read, Write},
    path::{Component, Path, PathBuf},
    process::{Command, Stdio},
    sync::{
        atomic::{AtomicBool, AtomicU64, Ordering},
        mpsc::{self, Receiver, TryRecvError},
        Arc,
    },
    thread,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

pub const RECEIPT_SCHEMA_VERSION: &str = "container-check-0.1";
pub const SETUP_TOTAL_SECONDS: u64 = 30;
pub const SETUP_COMMAND_SECONDS: u64 = 5;
pub const EXECUTION_SECONDS: u64 = 60;
pub const POST_ATTACH_SECONDS: u64 = 5;
pub const CLEANUP_TOTAL_SECONDS: u64 = 5;
pub const STREAM_LIMIT_BYTES: usize = 32 * 1024;
pub const OWNER_LABEL: &str = "org.followthrough.owner_token";
pub const RUN_LABEL: &str = "org.followthrough.run_id";

#[cfg(windows)]
pub const DOCKER_ENDPOINT: &str = "npipe:////./pipe/docker_engine";
#[cfg(unix)]
pub const DOCKER_ENDPOINT: &str = "unix:///var/run/docker.sock";

static TOKEN_COUNTER: AtomicU64 = AtomicU64::new(0);

/// Fixed status vocabulary for `container-check-0.1`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Status {
    CheckPassed,
    CheckFailed,
    Refused,
    Timeout,
    OutputLimit,
    Cancelled,
    InfrastructureError,
}

impl Status {
    pub fn exit_code(self) -> u8 {
        match self {
            Self::CheckPassed => 0,
            Self::CheckFailed => 1,
            _ => 2,
        }
    }
}

/// Evidence label. `synthetic_executor_control` is used whenever the command
/// boundary is scripted or fake; only a real production Docker invocation may
/// claim `real_container_check` (later qualification).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
#[allow(dead_code)]
pub enum EvidenceKind {
    SyntheticExecutorControl,
    RealContainerCheck,
}

/// Fixed production deadlines. Tests may shorten these through the library only;
/// the CLI exposes no extension/weakening switch.
#[derive(Debug, Clone)]
pub struct Deadlines {
    pub setup_total: Duration,
    pub setup_command: Duration,
    pub execution: Duration,
    pub post_attach: Duration,
    pub cleanup_total: Duration,
    pub stream_limit: usize,
}

impl Default for Deadlines {
    fn default() -> Self {
        Self {
            setup_total: Duration::from_secs(SETUP_TOTAL_SECONDS),
            setup_command: Duration::from_secs(SETUP_COMMAND_SECONDS),
            execution: Duration::from_secs(EXECUTION_SECONDS),
            post_attach: Duration::from_secs(POST_ATTACH_SECONDS),
            cleanup_total: Duration::from_secs(CLEANUP_TOTAL_SECONDS),
            stream_limit: STREAM_LIMIT_BYTES,
        }
    }
}

/// Validated executor configuration. The library builds the plan itself; no saved
/// argv or caller command/image/mount/env override exists.
#[derive(Debug, Clone)]
pub struct Config {
    pub workspace: PathBuf,
    pub run_id: String,
    pub output_dir: PathBuf,
    pub docker: PathBuf,
    pub execute: bool,
    pub evidence_kind: EvidenceKind,
    pub deadlines: Deadlines,
    pub cancellation: Arc<AtomicBool>,
}

/// Bounded subprocess output. At most `stream_limit` bytes are retained; reading
/// stops after detecting `limit + 1` bytes.
#[derive(Debug, Clone, Default)]
pub struct BoundedStream {
    pub bytes: Vec<u8>,
    pub observed_bytes: u64,
    pub truncated: bool,
    pub read_failed: bool,
    pub settled: bool,
}

#[derive(Debug, Clone)]
pub struct CommandRequest {
    pub program: PathBuf,
    pub args: Vec<String>,
    pub env: Vec<(String, String)>,
    pub timeout: Duration,
    pub stream_limit: usize,
    pub ignore_cancellation: bool,
    pub cancellation: Arc<AtomicBool>,
}

#[derive(Debug, Clone)]
pub struct CommandOutcome {
    pub pid: Option<u32>,
    pub exit_code: Option<i32>,
    pub stdout: BoundedStream,
    pub stderr: BoundedStream,
    pub timed_out: bool,
    pub output_limit: bool,
    pub cancelled: bool,
    pub spawn_failed: bool,
    pub direct_child_reaped: bool,
    pub reader_settled: bool,
    pub elapsed_ms: u64,
    pub issue: Option<String>,
}

impl CommandOutcome {
    pub fn exit_success(&self) -> bool {
        self.exit_code == Some(0) && !self.spawn_failed
    }

    pub fn stdout_lossy(&self) -> String {
        String::from_utf8_lossy(&self.stdout.bytes).into_owned()
    }

    pub fn stderr_lossy(&self) -> String {
        String::from_utf8_lossy(&self.stderr.bytes).into_owned()
    }
}

pub trait CommandRunner {
    fn run(&self, request: &CommandRequest) -> CommandOutcome;
}

/// Production wrapper: real subprocess with bounded concurrent capture, a hard
/// timeout, cooperative cancellation and direct-child kill/reap. Reader threads
/// are detached and never joined unboundedly; `reader_settled` reports whether
/// their bounded report arrived.
pub struct ProcessRunner;

impl CommandRunner for ProcessRunner {
    fn run(&self, request: &CommandRequest) -> CommandOutcome {
        let started = Instant::now();
        let timeout = request.timeout.max(Duration::from_millis(1));
        // One absolute allowance covers waiting, termination/reap and reader
        // settlement. Reserves are taken from the allowance, never added after it.
        let terminate_reserve = (timeout / 5)
            .min(Duration::from_millis(400))
            .max(Duration::from_millis(10));
        let reader_reserve = (timeout / 10)
            .min(Duration::from_millis(150))
            .max(Duration::from_millis(5));
        let wait_budget = timeout
            .saturating_sub(terminate_reserve)
            .saturating_sub(reader_reserve);
        let wait_deadline = started + wait_budget;
        let absolute_deadline = started + timeout;
        let reap_deadline = absolute_deadline
            .checked_sub(reader_reserve)
            .unwrap_or(absolute_deadline);
        let mut command = Command::new(&request.program);
        command
            .args(&request.args)
            .env_clear()
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        for (key, value) in &request.env {
            command.env(key, value);
        }
        let mut child = match command.spawn() {
            Ok(child) => child,
            Err(error) => {
                return CommandOutcome {
                    pid: None,
                    exit_code: None,
                    stdout: BoundedStream::default(),
                    stderr: BoundedStream::default(),
                    timed_out: false,
                    output_limit: false,
                    cancelled: false,
                    spawn_failed: true,
                    direct_child_reaped: false,
                    reader_settled: false,
                    elapsed_ms: started.elapsed().as_millis() as u64,
                    issue: Some(format!("spawn_failed:{}", error.kind())),
                }
            }
        };
        let pid = child.id();
        let mut stdout = Capture::start(
            child.stdout.take().expect("stdout piped"),
            request.stream_limit,
        )
        .ok();
        let mut stderr = Capture::start(
            child.stderr.take().expect("stderr piped"),
            request.stream_limit,
        )
        .ok();
        let mut status = None;
        let mut cancelled = false;
        let mut output_limit = false;
        let mut wait_failed = false;
        let mut hit_wait_deadline = false;
        loop {
            if !request.ignore_cancellation && request.cancellation.load(Ordering::SeqCst) {
                cancelled = true;
            }
            if status.is_none() {
                match child.try_wait() {
                    Ok(observed) => status = observed,
                    Err(_) => wait_failed = true,
                }
            }
            poll(&mut stdout);
            poll(&mut stderr);
            if stream_truncated(&stdout) || stream_truncated(&stderr) {
                output_limit = true;
            }
            if cancelled || output_limit || wait_failed {
                break;
            }
            if status.is_some() && capture_ready(&stdout) && capture_ready(&stderr) {
                break;
            }
            let now = Instant::now();
            if now >= wait_deadline {
                hit_wait_deadline = true;
                break;
            }
            thread::sleep((wait_deadline - now).min(Duration::from_millis(5)));
        }
        if status.is_none() {
            let _ = child.kill();
            while Instant::now() < reap_deadline {
                match child.try_wait() {
                    Ok(Some(observed)) => {
                        status = Some(observed);
                        break;
                    }
                    Ok(None) => thread::sleep(Duration::from_millis(5)),
                    Err(_) => break,
                }
            }
        }
        let timed_out = hit_wait_deadline;
        let stdout = stdout
            .take()
            .map(|capture| capture.finish_until(absolute_deadline))
            .unwrap_or_else(|| BoundedStream {
                read_failed: true,
                ..BoundedStream::default()
            });
        let stderr = stderr
            .take()
            .map(|capture| capture.finish_until(absolute_deadline))
            .unwrap_or_else(|| BoundedStream {
                read_failed: true,
                ..BoundedStream::default()
            });
        output_limit = output_limit || stdout.truncated || stderr.truncated;
        let direct_child_reaped = status.is_some();
        let reader_settled = stdout.settled && stderr.settled;
        let issue = if wait_failed {
            Some("wait_failed".to_string())
        } else if output_limit {
            Some("output_limit_exceeded".to_string())
        } else if cancelled {
            Some("cancelled".to_string())
        } else if stdout.read_failed || stderr.read_failed {
            Some("capture_read_failed".to_string())
        } else if !reader_settled {
            Some("reader_unsettled".to_string())
        } else if timed_out {
            Some("timed_out".to_string())
        } else {
            None
        };
        CommandOutcome {
            pid: Some(pid),
            exit_code: status.and_then(|status| status.code()),
            stdout,
            stderr,
            timed_out,
            output_limit,
            cancelled,
            spawn_failed: false,
            direct_child_reaped,
            reader_settled,
            elapsed_ms: started.elapsed().as_millis() as u64,
            issue,
        }
    }
}

fn capture_ready(capture: &Option<Capture>) -> bool {
    capture.as_ref().is_some_and(|capture| capture.report.is_some())
}

fn stream_truncated(capture: &Option<Capture>) -> bool {
    capture
        .as_ref()
        .and_then(|capture| capture.report.as_ref())
        .is_some_and(|report| report.truncated)
}

struct Capture {
    receiver: Receiver<BoundedStream>,
    report: Option<BoundedStream>,
}

impl Capture {
    fn start(mut reader: impl Read + Send + 'static, limit: usize) -> std::io::Result<Self> {
        let (sender, receiver) = mpsc::channel();
        thread::Builder::new().spawn(move || {
            let mut bytes = Vec::new();
            let mut observed = 0u64;
            let mut truncated = false;
            let mut read_failed = false;
            let mut buffer = [0u8; 8192];
            loop {
                if observed as usize > limit {
                    break;
                }
                let want = ((limit + 1) as u64 - observed).min(buffer.len() as u64) as usize;
                match reader.read(&mut buffer[..want]) {
                    Ok(0) => break,
                    Ok(count) => {
                        observed += count as u64;
                        if bytes.len() < limit {
                            let take = (limit - bytes.len()).min(count);
                            bytes.extend_from_slice(&buffer[..take]);
                        }
                        if observed as usize > limit {
                            truncated = true;
                            break;
                        }
                    }
                    Err(error) if error.kind() == std::io::ErrorKind::Interrupted => continue,
                    Err(_) => {
                        read_failed = true;
                        break;
                    }
                }
            }
            let _ = sender.send(BoundedStream {
                bytes,
                observed_bytes: observed,
                truncated,
                read_failed,
                settled: true,
            });
        })?;
        Ok(Self {
            receiver,
            report: None,
        })
    }

    fn poll(&mut self) {
        if self.report.is_none() {
            self.report = match self.receiver.try_recv() {
                Ok(report) => Some(report),
                Err(TryRecvError::Empty) => None,
                Err(TryRecvError::Disconnected) => Some(BoundedStream {
                    read_failed: true,
                    ..BoundedStream::default()
                }),
            };
        }
    }

    fn finish_until(mut self, deadline: Instant) -> BoundedStream {
        loop {
            self.poll();
            if self.report.is_some() || Instant::now() >= deadline {
                break;
            }
            thread::sleep((deadline - Instant::now()).min(Duration::from_millis(5)));
        }
        self.report.unwrap_or_else(|| BoundedStream {
            read_failed: true,
            ..BoundedStream::default()
        })
    }
}

fn poll(capture: &mut Option<Capture>) {
    if let Some(capture) = capture.as_mut() {
        capture.poll();
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct CommandObservation {
    pub name: String,
    pub argv: Vec<String>,
    pub pid: Option<u32>,
    pub exit_code: Option<i32>,
    pub stdout_bytes: u64,
    pub stderr_bytes: u64,
    /// Private receipt diagnostics only; never projected to agent-visible check output.
    pub stderr_excerpt: String,
    pub timed_out: bool,
    pub output_limit: bool,
    pub cancelled: bool,
    pub spawn_failed: bool,
    pub direct_child_reaped: bool,
    pub reader_settled: bool,
    pub elapsed_ms: u64,
    pub issue: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct Cleanup {
    pub state: String,
    pub method: String,
    pub removal_verified: bool,
    pub detail: Option<String>,
}

impl Cleanup {
    fn pending() -> Self {
        Self {
            state: "not_started".to_string(),
            method: "none".to_string(),
            removal_verified: false,
            detail: None,
        }
    }

    fn unknown(detail: impl Into<String>) -> Self {
        Self {
            state: "unknown".to_string(),
            method: "none".to_string(),
            removal_verified: false,
            detail: Some(detail.into()),
        }
    }

    fn settled(method: impl Into<String>, verified: bool, detail: Option<String>) -> Self {
        Self {
            state: "settled".to_string(),
            method: method.into(),
            removal_verified: verified,
            detail,
        }
    }

    fn not_created(detail: impl Into<String>) -> Self {
        Self {
            state: "settled".to_string(),
            method: "not_created".to_string(),
            removal_verified: false,
            detail: Some(detail.into()),
        }
    }
}

#[derive(Debug)]
pub struct Execution {
    pub status: Status,
    pub receipt_path: PathBuf,
    #[allow(dead_code)]
    pub diagnostic: Option<String>,
}

impl Execution {
    pub fn exit_code(&self) -> u8 {
        self.status.exit_code()
    }
}

struct Prepared {
    plan: Plan,
    workspace: PathBuf,
    output: PathBuf,
    docker: PathBuf,
    endpoint: &'static str,
    token: String,
    env: Vec<(String, String)>,
    deadlines: Deadlines,
    evidence_kind: EvidenceKind,
    cancellation: Arc<AtomicBool>,
}

struct Fail {
    status: Status,
    code: &'static str,
    detail: Option<String>,
}

impl Fail {
    fn new(status: Status, code: &'static str) -> Self {
        Self {
            status,
            code,
            detail: None,
        }
    }
}

/// Public synchronous entry point. Validation failures before the output root is
/// created return `Err` with a fixed bounded diagnostic and no fabricated receipt.
/// After root creation every lifecycle outcome is recorded (subject to receipt write
/// success).
pub fn execute(config: &Config, runner: &dyn CommandRunner) -> Result<Execution, String> {
    if !config.execute {
        return Err("execute_required: --execute is required; nothing was spawned".into());
    }
    let prepared = prepare(config)?;
    run_prepared(prepared, runner)
}

fn prepare(config: &Config) -> Result<Prepared, String> {
    let workspace = checked_existing_dir(&config.workspace, "invalid_workspace")?;
    let plan = build_plan(&workspace, &config.run_id)
        .map_err(|error| format!("plan_refused: {}", error.code()))?;
    let output = checked_new_output(&workspace, &config.output_dir)?;
    let docker = checked_existing_file(&config.docker, "invalid_docker")?;
    if docker.starts_with(&workspace) || output.starts_with(&workspace) || workspace.starts_with(&output) {
        return Err("invalid_output: output and docker paths must be outside the workspace".into());
    }
    fs::create_dir(&output).map_err(|error| {
        if error.kind() == std::io::ErrorKind::AlreadyExists {
            "output_exists: refusing an existing output directory".to_string()
        } else {
            format!("output_create_failed:{}", error.kind())
        }
    })?;
    let config_dir = output.join("docker-config");
    fs::create_dir(&config_dir).map_err(|error| {
        format!("output_create_failed:docker-config:{}", error.kind())
    })?;
    let env = sanitized_environment(&config_dir);
    Ok(Prepared {
        plan,
        workspace,
        output,
        docker,
        endpoint: DOCKER_ENDPOINT,
        token: ownership_token(),
        env,
        deadlines: config.deadlines.clone(),
        evidence_kind: config.evidence_kind,
        cancellation: config.cancellation.clone(),
    })
}

fn checked_existing_dir(path: &Path, code: &'static str) -> Result<PathBuf, String> {
    let canonical = checked_existing(path, code)?;
    if !canonical.is_dir() {
        return Err(format!("{code}: expected a directory"));
    }
    Ok(canonical)
}

fn checked_existing_file(path: &Path, code: &'static str) -> Result<PathBuf, String> {
    let canonical = checked_existing(path, code)?;
    if !canonical.is_file() {
        return Err(format!("{code}: expected a regular file"));
    }
    Ok(canonical)
}

fn checked_existing(path: &Path, code: &'static str) -> Result<PathBuf, String> {
    if path.to_str().is_none() {
        return Err(format!("{code}: path must be Unicode"));
    }
    if !path.is_absolute() {
        return Err(format!("{code}: path must be absolute"));
    }
    if path.components().any(|part| part == Component::ParentDir) {
        return Err(format!("{code}: parent components are refused"));
    }
    #[cfg(windows)]
    if path.components().any(
        |part| matches!(part, Component::Normal(name) if name.to_string_lossy().contains(':')),
    ) {
        return Err(format!("{code}: alternate data stream components are refused"));
    }
    let normalized: PathBuf = path
        .components()
        .filter(|part| *part != Component::CurDir)
        .collect();
    let ancestors: Vec<&Path> = normalized.ancestors().collect();
    for ancestor in ancestors.into_iter().rev() {
        if ancestor.file_name().is_some() {
            let metadata = fs::symlink_metadata(ancestor)
                .map_err(|error| format!("{code}: path is not accessible ({})", error.kind()))?;
            if is_link(&metadata) {
                return Err(format!("{code}: symlink or reparse component is refused"));
            }
        }
    }
    let canonical = fs::canonicalize(&normalized)
        .map_err(|error| format!("{code}: canonicalization failed ({})", error.kind()))?;
    if canonical.to_str().is_none() {
        return Err(format!("{code}: canonical path must be Unicode"));
    }
    Ok(canonical)
}

fn checked_new_output(workspace: &Path, output: &Path) -> Result<PathBuf, String> {
    if output.to_str().is_none() {
        return Err("invalid_output: path must be Unicode".into());
    }
    if !output.is_absolute() {
        return Err("invalid_output: path must be absolute".into());
    }
    if output
        .to_str()
        .is_some_and(|text| text.ends_with(['\\', '/']))
    {
        return Err("invalid_output: must name a new directory, not a trailing separator".into());
    }
    let name = output
        .file_name()
        .ok_or("invalid_output: missing directory name")?;
    if output.components().last() != Some(Component::Normal(name)) {
        return Err("invalid_output: must name a new directory".into());
    }
    let parent = output
        .parent()
        .ok_or("invalid_output: missing parent directory")?;
    let parent = checked_existing_dir(parent, "invalid_output")?;
    let candidate = parent.join(name);
    match fs::symlink_metadata(&candidate) {
        Ok(_) => return Err("output_exists: refusing an existing output".into()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => return Err(format!("invalid_output: cannot inspect output ({})", error.kind())),
    }
    if candidate.starts_with(workspace) || workspace.starts_with(&candidate) {
        return Err("invalid_output: output must not equal, descend from, or contain the workspace".into());
    }
    Ok(candidate)
}

fn sanitized_environment(config_dir: &Path) -> Vec<(String, String)> {
    let mut env = Vec::new();
    #[cfg(windows)]
    let allowed = ["PATH", "SystemRoot", "WINDIR", "TEMP", "TMP"];
    #[cfg(not(windows))]
    let allowed = ["PATH", "TMPDIR"];
    for key in allowed {
        if let Some(value) = std::env::var_os(key) {
            if let Some(value) = value.to_str() {
                env.push((key.to_string(), value.to_string()));
            }
        }
    }
    env.push((
        "DOCKER_CONFIG".to_string(),
        config_dir.to_string_lossy().into_owned(),
    ));
    env
}

fn ownership_token() -> String {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    let counter = TOKEN_COUNTER.fetch_add(1, Ordering::Relaxed);
    let material = format!("followthrough-executor:{now}:{}:{counter}", std::process::id());
    format!("{:x}", Sha256::digest(material.as_bytes()))
}

fn is_link(metadata: &fs::Metadata) -> bool {
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x400;
        metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
    }
    #[cfg(not(windows))]
    {
        metadata.file_type().is_symlink()
    }
}

fn run_prepared(prepared: Prepared, runner: &dyn CommandRunner) -> Result<Execution, String> {
    Lifecycle::new(prepared, runner).run()
}

struct Lifecycle<'a> {
    prep: Prepared,
    runner: &'a dyn CommandRunner,
    commands: Vec<CommandObservation>,
    setup_start: Instant,
    container_id: Option<String>,
    container_created: bool,
    create_ambiguous: bool,
    create_attempted: bool,
    attach: Option<CommandOutcome>,
    post_error: Option<&'static str>,
    public_check_exit: Option<i32>,
    image_id: Option<String>,
    image_arch: Option<String>,
    daemon_arch: Option<String>,
    cleanup: Cleanup,
    started: Instant,
}

impl<'a> Lifecycle<'a> {
    fn new(prep: Prepared, runner: &'a dyn CommandRunner) -> Self {
        Self {
            prep,
            runner,
            commands: Vec::new(),
            setup_start: Instant::now(),
            container_id: None,
            container_created: false,
            create_ambiguous: false,
            create_attempted: false,
            attach: None,
            post_error: None,
            public_check_exit: None,
            image_id: None,
            image_arch: None,
            daemon_arch: None,
            cleanup: Cleanup::pending(),
            started: Instant::now(),
        }
    }

    fn request(&self, args: Vec<String>, timeout: Duration, ignore_cancel: bool) -> CommandRequest {
        let mut argv = vec!["--host".to_string(), self.prep.endpoint.to_string()];
        argv.extend(args);
        CommandRequest {
            program: self.prep.docker.clone(),
            args: argv,
            env: self.prep.env.clone(),
            timeout,
            stream_limit: self.prep.deadlines.stream_limit,
            ignore_cancellation: ignore_cancel,
            cancellation: self.prep.cancellation.clone(),
        }
    }

    fn record(&mut self, name: &str, request: &CommandRequest, outcome: &CommandOutcome) {
        let retained = &outcome.stderr.bytes[..outcome.stderr.bytes.len().min(4096)];
        let mut stderr_excerpt = String::from_utf8_lossy(retained).into_owned();
        // Lossy UTF-8 replacement may expand bytes; preserve the serialized field bound.
        while stderr_excerpt.len() > 4096 {
            stderr_excerpt.pop();
        }
        self.commands.push(CommandObservation {
            name: name.to_string(),
            argv: request.args.clone(),
            pid: outcome.pid,
            exit_code: outcome.exit_code,
            stdout_bytes: outcome.stdout.observed_bytes,
            stderr_bytes: outcome.stderr.observed_bytes,
            stderr_excerpt,
            timed_out: outcome.timed_out,
            output_limit: outcome.output_limit,
            cancelled: outcome.cancelled,
            spawn_failed: outcome.spawn_failed,
            direct_child_reaped: outcome.direct_child_reaped,
            reader_settled: outcome.reader_settled,
            elapsed_ms: outcome.elapsed_ms,
            issue: outcome.issue.clone(),
        });
    }

    fn cancellation(&self) -> bool {
        self.prep.cancellation.load(Ordering::SeqCst)
    }

    fn check_cancel(&self) -> Result<(), Fail> {
        if self.cancellation() {
            Err(Fail::new(Status::Cancelled, "cancelled"))
        } else {
            Ok(())
        }
    }

    fn setup_command(&mut self, name: &str, args: Vec<String>) -> Result<CommandOutcome, Fail> {
        self.check_cancel()?;
        let remaining = self
            .prep
            .deadlines
            .setup_total
            .saturating_sub(self.setup_start.elapsed());
        if remaining.is_zero() {
            return Err(Fail::new(Status::InfrastructureError, "setup_total_exceeded"));
        }
        let timeout = remaining.min(self.prep.deadlines.setup_command);
        let request = self.request(args, timeout, false);
        let outcome = self.runner.run(&request);
        self.record(name, &request, &outcome);
        Ok(outcome)
    }

    fn require_setup(outcome: &CommandOutcome, code: &'static str) -> Result<(), Fail> {
        if outcome.timed_out {
            Err(Fail::new(Status::InfrastructureError, "setup_timeout"))
        } else if outcome.output_limit {
            Err(Fail::new(Status::InfrastructureError, "setup_output_limit"))
        } else if outcome.cancelled {
            Err(Fail::new(Status::Cancelled, "cancelled"))
        } else if outcome.spawn_failed {
            Err(Fail::new(Status::InfrastructureError, "setup_spawn_failed"))
        } else if !outcome.exit_success() {
            Err(Fail::new(Status::InfrastructureError, code))
        } else if !outcome.direct_child_reaped {
            Err(Fail::new(Status::InfrastructureError, "setup_child_not_reaped"))
        } else if outcome.issue.is_some()
            || !outcome.reader_settled
            || !outcome.stdout.settled
            || !outcome.stderr.settled
            || outcome.stdout.read_failed
            || outcome.stderr.read_failed
        {
            Err(Fail::new(Status::InfrastructureError, "setup_capture_unsettled"))
        } else {
            Ok(())
        }
    }

    fn run(mut self) -> Result<Execution, String> {
        let result = self.run_inner();
        let (mut status, mut diagnostic) = match result {
            Ok(status) => (status, None),
            Err(fail) => (fail.status, Some(format_fail(&fail))),
        };
        self.cleanup_all();
        if self.cleanup.state == "unknown" && matches!(status, Status::CheckPassed | Status::CheckFailed) {
            status = Status::InfrastructureError;
            diagnostic = Some("cleanup_unknown".to_string());
        }
        let stdout_path = self.write_attach_stream("stdout.txt", false);
        let stderr_path = self.write_attach_stream("stderr.txt", true);
        if self.attach.is_some() && (stdout_path.is_none() || stderr_path.is_none()) {
            status = Status::InfrastructureError;
            diagnostic = Some("stream_write_failed".to_string());
        }
        let receipt_path = self.prep.output.join("receipt.json");
        let receipt = self.receipt(status, diagnostic.as_deref(), stdout_path.as_deref(), stderr_path.as_deref());
        let mut payload = serde_json::to_vec_pretty(&receipt)
            .map_err(|error| format!("receipt_serialization_failed:{error}"))?;
        payload.push(b'\n');
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&receipt_path)
            .map_err(|error| format!("receipt_write_failed:{}", error.kind()))?;
        file.write_all(&payload)
            .map_err(|error| format!("receipt_write_failed:{}", error.kind()))?;
        Ok(Execution {
            status,
            receipt_path,
            diagnostic,
        })
    }

    fn run_inner(&mut self) -> Result<Status, Fail> {
        self.check_cancel()?;
        let info = self.setup_command("info", vec!["info".into(), "--format".into(), "{{json .}}".into()])?;
        Self::require_setup(&info, "docker_info_failed")?;
        let info_json = parse_json(&info).ok_or_else(|| {
            Fail::new(Status::InfrastructureError, "docker_info_malformed")
        })?;
        let os = info_json.get("OSType").and_then(Value::as_str).unwrap_or("");
        if os != "linux" {
            return Err(Fail::new(Status::InfrastructureError, "daemon_not_linux"));
        }
        self.daemon_arch = normalize_arch(info_json.get("Architecture").and_then(Value::as_str));

        let image = self.setup_command(
            "image_inspect",
            vec![
                "image".into(),
                "inspect".into(),
                "--format".into(),
                "{{json .}}".into(),
                IMAGE.into(),
            ],
        )?;
        Self::require_setup(&image, "image_inspect_failed")?;
        let image_json = parse_json(&image).ok_or_else(|| {
            Fail::new(Status::InfrastructureError, "image_inspect_malformed")
        })?;
        let image_obj = image_json
            .as_object()
            .ok_or_else(|| Fail::new(Status::InfrastructureError, "image_shape_invalid"))?;
        let image_os = image_obj.get("Os").and_then(Value::as_str).unwrap_or("");
        if image_os != "linux" {
            return Err(Fail::new(Status::InfrastructureError, "image_not_linux"));
        }
        let image_id = image_obj
            .get("Id")
            .and_then(Value::as_str)
            .filter(|id| is_sha256_id(id))
            .ok_or_else(|| Fail::new(Status::InfrastructureError, "image_id_invalid"))?;
        self.image_id = Some(image_id.to_string());
        let digests = image_obj
            .get("RepoDigests")
            .and_then(Value::as_array)
            .ok_or_else(|| Fail::new(Status::InfrastructureError, "image_digest_missing"))?;
        if !digests.iter().any(|digest| digest.as_str() == Some(IMAGE)) {
            return Err(Fail::new(Status::InfrastructureError, "image_digest_mismatch"));
        }
        self.image_arch = normalize_arch(image_obj.get("Architecture").and_then(Value::as_str));
        if self.daemon_arch.is_none() || self.image_arch.is_none() || self.daemon_arch != self.image_arch {
            return Err(Fail::new(Status::InfrastructureError, "architecture_incompatible"));
        }

        let create = self.setup_command("create", create_args(&self.prep.plan, &self.prep.token))?;
        self.create_attempted = true;
        let clean = command_observation_clean(&create);
        let id = (clean && create.exit_success())
            .then(|| parse_container_id(&create))
            .flatten();
        let Some(id) = id else {
            let collision = clean
                && create.exit_code.is_some_and(|code| code != 0)
                && create.stdout.bytes.is_empty()
                && docker_name_conflict(&create, &self.prep.plan.container_name);
            self.create_ambiguous = !collision;
            return Err(Fail::new(
                Status::InfrastructureError,
                if collision {
                    "create_collision"
                } else {
                    "create_ambiguous"
                },
            ));
        };
        self.container_id = Some(id.clone());
        self.container_created = true;

        let pre = self.setup_command(
            "inspect_created",
            vec![
                "container".into(),
                "inspect".into(),
                "--format".into(),
                "{{json .}}".into(),
                id.clone(),
            ],
        )?;
        Self::require_setup(&pre, "inspect_created_failed")?;
        let pre_json = parse_json(&pre)
            .ok_or_else(|| Fail::new(Status::InfrastructureError, "inspect_created_malformed"))?;
        if let Err(code) = self.validate_container(&pre_json, &id, true) {
            return Err(Fail::new(Status::Refused, code));
        }

        let rehashed = build_plan(&self.prep.workspace, &self.prep.plan.run_id)
            .map_err(|_| Fail::new(Status::Refused, "workspace_rehash_failed"))?;
        if !same_inventory(&self.prep.plan, &rehashed) {
            return Err(Fail::new(Status::Refused, "workspace_changed_before_start"));
        }
        if self.setup_start.elapsed() >= self.prep.deadlines.setup_total {
            return Err(Fail::new(
                Status::InfrastructureError,
                "setup_total_exceeded_after_rehash",
            ));
        }
        self.check_cancel()?;

        let start_request = self.request(
            vec![
                "container".into(),
                "start".into(),
                "--attach".into(),
                id.clone(),
            ],
            self.prep.deadlines.execution,
            false,
        );
        let start = self.runner.run(&start_request);
        self.record("start_attach", &start_request, &start);
        self.attach = Some(start);

        let post_started = Instant::now();
        let post_remaining = self
            .prep
            .deadlines
            .post_attach
            .saturating_sub(post_started.elapsed());
        if post_remaining.is_zero() {
            self.post_error = Some("post_attach_deadline_exceeded");
        } else {
            let request = self.request(
                vec![
                    "container".into(),
                    "inspect".into(),
                    "--format".into(),
                    "{{json .}}".into(),
                    id.clone(),
                ],
                post_remaining,
                false,
            );
            let post = self.runner.run(&request);
            self.record("inspect_post_attach", &request, &post);
            match inspect_settled(&post) {
                Ok(()) => match parse_json(&post) {
                    Some(value) => match self.validate_container(&value, &id, false) {
                        Ok(()) => {
                            if let Some(exit) = post_exit(&value) {
                                self.public_check_exit = Some(exit);
                            } else {
                                self.post_error = Some("post_state_exit_missing");
                            }
                        }
                        Err(code) => self.post_error = Some(code),
                    },
                    None => self.post_error = Some("post_state_malformed"),
                },
                Err(code) => self.post_error = Some(code),
            }
        }
        self.classify()
    }

    fn classify(&self) -> Result<Status, Fail> {
        let attach = self.attach.as_ref().expect("attach outcome recorded");
        if attach.cancelled || self.cancellation() {
            return Ok(Status::Cancelled);
        }
        if attach.timed_out {
            return Ok(Status::Timeout);
        }
        if attach.output_limit {
            return Ok(Status::OutputLimit);
        }
        if let Some(code) = self.post_error {
            return Err(Fail::new(Status::InfrastructureError, code));
        }
        let exit = self
            .public_check_exit
            .ok_or_else(|| Fail::new(Status::InfrastructureError, "post_exit_missing"))?;
        if attach.spawn_failed
            || !attach.direct_child_reaped
            || attach.issue.is_some()
            || !attach.reader_settled
            || !attach.stdout.settled
            || !attach.stderr.settled
            || attach.stdout.read_failed
            || attach.stderr.read_failed
        {
            return Err(Fail::new(Status::InfrastructureError, "attach_unsettled"));
        }
        let attach_code = attach.exit_code;
        if attach_code != Some(0) && attach_code != Some(exit) {
            return Err(Fail::new(Status::InfrastructureError, "attach_exit_inconsistent"));
        }
        if exit == 0 {
            Ok(Status::CheckPassed)
        } else {
            Ok(Status::CheckFailed)
        }
    }

    fn validate_container(&self, value: &Value, id: &str, pre: bool) -> Result<(), &'static str> {
        if value.get("Id").and_then(Value::as_str) != Some(id) {
            return Err("inspect_id_mismatch");
        }
        let name = value
            .get("Name")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim_start_matches('/');
        if name != self.prep.plan.container_name {
            return Err("inspect_name_mismatch");
        }
        let labels = value
            .pointer("/Config/Labels")
            .and_then(Value::as_object)
            .ok_or("inspect_labels_missing")?;
        if labels.get(RUN_LABEL).and_then(Value::as_str) != Some(self.prep.plan.run_id.as_str()) {
            return Err("inspect_run_label_mismatch");
        }
        if labels.get(OWNER_LABEL).and_then(Value::as_str) != Some(self.prep.token.as_str()) {
            return Err("inspect_owner_label_mismatch");
        }
        if value.pointer("/Config/Image").and_then(Value::as_str) != Some(IMAGE) {
            return Err("inspect_image_reference_mismatch");
        }
        let expected_image = self.image_id.as_deref().ok_or("inspect_image_missing")?;
        if value.get("Image").and_then(Value::as_str) != Some(expected_image) {
            return Err("inspect_image_id_mismatch");
        }
        if value.pointer("/Config/User").and_then(Value::as_str) != Some("65534:65534") {
            return Err("inspect_user_mismatch");
        }
        if exact_string_array(value.pointer("/Config/Entrypoint"))
            != Some(vec!["python".to_string()])
        {
            return Err("inspect_entrypoint_mismatch");
        }
        if exact_string_array(value.pointer("/Config/Cmd"))
            != Some(vec![
                "-I".to_string(),
                "-B".to_string(),
                "/workspace/public_checks.py".to_string(),
            ])
        {
            return Err("inspect_cmd_mismatch");
        }
        if value.pointer("/Config/WorkingDir").and_then(Value::as_str) != Some("/workspace") {
            return Err("inspect_workdir_mismatch");
        }
        let host = value
            .get("HostConfig")
            .ok_or("inspect_host_config_missing")?;
        if host.get("ReadonlyRootfs").and_then(Value::as_bool) != Some(true) {
            return Err("inspect_readonly_root_mismatch");
        }
        if host.get("NetworkMode").and_then(Value::as_str) != Some("none") {
            return Err("inspect_network_mismatch");
        }
        let cap_drop = exact_string_array(host.get("CapDrop"))
            .ok_or("inspect_capdrop_missing")?;
        if cap_drop != vec!["ALL".to_string()] {
            return Err("inspect_capdrop_mismatch");
        }
        let security = exact_string_array(host.get("SecurityOpt"))
            .ok_or("inspect_security_missing")?;
        if security.is_empty()
            || security.iter().any(|opt| {
                opt != "no-new-privileges" && opt != "no-new-privileges:true"
            })
        {
            return Err("inspect_no_new_privileges_mismatch");
        }
        if host.get("PidsLimit").and_then(Value::as_i64) != Some(64) {
            return Err("inspect_pids_mismatch");
        }
        if host.get("Memory").and_then(Value::as_i64) != Some(268_435_456) {
            return Err("inspect_memory_mismatch");
        }
        if host.get("NanoCpus").and_then(Value::as_i64) != Some(1_000_000_000) {
            return Err("inspect_cpus_mismatch");
        }
        let tmpfs = host
            .get("Tmpfs")
            .and_then(Value::as_object)
            .ok_or("inspect_tmpfs_missing")?;
        if tmpfs.len() != 1
            || tmpfs.get("/tmp").and_then(Value::as_str)
                != Some("rw,noexec,nosuid,size=16m")
        {
            return Err("inspect_tmpfs_mismatch");
        }
        if host.pointer("/LogConfig/Type").and_then(Value::as_str) != Some("none") {
            return Err("inspect_logging_mismatch");
        }
        if host.get("AutoRemove").and_then(Value::as_bool) != Some(false) {
            return Err("inspect_autoremove_mismatch");
        }
        if host.get("Privileged").and_then(Value::as_bool) != Some(false) {
            return Err("inspect_privileged_mismatch");
        }
        for key in ["CapAdd", "Devices", "ExtraHosts", "Binds"] {
            match host.get(key) {
                None | Some(Value::Null) => {}
                Some(Value::Array(items)) if items.is_empty() => {}
                _ => return Err("inspect_extra_privilege_mismatch"),
            }
        }
        for key in ["PidMode", "UTSMode", "UsernsMode"] {
            if host
                .get(key)
                .and_then(Value::as_str)
                .is_some_and(|text| !text.is_empty())
            {
                return Err("inspect_host_namespace_mismatch");
            }
        }
        if let Some(mode) = host.get("IpcMode").and_then(Value::as_str) {
            if mode != "private" {
                return Err("inspect_host_namespace_mismatch");
            }
        }
        let mounts = value
            .get("Mounts")
            .and_then(Value::as_array)
            .ok_or("inspect_mounts_missing")?;
        if mounts.len() != 1 {
            return Err("inspect_mount_count_mismatch");
        }
        let mount = &mounts[0];
        if mount.get("Type").and_then(Value::as_str) != Some("bind")
            || mount.get("Destination").and_then(Value::as_str) != Some("/workspace")
            || mount.get("Source").and_then(Value::as_str) != Some(self.prep.plan.workspace.as_str())
            || mount.get("RW").and_then(Value::as_bool) != Some(false)
        {
            return Err("inspect_mount_mismatch");
        }
        let state = value.get("State").ok_or("inspect_state_missing")?;
        let oom_killed = state
            .get("OOMKilled")
            .and_then(Value::as_bool)
            .ok_or("inspect_oom_missing")?;
        let state_error = state
            .get("Error")
            .and_then(Value::as_str)
            .ok_or("inspect_error_missing")?;
        let started_at = state.get("StartedAt").and_then(Value::as_str);
        let finished_at = state.get("FinishedAt").and_then(Value::as_str);
        if pre {
            if state.get("Status").and_then(Value::as_str) != Some("created")
                || state.get("Running").and_then(Value::as_bool) != Some(false)
                || state.get("ExitCode").and_then(Value::as_i64) != Some(0)
                || oom_killed
                || !state_error.is_empty()
                || !is_unstarted(started_at)
                || !is_unstarted(finished_at)
            {
                return Err("inspect_created_state_mismatch");
            }
        } else {
            if state.get("Status").and_then(Value::as_str) != Some("exited")
                || state.get("Running").and_then(Value::as_bool) != Some(false)
                || oom_killed
                || !state_error.is_empty()
                || !is_started(started_at)
                || !is_started(finished_at)
                || state.get("ExitCode").and_then(Value::as_i64).is_none()
            {
                return Err("inspect_exited_state_mismatch");
            }
        }
        Ok(())
    }

    fn cleanup_all(&mut self) {
        if !self.create_attempted {
            self.cleanup = Cleanup::not_created("create_not_attempted");
            return;
        }
        if !self.container_created {
            if self.create_ambiguous {
                self.cleanup_ambiguous();
            } else {
                self.cleanup = Cleanup::not_created("create_failed_no_id");
            }
            return;
        }
        self.cleanup_known();
    }

    fn cleanup_known(&mut self) {
        let id = match self.container_id.clone() {
            Some(id) => id,
            None => {
                self.cleanup = Cleanup::unknown("owned_id_missing");
                return;
            }
        };
        let started = Instant::now();
        let inspect = match self.cleanup_command(
            "cleanup_inspect",
            vec![
                "container".into(),
                "inspect".into(),
                "--format".into(),
                "{{json .}}".into(),
                id.clone(),
            ],
            started,
        ) {
            Some(outcome) => outcome,
            None => return,
        };
        if !cleanup_outcome_settled(&inspect) {
            self.cleanup = Cleanup::unknown("cleanup_inspect_failed");
            return;
        }
        let value = match parse_json(&inspect) {
            Some(value) => value,
            None => {
                self.cleanup = Cleanup::unknown("cleanup_inspect_malformed");
                return;
            }
        };
        if !self.owned_identity(&value, &id) {
            self.cleanup = Cleanup::unknown("cleanup_identity_mismatch");
            return;
        }
        self.remove_and_verify(&id, started, "rm_force");
    }

    fn cleanup_ambiguous(&mut self) {
        let started = Instant::now();
        let ids = match self.cleanup_list(started) {
            Some(ids) => ids,
            None => return,
        };
        if ids.is_empty() {
            self.cleanup = Cleanup::unknown("ambiguous_create_absent_late_creation_possible");
            return;
        }
        if ids.len() != 1 {
            self.cleanup = Cleanup::unknown("ambiguous_create_multiple_matches");
            return;
        }
        let id = ids[0].clone();
        let inspect = match self.cleanup_command(
            "cleanup_inspect_ambiguous",
            vec![
                "container".into(),
                "inspect".into(),
                "--format".into(),
                "{{json .}}".into(),
                id.clone(),
            ],
            started,
        ) {
            Some(outcome) => outcome,
            None => return,
        };
        if !cleanup_outcome_settled(&inspect) {
            self.cleanup = Cleanup::unknown("ambiguous_inspect_failed");
            return;
        }
        let value = match parse_json(&inspect) {
            Some(value) => value,
            None => {
                self.cleanup = Cleanup::unknown("ambiguous_inspect_malformed");
                return;
            }
        };
        if !self.owned_identity(&value, &id) {
            self.cleanup = Cleanup::unknown("ambiguous_identity_mismatch");
            return;
        }
        self.container_id = Some(id.clone());
        self.remove_and_verify(&id, started, "rm_force_ambiguous");
    }

    fn remove_and_verify(&mut self, id: &str, started: Instant, method: &str) {
        let remove = match self.cleanup_command(
            "cleanup_rm",
            vec!["container".into(), "rm".into(), "--force".into(), id.to_string()],
            started,
        ) {
            Some(outcome) => outcome,
            None => return,
        };
        if !cleanup_outcome_settled(&remove) {
            self.cleanup = Cleanup::unknown("cleanup_rm_failed");
            return;
        }
        let ids = match self.cleanup_list(started) {
            Some(ids) => ids,
            None => return,
        };
        if ids.is_empty() {
            self.cleanup = Cleanup::settled(method, true, Some("container_absence_verified".into()));
        } else {
            self.cleanup = Cleanup::unknown("cleanup_absence_not_verified");
        }
    }

    fn cleanup_list(&mut self, started: Instant) -> Option<Vec<String>> {
        let args = vec![
            "container".into(),
            "ls".into(),
            "--all".into(),
            "--quiet".into(),
            "--no-trunc".into(),
            "--filter".into(),
            format!("label={OWNER_LABEL}={}", self.prep.token),
            "--filter".into(),
            format!("label={RUN_LABEL}={}", self.prep.plan.run_id),
            "--filter".into(),
            format!("name={}", self.prep.plan.container_name),
            "--format".into(),
            "{{.ID}}".into(),
        ];
        let outcome = self.cleanup_command("cleanup_list", args, started)?;
        if !cleanup_outcome_settled(&outcome) {
            self.cleanup = Cleanup::unknown("cleanup_list_failed");
            return None;
        }
        let text = outcome.stdout_lossy();
        let ids: Vec<String> = text
            .split_whitespace()
            .map(|id| id.to_string())
            .collect();
        if ids.iter().any(|id| !is_container_id(id)) {
            self.cleanup = Cleanup::unknown("cleanup_list_malformed");
            return None;
        }
        Some(ids)
    }

    fn cleanup_command(
        &mut self,
        name: &str,
        args: Vec<String>,
        started: Instant,
    ) -> Option<CommandOutcome> {
        let remaining = self
            .prep
            .deadlines
            .cleanup_total
            .saturating_sub(started.elapsed());
        if remaining.is_zero() {
            self.cleanup = Cleanup::unknown("cleanup_deadline_exceeded");
            return None;
        }
        let request = self.request(args, remaining, true);
        let outcome = self.runner.run(&request);
        self.record(name, &request, &outcome);
        Some(outcome)
    }

    fn owned_identity(&self, value: &Value, id: &str) -> bool {
        if value.get("Id").and_then(Value::as_str) != Some(id) {
            return false;
        }
        let name = value
            .get("Name")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim_start_matches('/');
        if name != self.prep.plan.container_name {
            return false;
        }
        let labels = match value.pointer("/Config/Labels").and_then(Value::as_object) {
            Some(labels) => labels,
            None => return false,
        };
        labels.get(RUN_LABEL).and_then(Value::as_str) == Some(self.prep.plan.run_id.as_str())
            && labels.get(OWNER_LABEL).and_then(Value::as_str) == Some(self.prep.token.as_str())
    }

    fn write_attach_stream(&self, name: &str, stderr: bool) -> Option<PathBuf> {
        let outcome = self.attach.as_ref()?;
        let stream = if stderr {
            &outcome.stderr
        } else {
            &outcome.stdout
        };
        let path = self.prep.output.join(name);
        let mut file = OpenOptions::new().write(true).create_new(true).open(&path).ok()?;
        file.write_all(&stream.bytes).ok()?;
        Some(path)
    }

    fn receipt(
        &self,
        status: Status,
        diagnostic: Option<&str>,
        stdout_path: Option<&Path>,
        stderr_path: Option<&Path>,
    ) -> Value {
        let candidate = self
            .prep
            .plan
            .inventory
            .files
            .iter()
            .find(|file| matches!(file.role, isolation_plan::FileRole::Candidate));
        let container = self.container_id.as_ref().map(|id| {
            json!({
                "id": id,
                "name": self.prep.plan.container_name,
                "owner_token": self.prep.token,
                "attached": self.attach.as_ref().map(|outcome| json!({
                    "exit_code": outcome.exit_code,
                    "timed_out": outcome.timed_out,
                    "output_limit": outcome.output_limit,
                    "cancelled": outcome.cancelled,
                    "direct_child_reaped": outcome.direct_child_reaped,
                })),
            })
        });
        json!({
            "schema_version": RECEIPT_SCHEMA_VERSION,
            "evidence_kind": self.prep.evidence_kind,
            "run_id": self.prep.plan.run_id,
            "status": status,
            "diagnostic": diagnostic,
            "workspace": {
                "path": self.prep.plan.workspace,
                "candidate_file": self.prep.plan.candidate_file,
                "candidate_sha256": candidate.map(|file| file.sha256.clone()),
                "inventory": self.prep.plan.inventory.files,
                "inventory_file_count": self.prep.plan.inventory.file_count,
                "inventory_total_bytes": self.prep.plan.inventory.total_bytes,
            },
            "image": {
                "reference": IMAGE,
                "observed_id": self.image_id,
                "daemon_architecture": self.daemon_arch,
                "image_architecture": self.image_arch,
            },
            "container": container,
            "public_check_exit": self.public_check_exit,
            "commands": self.commands,
            "cleanup": self.cleanup,
            "stdout_file": stdout_path.map(|path| path.to_string_lossy().into_owned()),
            "stderr_file": stderr_path.map(|path| path.to_string_lossy().into_owned()),
            "elapsed_ms": self.started.elapsed().as_millis() as u64,
            "observed": {
                "setup_elapsed_ms": self.setup_start.elapsed().as_millis() as u64,
                "post_attach_budget_ms": self.prep.deadlines.post_attach.as_millis() as u64,
                "cleanup_budget_ms": self.prep.deadlines.cleanup_total.as_millis() as u64,
                "execution_budget_ms": self.prep.deadlines.execution.as_millis() as u64,
            },
            "limitations": [
                "stable_filesystem_assumption",
                "local_trusted_docker_daemon_assumption",
                "direct_child_only_process_settlement",
                "not_isolation_qualification",
                "public_check_exit_is_not_hidden_functional_or_workflow_grade",
                if matches!(self.prep.evidence_kind, EvidenceKind::SyntheticExecutorControl) {
                    "synthetic_command_boundary_not_real_docker"
                } else {
                    "production_label_does_not_imply_qualified_isolation"
                },
            ],
        })
    }
}

fn create_args(plan: &Plan, token: &str) -> Vec<String> {
    let mut args = vec!["container".to_string(), "create".to_string()];
    for argument in plan.argv.iter().skip(1) {
        if argument == "--rm" {
            continue;
        }
        args.push(argument.clone());
    }
    let image_index = args
        .iter()
        .position(|argument| argument == IMAGE)
        .expect("planner argv contains the immutable image");
    args.splice(
        image_index..image_index,
        [
            "--label".to_string(),
            format!("{OWNER_LABEL}={token}"),
            "--log-driver=none".to_string(),
        ],
    );
    args
}

fn same_inventory(left: &Plan, right: &Plan) -> bool {
    left.workspace == right.workspace
        && left.candidate_file == right.candidate_file
        && left.inventory.files.len() == right.inventory.files.len()
        && left
            .inventory
            .files
            .iter()
            .zip(&right.inventory.files)
            .all(|(a, b)| a.name == b.name && a.bytes == b.bytes && a.sha256 == b.sha256)
}

fn cleanup_outcome_settled(outcome: &CommandOutcome) -> bool {
    outcome.exit_success() && command_observation_clean(outcome)
}

fn command_observation_clean(outcome: &CommandOutcome) -> bool {
    outcome.direct_child_reaped
        && !outcome.spawn_failed
        && !outcome.timed_out
        && !outcome.output_limit
        && !outcome.cancelled
        && outcome.issue.is_none()
        && outcome.reader_settled
        && outcome.stdout.settled
        && outcome.stderr.settled
        && !outcome.stdout.read_failed
        && !outcome.stderr.read_failed
        && !outcome.stdout.truncated
        && !outcome.stderr.truncated
}

fn inspect_settled(outcome: &CommandOutcome) -> Result<(), &'static str> {
    if outcome.timed_out {
        Err("post_inspect_timeout")
    } else if outcome.output_limit {
        Err("post_inspect_output_limit")
    } else if outcome.cancelled {
        Err("post_inspect_cancelled")
    } else if outcome.spawn_failed || !outcome.direct_child_reaped {
        Err("post_inspect_unsettled")
    } else if !outcome.exit_success() {
        Err("post_inspect_failed")
    } else if outcome.issue.is_some()
        || !outcome.reader_settled
        || !outcome.stdout.settled
        || !outcome.stderr.settled
        || outcome.stdout.read_failed
        || outcome.stderr.read_failed
    {
        Err("post_inspect_capture_failed")
    } else {
        Ok(())
    }
}

fn parse_json(outcome: &CommandOutcome) -> Option<Value> {
    serde_json::from_str(outcome.stdout_lossy().trim()).ok()
}

fn parse_container_id(outcome: &CommandOutcome) -> Option<String> {
    let text = outcome.stdout_lossy();
    let text = text.trim();
    if is_container_id(text) {
        Some(text.to_string())
    } else {
        None
    }
}

fn docker_name_conflict(outcome: &CommandOutcome, name: &str) -> bool {
    let diagnostic = outcome.stderr_lossy();
    let diagnostic = diagnostic.trim();
    let diagnostic = diagnostic
        .strip_prefix("Error response from daemon: ")
        .unwrap_or(diagnostic);
    let prefix = format!("Conflict. The container name \"/{name}\" is already in use by container \"");
    let Some((id, _)) = diagnostic
        .strip_prefix(&prefix)
        .and_then(|rest| rest.split_once("\"."))
    else {
        return false;
    };
    is_container_id(id)
}

fn is_container_id(text: &str) -> bool {
    text.len() == 64 && text.bytes().all(|byte| byte.is_ascii_hexdigit())
}

fn is_sha256_id(text: &str) -> bool {
    text.strip_prefix("sha256:")
        .is_some_and(is_container_id)
}

fn normalize_arch(value: Option<&str>) -> Option<String> {
    match value {
        Some("x86_64" | "amd64") => Some("amd64".to_string()),
        Some("aarch64" | "arm64") => Some("arm64".to_string()),
        _ => None,
    }
}

fn is_unstarted(value: Option<&str>) -> bool {
    matches!(
        value,
        Some("0001-01-01T00:00:00Z")
            | Some("0001-01-01T00:00:00.000000000Z")
            | Some("0001-01-01T00:00:00.0000000Z")
            | Some("0000-00-00T00:00:00Z")
    )
}

fn is_started(value: Option<&str>) -> bool {
    value.is_some_and(is_docker_time)
}

fn is_docker_time(text: &str) -> bool {
    let bytes = text.as_bytes();
    if bytes.len() < 20
        || bytes[4] != b'-'
        || bytes[7] != b'-'
        || bytes[10] != b'T'
        || bytes[13] != b':'
        || bytes[16] != b':'
    {
        return false;
    }
    let part = |start: usize, end: usize| -> Option<u32> {
        std::str::from_utf8(&bytes[start..end]).ok()?.parse().ok()
    };
    let (Some(year), Some(month), Some(day), Some(hour), Some(minute), Some(second)) = (
        part(0, 4),
        part(5, 7),
        part(8, 10),
        part(11, 13),
        part(14, 16),
        part(17, 19),
    ) else {
        return false;
    };
    if year < 1970
        || !(1..=12).contains(&month)
        || !(1..=31).contains(&day)
        || hour > 23
        || minute > 59
        || second > 60
    {
        return false;
    }
    let rest = &text[19..];
    if let Some(rest) = rest.strip_prefix('.') {
        let digits = rest.bytes().take_while(u8::is_ascii_digit).count();
        if digits == 0 || digits == rest.len() {
            return false;
        }
        let zone = &rest[digits..];
        return zone == "Z" || is_numeric_zone(zone);
    }
    rest == "Z" || is_numeric_zone(rest)
}

fn is_numeric_zone(zone: &str) -> bool {
    let bytes = zone.as_bytes();
    bytes.len() == 6
        && matches!(bytes[0], b'+' | b'-')
        && bytes[1].is_ascii_digit()
        && bytes[2].is_ascii_digit()
        && bytes[3] == b':'
        && bytes[4].is_ascii_digit()
        && bytes[5].is_ascii_digit()
}

fn exact_string_array(value: Option<&Value>) -> Option<Vec<String>> {
    let items = value?.as_array()?;
    let mut result = Vec::with_capacity(items.len());
    for item in items {
        result.push(item.as_str()?.to_string());
    }
    Some(result)
}

fn post_exit(value: &Value) -> Option<i32> {
    let exit = value.pointer("/State/ExitCode")?.as_i64()?;
    i32::try_from(exit).ok()
}

fn format_fail(fail: &Fail) -> String {
    match &fail.detail {
        Some(detail) => format!("{}:{}", fail.code, detail),
        None => fail.code.to_string(),
    }
}

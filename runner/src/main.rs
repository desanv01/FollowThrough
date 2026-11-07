mod paths;
mod process;

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    env,
    ffi::OsString,
    fs::{self, File, OpenOptions},
    io::{self, Write},
    path::{Path, PathBuf},
    process::{Command, ExitCode},
    time::{Duration, SystemTime, UNIX_EPOCH},
};

const VERSION: &str = "0.1";
const EVIDENCE_KIND: &str = "real_local_control";
const GRADER: &str = "grader/retry_processing.py";
const REQUIRED_CHECK_IDS: [&str; 10] = [
    "new_delivery",
    "duplicate_replay",
    "conflicting_units",
    "tenant_and_event_identity",
    "malformed_items",
    "mixed_batch",
    "rollback_after_credit",
    "rollback_after_mark",
    "interrupted_batch_retry",
    "ack_loss_reopen_retry",
];
const FILE_LIMIT: usize = 1024 * 1024;
const HELP: &str = "followthrough control --project-root <path> --candidate <path> --python <executable> --output-dir <new path> [--timeout-seconds 60]\n\nRuns only trusted local fixture code. This is NOT a sandbox.\nRelative candidate paths start at the project root. Output paths start at the working directory and need an existing parent.\nExit status: 0 pass, 1 functional fail, 2 preflight, candidate execution, or infrastructure error.";

struct Options {
    project: PathBuf,
    candidate: PathBuf,
    python: PathBuf,
    output: PathBuf,
    timeout_seconds: u64,
}

impl Options {
    fn parse(mut args: impl Iterator<Item = OsString>) -> Result<Self, String> {
        let mut project = None;
        let mut candidate = None;
        let mut python = None;
        let mut output = None;
        let mut timeout = None;
        while let Some(flag) = args.next() {
            let field = match flag.to_str() {
                Some("--project-root") => &mut project,
                Some("--candidate") => &mut candidate,
                Some("--python") => &mut python,
                Some("--output-dir") => &mut output,
                Some("--timeout-seconds") => &mut timeout,
                _ => return Err("unknown option (see --help)".into()),
            };
            if field.is_some() {
                return Err(format!("duplicate option {}", flag.to_string_lossy()));
            }
            *field = Some(
                args.next()
                    .filter(|arg| !arg.is_empty())
                    .ok_or_else(|| format!("missing value for {}", flag.to_string_lossy()))?,
            );
        }
        let timeout_seconds = match timeout {
            Some(value) => value
                .to_str()
                .and_then(|value| value.parse::<u64>().ok())
                .filter(|&seconds| (1..=3600).contains(&seconds))
                .ok_or("--timeout-seconds must be an integer in 1..=3600")?,
            None => 60,
        };
        Ok(Self {
            project: project.ok_or("missing --project-root")?.into(),
            candidate: candidate.ok_or("missing --candidate")?.into(),
            python: python.ok_or("missing --python")?.into(),
            output: output.ok_or("missing --output-dir")?.into(),
            timeout_seconds,
        })
    }
}

struct EventLog {
    file: File,
    run_id: String,
    seq: u64,
}

impl EventLog {
    fn new(output: &Path) -> io::Result<Self> {
        Ok(Self {
            file: new_file(&output.join("events.jsonl"))?,
            run_id: format!("local-{}-{}", utc_ms(), std::process::id()),
            seq: 0,
        })
    }

    fn record(&mut self, event_type: &str, payload: Value) -> io::Result<()> {
        self.seq += 1;
        let event = json!({
            "schema_version": VERSION,
            "run_id": self.run_id,
            "seq": self.seq,
            "event_type": event_type,
            "timestamp_utc": utc_ms(),
            "timestamp_format": "unix_milliseconds",
            "evidence_kind": EVIDENCE_KIND,
            "payload": payload,
        });
        let mut line = serde_json::to_vec(&event)?;
        line.push(b'\n');
        self.file.write_all(&line)
    }
}

#[derive(Serialize)]
struct Checkpoint {
    original_path: PathBuf,
    snapshot_path: PathBuf,
    sha256: String,
    bytes: usize,
}

#[derive(Deserialize, Serialize)]
struct CandidateIdentity {
    path: PathBuf,
    sha256: String,
}

#[derive(Deserialize)]
struct Grade {
    schema_version: String,
    task_family: String,
    evidence_kind: String,
    candidate: CandidateIdentity,
    functional_pass: bool,
    checks: Vec<GradeCheck>,
    errors: Vec<Value>,
}

#[derive(Deserialize)]
struct GradeCheck {
    id: String,
    passed: bool,
    #[serde(rename = "detail")]
    _detail: String,
}

#[derive(Serialize)]
struct GradeReceipt {
    path: PathBuf,
    sha256: String,
    candidate: CandidateIdentity,
    functional_pass: bool,
    check_count: usize,
    error_count: usize,
}

struct GradeResult {
    receipt: GradeReceipt,
    candidate_errors_only: bool,
}

#[derive(Serialize)]
struct Summary {
    schema_version: &'static str,
    run_id: String,
    evidence_kind: &'static str,
    status: &'static str,
    issue: Option<String>,
    checkpoint: Option<Checkpoint>,
    grader: Option<process::Outcome>,
    grade: Option<GradeReceipt>,
}

struct RunInputs {
    project: PathBuf,
    grader: PathBuf,
    candidate: PathBuf,
    python: PathBuf,
    output: PathBuf,
    timeout_seconds: u64,
}

fn utc_ms() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
}

fn new_file(path: &Path) -> io::Result<File> {
    OpenOptions::new().write(true).create_new(true).open(path)
}

fn sha256(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn python_executable(cwd: &Path, requested: &Path) -> io::Result<PathBuf> {
    if requested.as_os_str() != requested.file_name().unwrap_or_default() {
        let path = paths::absolute_from(cwd, requested)?;
        if path.is_file() {
            return Ok(path);
        }
    } else if let Some(search) = env::var_os("PATH") {
        for dir in
            env::split_paths(&search).filter(|dir| dir.is_absolute() && dir.to_str().is_some())
        {
            let mut path = dir.join(requested);
            if cfg!(windows) && path.extension().is_none() {
                path.set_extension("exe");
            }
            if path.to_str().is_some() && path.is_file() {
                return Ok(path);
            }
        }
    }
    Err(io::Error::new(
        io::ErrorKind::NotFound,
        "Python executable not found",
    ))
}

fn grade_receipt(grade_path: &Path, checkpoint: &Checkpoint) -> Result<GradeResult, String> {
    let bytes = paths::read_bounded(grade_path, FILE_LIMIT)
        .map_err(|error| format!("grade read failed: {error}"))?;
    let grade: Grade = serde_json::from_slice(&bytes).map_err(|error| {
        format!(
            "invalid grade JSON at line {}, column {}",
            error.line(),
            error.column()
        )
    })?;
    if grade.schema_version != VERSION
        || grade.task_family != "retry-processing"
        || grade.evidence_kind != EVIDENCE_KIND
    {
        return Err("grade schema, task, or evidence label mismatch".into());
    }
    if !grade.candidate.path.is_absolute()
        || fs::canonicalize(&grade.candidate.path).ok().as_ref() != Some(&checkpoint.snapshot_path)
        || grade.candidate.sha256 != checkpoint.sha256
        || paths::read_bounded(&checkpoint.snapshot_path, FILE_LIMIT)
            .map(|bytes| sha256(&bytes))
            .ok()
            != Some(checkpoint.sha256.clone())
    {
        return Err("grade candidate identity differs from the checkpoint".into());
    }
    if grade.errors.is_empty()
        && (grade.checks.len() != REQUIRED_CHECK_IDS.len()
            || !REQUIRED_CHECK_IDS
                .iter()
                .all(|required| grade.checks.iter().any(|check| check.id == *required)))
    {
        return Err("complete grade must include each required retry check ID exactly once".into());
    }
    if grade.checks.iter().any(|check| check.id.is_empty())
        || (grade.errors.is_empty()
            && grade.functional_pass != grade.checks.iter().all(|check| check.passed))
        || (!grade.errors.is_empty() && grade.functional_pass)
    {
        return Err("inconsistent grade checks or errors".into());
    }
    Ok(GradeResult {
        receipt: GradeReceipt {
            path: grade_path.into(),
            sha256: sha256(&bytes),
            candidate: grade.candidate,
            functional_pass: grade.functional_pass,
            check_count: grade.checks.len(),
            error_count: grade.errors.len(),
        },
        candidate_errors_only: !grade.errors.is_empty()
            && grade
                .errors
                .iter()
                .all(|error| error.get("origin").and_then(Value::as_str) == Some("candidate")),
    })
}

fn perform(inputs: &RunInputs, events: &mut EventLog, summary: &mut Summary) -> Result<(), String> {
    let RunInputs {
        project,
        grader,
        candidate,
        python,
        output,
        timeout_seconds,
    } = inputs;
    let source = paths::read_bounded(candidate, FILE_LIMIT)
        .map_err(|error| format!("candidate read failed: {error}"))?;
    let grader_bytes = paths::read_bounded(grader, FILE_LIMIT)
        .map_err(|error| format!("grader read failed: {error}"))?;
    let snapshot_dir = output.join("checkpoint");
    fs::create_dir(&snapshot_dir)
        .map_err(|error| format!("checkpoint directory failed: {error}"))?;
    let snapshot_path = snapshot_dir.join("processor.py");
    new_file(&snapshot_path)
        .and_then(|mut file| file.write_all(&source))
        .map_err(|error| format!("checkpoint write failed: {error}"))?;
    let checkpoint = Checkpoint {
        original_path: candidate.clone(),
        snapshot_path: fs::canonicalize(snapshot_path)
            .map_err(|error| format!("checkpoint path failed: {error}"))?,
        sha256: sha256(&source),
        bytes: source.len(),
    };
    let grade_path = output.join("grade.json");
    let argv = [
        Path::new("-I"),
        Path::new("-B"),
        grader,
        Path::new("--candidate"),
        &checkpoint.snapshot_path,
        Path::new("--output"),
        &grade_path,
    ];
    let mut command = Command::new(python);
    command.args(argv).current_dir(project).env_clear();
    for key in ["PATH", "SystemRoot", "WINDIR", "TEMP", "TMP", "TMPDIR"] {
        if let Some(value) = env::var_os(key) {
            command.env(key, value);
        }
    }
    let command_receipt = json!({
        "executable": python,
        "argv": argv,
        "cwd": project,
        "grader_sha256": sha256(&grader_bytes),
        "timeout_seconds": timeout_seconds,
        "stream_limit_bytes_each": process::STREAM_LIMIT,
        "cleanup_scope": process::CLEANUP_SCOPE,
    });
    events
        .record("checkpoint", json!(&checkpoint))
        .map_err(|error| error.to_string())?;
    summary.checkpoint = Some(checkpoint);
    events
        .record("grader_launch_requested", command_receipt)
        .map_err(|error| error.to_string())?;
    let outcome = process::run(&mut command, Duration::from_secs(*timeout_seconds), |pid| {
        events.record("grader_started", json!({ "pid": pid }))
    })
    .map_err(|error| format!("grader launch failed: {error}"))?;
    let outcome_event = json!(&outcome);
    summary.grader = Some(outcome);
    events
        .record("grader_finished", outcome_event)
        .map_err(|error| error.to_string())?;
    let outcome = summary.grader.as_ref().expect("grader outcome recorded");
    if let Some(issue) = outcome.issue {
        return Err(issue.into());
    }
    let checkpoint = summary.checkpoint.as_ref().expect("checkpoint recorded");
    let grade = grade_receipt(&grade_path, checkpoint).map_err(|issue| {
        if outcome.exit_success {
            issue
        } else {
            format!("grader exited unsuccessfully and {issue}")
        }
    })?;
    events
        .record("grade_result", json!(&grade.receipt))
        .map_err(|error| error.to_string())?;
    summary.grade = Some(grade.receipt);
    let receipt = summary.grade.as_ref().expect("grade receipt recorded");
    if !outcome.exit_success {
        if grade.candidate_errors_only {
            summary.status = "candidate_execution";
        }
        return Err("grader exited unsuccessfully".into());
    }
    if receipt.error_count > 0 {
        return Err("grader reported execution errors despite exiting successfully".into());
    }
    summary.status = if receipt.functional_pass {
        "functional_pass"
    } else {
        "functional_fail"
    };
    Ok(())
}

fn control(options: Options) -> Result<u8, String> {
    let cwd = env::current_dir().map_err(|error| error.to_string())?;
    let project = paths::absolute_from(&cwd, &options.project)
        .and_then(|path| paths::checked_existing(&path))
        .map_err(|error| format!("invalid project root: {error}"))?;
    if !project.is_dir() {
        return Err("project root must be a directory".into());
    }
    let grader = paths::project_file(&project, Path::new(GRADER))
        .map_err(|error| format!("invalid grader path: {error}"))?;
    let candidate = paths::project_file(&project, &options.candidate)
        .map_err(|error| format!("invalid candidate path: {error}"))?;
    let python = python_executable(&cwd, &options.python)
        .map_err(|error| format!("invalid Python executable: {error}"))?;
    let output = paths::create_output_dir(&cwd, &options.output)
        .map_err(|error| format!("cannot create new output directory: {error}"))?;

    let inputs = RunInputs {
        project,
        grader,
        candidate,
        python,
        output,
        timeout_seconds: options.timeout_seconds,
    };
    let mut events = EventLog::new(&inputs.output).map_err(|error| error.to_string())?;
    events
        .record(
            "run_started",
            json!({ "output_dir": inputs.output, "sandbox": false }),
        )
        .map_err(|error| error.to_string())?;
    let mut summary = Summary {
        schema_version: VERSION,
        run_id: events.run_id.clone(),
        evidence_kind: EVIDENCE_KIND,
        status: "infrastructure_error",
        issue: None,
        checkpoint: None,
        grader: None,
        grade: None,
    };
    summary.issue = perform(&inputs, &mut events, &mut summary).err();
    let final_event = events.record(
        "run_finished",
        json!({ "status": summary.status, "issue": summary.issue }),
    );
    let mut file =
        new_file(&inputs.output.join("summary.json")).map_err(|error| error.to_string())?;
    serde_json::to_writer_pretty(&mut file, &summary).map_err(|error| error.to_string())?;
    file.write_all(b"\n").map_err(|error| error.to_string())?;
    final_event.map_err(|error| error.to_string())?;
    if let Some(issue) = summary.issue {
        eprintln!("followthrough: {issue}");
    }
    Ok(match summary.status {
        "functional_pass" => 0,
        "functional_fail" => 1,
        _ => 2,
    })
}

fn main() -> ExitCode {
    let mut args = env::args_os().skip(1).peekable();
    let result = match args.next().as_deref().and_then(|value| value.to_str()) {
        Some("--version") if args.len() == 0 => {
            println!("followthrough {}", env!("CARGO_PKG_VERSION"));
            return ExitCode::SUCCESS;
        }
        Some("--help" | "-h") if args.len() == 0 => {
            println!("{HELP}");
            return ExitCode::SUCCESS;
        }
        Some("control") => {
            if args.len() == 1
                && args
                    .peek()
                    .is_some_and(|arg| arg == "--help" || arg == "-h")
            {
                println!("{HELP}");
                return ExitCode::SUCCESS;
            }
            Options::parse(args).and_then(control)
        }
        _ => Err("expected control, --help, or --version".into()),
    };
    match result {
        Ok(code) => ExitCode::from(code),
        Err(error) => {
            eprintln!("followthrough: {error}");
            ExitCode::from(2)
        }
    }
}

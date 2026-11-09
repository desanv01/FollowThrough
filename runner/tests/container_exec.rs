//! Offline synthetic executor tests for P2-EXECUTOR/r2.
//!
//! All evidence is synthetic_executor_control. Fake Docker replies and inert
//! candidate bytes only; no installed Docker, container, candidate Python or
//! provider is invoked. One test compiles the owned `fake_docker.rs` helper and
//! exercises the real production process wrapper's bounded capture/timeout/reap.

#[path = "../src/container_exec.rs"]
mod container_exec;

use container_exec::isolation_plan::IMAGE;
use container_exec::{
    execute, BoundedStream, CommandOutcome, CommandRequest, CommandRunner, Config, Deadlines,
    EvidenceKind, ProcessRunner, Status, DOCKER_ENDPOINT,
};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    env, fs,
    path::{Path, PathBuf},
    process::Command,
    sync::{
        atomic::{AtomicBool, AtomicUsize, Ordering},
        Arc, Mutex, OnceLock,
    },
    time::Duration,
};

static COUNTER: AtomicUsize = AtomicUsize::new(0);
const IMAGE_ID: &str = "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
const FOREIGN_ID: &str = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc";

fn scratch_root() -> PathBuf {
    static ROOT: OnceLock<PathBuf> = OnceLock::new();
    ROOT.get_or_init(|| {
        // Deterministic once-only setup. Prefer the Cargo-owned target directory
        // (already writable in an integrated-shaped checkout) over a fresh sibling
        // tree; fall back to the overlay tmp directory when run without Cargo env.
        let base = env::var_os("CARGO_TARGET_DIR")
            .map(PathBuf::from)
            .map(|target| target.join("executor-test-scratch"))
            .unwrap_or_else(|| Path::new(env!("CARGO_MANIFEST_DIR")).join("../tmp/tests"));
        fs::create_dir_all(&base).unwrap();
        fs::canonicalize(base).unwrap()
    })
    .clone()
}

fn unique_dir(label: &str) -> PathBuf {
    let serial = COUNTER.fetch_add(1, Ordering::Relaxed);
    let path = scratch_root().join(format!("{label}-{}-{serial}", std::process::id()));
    fs::create_dir(&path).unwrap();
    path
}

fn sha256_hex(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

struct Fixture {
    root: PathBuf,
    workspace: PathBuf,
    output: PathBuf,
    docker: PathBuf,
}

impl Fixture {
    fn new(label: &str) -> Self {
        let root = unique_dir(label);
        let workspace = root.join("work space");
        fs::create_dir(&workspace).unwrap();
        fs::write(
            workspace.join("processor.py"),
            b"# inert candidate fixture; never executed\n",
        )
        .unwrap();
        fs::write(
            workspace.join("public_checks.py"),
            b"# inert public checks fixture; never executed\n",
        )
        .unwrap();
        fs::write(workspace.join("TASK.md"), b"# inert task\n").unwrap();
        let docker = root.join("fake docker.bin");
        fs::write(&docker, b"not a real executable").unwrap();
        let output = root.join("output dir");
        Self {
            root,
            workspace,
            output,
            docker,
        }
    }

    fn config(&self) -> Config {
        Config {
            workspace: self.workspace.clone(),
            run_id: "exec-run-1".to_string(),
            output_dir: self.output.clone(),
            docker: self.docker.clone(),
            execute: true,
            evidence_kind: EvidenceKind::SyntheticExecutorControl,
            deadlines: Deadlines::default(),
            cancellation: Arc::new(AtomicBool::new(false)),
        }
    }
}

#[derive(Clone)]
struct Scenario {
    info_fail: bool,
    daemon_os: &'static str,
    daemon_arch: &'static str,
    image_missing: bool,
    image_arch: &'static str,
    image_digest_wrong: bool,
    create_collision: bool,
    create_timeout: bool,
    create_capture_failure: bool,
    create_malformed: bool,
    ambiguous_owned: bool,
    ambiguous_unrelated: bool,
    inspect_mismatch: Option<&'static str>,
    cleanup_identity_mismatch: bool,
    cleanup_rm_fail: bool,
    cleanup_list_fail: bool,
    cleanup_list_orphan: bool,
    drift_on_inspect: bool,
    post: PostState,
    attach_exit: i32,
    start_timeout: bool,
    start_overflow: bool,
    start_cancelled: bool,
    post_inspect_timeout: bool,
    cleanup_rm_timeout: bool,
    plant_receipt: bool,
    create_nonzero_malformed: bool,
    security_disabled: bool,
    privileged_missing: bool,
    extra_tmpfs: bool,
    command_nonstring: bool,
    post_oom_missing: bool,
    post_error_missing: bool,
    post_bad_times: bool,
    post_capture_failure: bool,
}

impl Default for Scenario {
    fn default() -> Self {
        Self {
            info_fail: false,
            daemon_os: "linux",
            daemon_arch: "amd64",
            image_missing: false,
            image_arch: "amd64",
            image_digest_wrong: false,
            create_collision: false,
            create_timeout: false,
            create_capture_failure: false,
            create_malformed: false,
            ambiguous_owned: false,
            ambiguous_unrelated: false,
            inspect_mismatch: None,
            cleanup_identity_mismatch: false,
            cleanup_rm_fail: false,
            cleanup_list_fail: false,
            cleanup_list_orphan: false,
            drift_on_inspect: false,
            post: PostState::Exited,
            attach_exit: 0,
            start_timeout: false,
            start_overflow: false,
            start_cancelled: false,
            post_inspect_timeout: false,
            cleanup_rm_timeout: false,
            plant_receipt: false,
            create_nonzero_malformed: false,
            security_disabled: false,
            privileged_missing: false,
            extra_tmpfs: false,
            command_nonstring: false,
            post_oom_missing: false,
            post_error_missing: false,
            post_bad_times: false,
            post_capture_failure: false,
        }
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum PostState {
    Exited,
    Created,
    Dead,
    Oom,
    ErrorState,
    MissingTimes,
    DefaultZero,
}

struct FakeState {
    id: Option<String>,
    late_id: Option<String>,
    name: String,
    mount: String,
    run_label: String,
    owner_label: String,
    started: bool,
    removed: bool,
    inspect_calls: usize,
    drift_done: bool,
    requests: Vec<CommandRequest>,
    create_argv: Vec<String>,
    rm_ids: Vec<String>,
}

struct FakeDocker {
    workspace: PathBuf,
    output: PathBuf,
    scenario: Scenario,
    state: Mutex<FakeState>,
}

impl FakeDocker {
    fn new(workspace: &Path, output: &Path, scenario: Scenario) -> Self {
        Self {
            workspace: workspace.to_path_buf(),
            output: output.to_path_buf(),
            scenario,
            state: Mutex::new(FakeState {
                id: None,
                late_id: None,
                name: String::new(),
                mount: String::new(),
                run_label: String::new(),
                owner_label: String::new(),
                started: false,
                removed: false,
                inspect_calls: 0,
                drift_done: false,
                requests: Vec::new(),
                create_argv: Vec::new(),
                rm_ids: Vec::new(),
            }),
        }
    }

    fn state(&self) -> std::sync::MutexGuard<'_, FakeState> {
        self.state.lock().unwrap()
    }
}

fn stream(bytes: &[u8]) -> BoundedStream {
    BoundedStream {
        bytes: bytes.to_vec(),
        observed_bytes: bytes.len() as u64,
        truncated: false,
        read_failed: false,
        settled: true,
    }
}

fn name_conflict_stderr(name: &str) -> BoundedStream {
    stream(
        format!(
            "Error response from daemon: Conflict. The container name \"/{name}\" is already in use by container \"{FOREIGN_ID}\". You have to remove (or rename) that container to be able to reuse that name.\n"
        )
        .as_bytes(),
    )
}

fn outcome(exit: i32, stdout: &[u8]) -> CommandOutcome {
    CommandOutcome {
        pid: Some(4242),
        exit_code: Some(exit),
        stdout: stream(stdout),
        stderr: stream(b""),
        timed_out: false,
        output_limit: false,
        cancelled: false,
        spawn_failed: false,
        direct_child_reaped: true,
        reader_settled: true,
        elapsed_ms: 1,
        issue: None,
    }
}

fn timed_out() -> CommandOutcome {
    CommandOutcome {
        timed_out: true,
        issue: Some("timed_out".into()),
        ..outcome(0, b"")
    }
}

fn output_limit() -> CommandOutcome {
    CommandOutcome {
        output_limit: true,
        stdout: BoundedStream {
            bytes: vec![b'x'; 32768],
            observed_bytes: 32769,
            truncated: true,
            read_failed: false,
            settled: true,
        },
        issue: Some("output_limit_exceeded".into()),
        ..outcome(0, b"")
    }
}

fn cancelled() -> CommandOutcome {
    CommandOutcome {
        cancelled: true,
        issue: Some("cancelled".into()),
        ..outcome(0, b"")
    }
}

fn json_outcome(value: &Value) -> CommandOutcome {
    outcome(0, serde_json::to_string(value).unwrap().as_bytes())
}

fn image_id() -> String {
    IMAGE_ID.to_string()
}

fn find_value(args: &[String], flag: &str) -> Option<String> {
    args.iter()
        .position(|arg| arg == flag)
        .and_then(|index| args.get(index + 1).cloned())
}

fn label_value(args: &[String], prefix: &str) -> Option<String> {
    args.iter()
        .enumerate()
        .find(|(_, arg)| arg.starts_with(prefix))
        .map(|(index, _)| args[index].to_string())
}

impl CommandRunner for FakeDocker {
    fn run(&self, request: &CommandRequest) -> CommandOutcome {
        let mut state = self.state.lock().unwrap();
        state.requests.push(request.clone());
        let args = &request.args;
        if args.len() < 3 || args[0] != "--host" || args[1] != DOCKER_ENDPOINT {
            return outcome(2, b"bad host prefix");
        }
        let rest = &args[2..];
        match rest.first().map(String::as_str) {
            Some("info") => {
                if self.scenario.info_fail {
                    let mut failure = outcome(1, b"");
                    failure.stderr = stream(&b"private setup diagnostic;".repeat(256));
                    return failure;
                }
                json_outcome(&json!({
                    "OSType": self.scenario.daemon_os,
                    "Architecture": self.scenario.daemon_arch,
                }))
            }
            Some("image") => {
                if !rest.iter().any(|arg| arg == "--format")
                    || !rest.iter().any(|arg| arg == "{{json .}}")
                {
                    return outcome(2, b"image inspect requires the formatted object argv");
                }
                if self.scenario.image_missing {
                    return outcome(1, b"no such image");
                }
                let digest = if self.scenario.image_digest_wrong {
                    "python@sha256:0000000000000000000000000000000000000000000000000000000000000000"
                } else {
                    IMAGE
                };
                json_outcome(&json!({
                    "Id": image_id(),
                    "Os": "linux",
                    "Architecture": self.scenario.image_arch,
                    "RepoDigests": [digest],
                }))
            }
            Some("container") => match rest.get(1).map(String::as_str) {
                Some("create") => {
                    state.create_argv = rest.to_vec();
                    state.name =
                        find_value(rest, "--name").unwrap_or_else(|| "missing-name".to_string());
                    if self.scenario.create_collision {
                        return CommandOutcome {
                            stderr: name_conflict_stderr(&state.name),
                            ..outcome(1, b"")
                        };
                    }
                    state.mount = find_value(rest, "--mount")
                        .unwrap_or_default()
                        .split(',')
                        .find_map(|part| part.strip_prefix("src=").map(str::to_string))
                        .unwrap_or_default();
                    state.run_label = label_value(rest, "org.followthrough.run_id=")
                        .unwrap_or_default()
                        .trim_start_matches("org.followthrough.run_id=")
                        .to_string();
                    state.owner_label = label_value(rest, "org.followthrough.owner_token=")
                        .unwrap_or_default()
                        .trim_start_matches("org.followthrough.owner_token=")
                        .to_string();
                    let id = "a".repeat(64);
                    if self.scenario.create_timeout {
                        if self.scenario.ambiguous_owned {
                            state.late_id = Some(id);
                        } else if self.scenario.ambiguous_unrelated {
                            state.late_id = Some(FOREIGN_ID.to_string());
                        }
                        return CommandOutcome {
                            exit_code: Some(1),
                            stderr: name_conflict_stderr(&state.name),
                            ..timed_out()
                        };
                    }
                    if self.scenario.create_malformed {
                        return outcome(0, b"not-a-container-id");
                    }
                    if self.scenario.create_nonzero_malformed {
                        state.late_id = Some(id.clone());
                        return outcome(1, b"unexpected create artifact already exists");
                    }
                    state.id = Some(id.clone());
                    let mut result = outcome(0, id.as_bytes());
                    if self.scenario.create_capture_failure {
                        result.stderr.read_failed = true;
                        result.issue = Some("capture_read_failed".into());
                    }
                    result
                }
                Some("inspect") => {
                    state.inspect_calls += 1;
                    if self.scenario.post_inspect_timeout
                        && state.started
                        && state.inspect_calls == 2
                    {
                        return timed_out();
                    }
                    let id = rest.get(4).cloned().unwrap_or_default();
                    if self.scenario.drift_on_inspect && !state.drift_done && state.inspect_calls == 1
                    {
                        state.drift_done = true;
                        let _ = fs::write(
                            self.workspace.join("processor.py"),
                            b"# changed inert candidate\n",
                        );
                    }
                    let cleanup_identity_mismatch = self.scenario.cleanup_identity_mismatch
                        && ((state.id.is_some() && state.inspect_calls >= 3)
                            || (state.id.is_none() && state.inspect_calls >= 1));
                    let foreign = self.scenario.ambiguous_unrelated && id == FOREIGN_ID;
                    let mut result = json_outcome(&self.inspect_json(
                        &state,
                        &id,
                        cleanup_identity_mismatch,
                        foreign,
                    ));
                    if self.scenario.post_capture_failure
                        && state.started
                        && state.inspect_calls == 2
                    {
                        result.stderr.read_failed = true;
                        result.issue = Some("capture_read_failed".into());
                    }
                    result
                }
                Some("start") => {
                    if self.scenario.start_timeout {
                        return timed_out();
                    }
                    if self.scenario.start_overflow {
                        return output_limit();
                    }
                    if self.scenario.start_cancelled {
                        return cancelled();
                    }
                    if self.scenario.plant_receipt {
                        let _ = fs::write(self.output.join("receipt.json"), b"planted");
                    }
                    state.started = true;
                    outcome(
                        self.scenario.attach_exit,
                        b"public checker synthetic output\n",
                    )
                }
                Some("rm") => {
                    let id = rest.get(3).cloned().unwrap_or_default();
                    state.rm_ids.push(id.clone());
                    if self.scenario.cleanup_rm_timeout {
                        return timed_out();
                    }
                    if self.scenario.cleanup_rm_fail {
                        return outcome(1, b"rm failed");
                    }
                    if Some(id.clone()) == state.id || Some(id) == state.late_id {
                        state.removed = true;
                        outcome(0, b"")
                    } else {
                        outcome(1, b"unowned id")
                    }
                }
                Some("ls") => {
                    if self.scenario.cleanup_list_fail {
                        return outcome(1, b"ls failed");
                    }
                    if self.scenario.cleanup_list_orphan {
                        return outcome(0, FOREIGN_ID.as_bytes());
                    }
                    if state.removed {
                        return outcome(0, b"");
                    }
                    let body = state.id.as_deref().or(state.late_id.as_deref()).unwrap_or("");
                    if !body.is_empty() && !rest.iter().any(|arg| arg == "--no-trunc") {
                        return outcome(0, body[..12.min(body.len())].as_bytes());
                    }
                    outcome(0, body.as_bytes())
                }
                _ => outcome(2, b"unknown container command"),
            },
            _ => outcome(2, b"unknown command"),
        }
    }
}

impl FakeDocker {
    fn inspect_json(
        &self,
        state: &FakeState,
        id: &str,
        cleanup_identity_mismatch: bool,
        foreign: bool,
    ) -> Value {
        let owner = if cleanup_identity_mismatch
            || foreign
            || (self.scenario.inspect_mismatch == Some("token") && state.inspect_calls == 1)
        {
            "different-owner".to_string()
        } else {
            state.owner_label.clone()
        };
        let source = if self.scenario.inspect_mismatch == Some("mount") {
            "C:\\different\\workspace".to_string()
        } else {
            state.mount.clone()
        };
        let readonly = self.scenario.inspect_mismatch != Some("security");
        let inspected_image = if self.scenario.inspect_mismatch == Some("image") {
            "sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
        } else {
            IMAGE_ID
        };
        let (status, running, started_at, finished_at, exit_code, oom, error) =
            if !state.started {
                (
                    "created",
                    false,
                    "0001-01-01T00:00:00Z",
                    "0001-01-01T00:00:00Z",
                    0,
                    false,
                    "",
                )
            } else {
                match self.scenario.post {
                    PostState::Exited => (
                        "exited",
                        false,
                        "2026-11-07T00:00:00Z",
                        "2026-11-07T00:00:01Z",
                        self.scenario.attach_exit,
                        false,
                        "",
                    ),
                    PostState::Created => (
                        "created",
                        false,
                        "0001-01-01T00:00:00Z",
                        "0001-01-01T00:00:00Z",
                        0,
                        false,
                        "",
                    ),
                    PostState::Dead => (
                        "dead",
                        false,
                        "2026-11-07T00:00:00Z",
                        "0001-01-01T00:00:00Z",
                        137,
                        false,
                        "",
                    ),
                    PostState::Oom => (
                        "exited",
                        false,
                        "2026-11-07T00:00:00Z",
                        "2026-11-07T00:00:01Z",
                        137,
                        true,
                        "",
                    ),
                    PostState::ErrorState => (
                        "exited",
                        false,
                        "2026-11-07T00:00:00Z",
                        "2026-11-07T00:00:01Z",
                        1,
                        false,
                        "synthetic error",
                    ),
                    PostState::MissingTimes => (
                        "exited",
                        false,
                        "0001-01-01T00:00:00Z",
                        "0001-01-01T00:00:00Z",
                        0,
                        false,
                        "",
                    ),
                    PostState::DefaultZero => (
                        "created",
                        false,
                        "0001-01-01T00:00:00Z",
                        "0001-01-01T00:00:00Z",
                        0,
                        false,
                        "",
                    ),
                }
            };
        let display_name = if cleanup_identity_mismatch || foreign {
            "/unrelated-container"
        } else {
            ""
        };
        let name = if display_name.is_empty() {
            format!("/{}", state.name)
        } else {
            display_name.to_string()
        };
        let mut value = json!({
            "Id": id,
            "Name": name,
            "Image": inspected_image,
            "Config": {
                "Image": IMAGE,
                "User": "65534:65534",
                "Entrypoint": ["python"],
                "Cmd": ["-I", "-B", "/workspace/public_checks.py"],
                "WorkingDir": "/workspace",
                "Labels": {
                    "org.followthrough.run_id": state.run_label,
                    "org.followthrough.owner_token": owner,
                },
            },
            "HostConfig": {
                "ReadonlyRootfs": readonly,
                "NetworkMode": "none",
                "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges"],
                "PidsLimit": 64,
                "Memory": 268435456,
                "NanoCpus": 1000000000,
                "Tmpfs": {"/tmp": "rw,noexec,nosuid,size=16m"},
                "LogConfig": {"Type": "none"},
                "AutoRemove": false,
                "Privileged": false,
                "CapAdd": [],
                "Devices": [],
                "ExtraHosts": null,
                "Binds": null,
                "PidMode": "",
                "UTSMode": "",
                "UsernsMode": "",
                "IpcMode": "private",
            },
            "Mounts": [{
                "Type": "bind",
                "Source": source,
                "Destination": "/workspace",
                "RW": false,
            }],
            "State": {
                "Status": status,
                "Running": running,
                "StartedAt": started_at,
                "FinishedAt": finished_at,
                "ExitCode": exit_code,
                "OOMKilled": oom,
                "Error": error,
            },
        });
        if state.inspect_calls == 1 {
            let host = value["HostConfig"].as_object_mut().unwrap();
            if self.scenario.security_disabled {
                host.insert(
                    "SecurityOpt".into(),
                    json!(["no-new-privileges:false", "seccomp=unconfined"]),
                );
            }
            if self.scenario.privileged_missing {
                host.remove("Privileged");
            }
            if self.scenario.extra_tmpfs {
                host["Tmpfs"]["/extra"] = json!("rw");
            }
            if self.scenario.command_nonstring {
                value["Config"]["Cmd"] =
                    json!(["-I", 17, "-B", "/workspace/public_checks.py"]);
            }
        }
        if state.started && state.inspect_calls == 2 {
            let state_obj = value["State"].as_object_mut().unwrap();
            if self.scenario.post_oom_missing {
                state_obj.remove("OOMKilled");
            }
            if self.scenario.post_error_missing {
                state_obj.remove("Error");
            }
            if self.scenario.post_bad_times {
                state_obj.insert("StartedAt".into(), json!("this-is-a-bad-T-timestamp"));
                state_obj.insert("FinishedAt".into(), json!("this-is-a-bad-T-timestamp"));
            }
        }
        value
    }
}

fn run_scenario(fixture: &Fixture, scenario: Scenario) -> (container_exec::Execution, FakeDocker) {
    let fake = FakeDocker::new(&fixture.workspace, &fixture.output, scenario);
    let execution = execute(&fixture.config(), &fake).expect("executor should produce receipt");
    (execution, fake)
}

fn read_receipt(execution: &container_exec::Execution) -> Value {
    serde_json::from_slice(&fs::read(&execution.receipt_path).unwrap()).unwrap()
}

#[test]
fn successful_lifecycle_is_bounded_owned_and_labeled_synthetic() {
    let fixture = Fixture::new("success");
    let (execution, fake) = run_scenario(&fixture, Scenario::default());
    assert_eq!(execution.status, Status::CheckPassed);
    assert_eq!(execution.exit_code(), 0);
    let receipt = read_receipt(&execution);
    assert_eq!(receipt["schema_version"], "container-check-0.1");
    assert_eq!(receipt["evidence_kind"], "synthetic_executor_control");
    assert_eq!(receipt["status"], "check_passed");
    assert_eq!(receipt["public_check_exit"], 0);
    assert_eq!(receipt["cleanup"]["state"], "settled");
    assert_eq!(receipt["cleanup"]["removal_verified"], true);
    assert_eq!(receipt["workspace"]["path"], fake.state().mount);
    assert_eq!(
        receipt["workspace"]["candidate_sha256"],
        sha256_hex(b"# inert candidate fixture; never executed\n")
    );
    assert!(receipt["limitations"]
        .as_array()
        .unwrap()
        .iter()
        .any(|value| value == "synthetic_command_boundary_not_real_docker"));
    let commands = receipt["commands"].as_array().unwrap();
    let names: Vec<&str> = commands
        .iter()
        .map(|command| command["name"].as_str().unwrap())
        .collect();
    assert_eq!(
        names,
        [
            "info",
            "image_inspect",
            "create",
            "inspect_created",
            "start_attach",
            "inspect_post_attach",
            "cleanup_inspect",
            "cleanup_rm",
            "cleanup_list",
        ]
    );
    let create_argv = fake.state().create_argv.clone();
    assert!(!create_argv.iter().any(|arg| arg == "--rm"));
    assert!(create_argv.iter().any(|arg| arg == "--log-driver=none"));
    assert!(create_argv
        .iter()
        .any(|arg| arg.starts_with("org.followthrough.owner_token=")));
    assert!(create_argv.iter().any(|arg| arg == "--network=none"));
    assert!(create_argv.iter().any(|arg| arg == "--read-only"));
    assert!(!create_argv.iter().any(|arg| arg == "--env"));
    assert!(!create_argv.iter().any(|arg| arg == "--volume"));
    let start = commands
        .iter()
        .find(|command| command["name"] == "start_attach")
        .unwrap();
    assert!(start["argv"]
        .as_array()
        .unwrap()
        .iter()
        .any(|arg| arg == "--attach"));
    assert!(start["argv"].as_array().unwrap().iter().any(|arg| arg == &"a".repeat(64)));
    assert_eq!(receipt["image"]["reference"], IMAGE);
    assert_eq!(receipt["image"]["observed_id"], IMAGE_ID);
    assert_eq!(receipt["image"]["daemon_architecture"], "amd64");
    assert_eq!(receipt["image"]["image_architecture"], "amd64");
    assert_eq!(receipt["container"]["owner_token"], fake.state().owner_label);
    assert!(fixture.output.join("stdout.txt").exists());
    assert!(fixture.output.join("stderr.txt").exists());
    assert!(fs::read_to_string(fixture.output.join("stdout.txt"))
        .unwrap()
        .contains("public checker synthetic output"));
    let requests = fake.state().requests.clone();
    for request in requests {
        assert_eq!(request.args[0], "--host");
        assert_eq!(request.args[1], DOCKER_ENDPOINT);
        for (key, value) in &request.env {
            assert!(
                matches!(
                    key.as_str(),
                    "PATH" | "SystemRoot" | "WINDIR" | "TEMP" | "TMP" | "TMPDIR" | "DOCKER_CONFIG"
                ),
                "unexpected env key {key}"
            );
            assert!(!key.contains("TOKEN"));
            assert!(!key.contains("PROXY"));
            assert!(!key.contains("HOME"));
            if key == "DOCKER_CONFIG" {
                assert!(!Path::new(value).starts_with(&fixture.workspace));
            }
        }
        assert!(!request.env.is_empty());
    }
}

#[test]
fn nonzero_check_is_check_failed_and_retains_observed_exit() {
    let fixture = Fixture::new("nonzero");
    let scenario = Scenario {
        attach_exit: 7,
        ..Scenario::default()
    };
    let (execution, _) = run_scenario(&fixture, scenario);
    assert_eq!(execution.status, Status::CheckFailed);
    assert_eq!(execution.exit_code(), 1);
    let receipt = read_receipt(&execution);
    assert_eq!(receipt["status"], "check_failed");
    assert_eq!(receipt["public_check_exit"], 7);
    assert_eq!(receipt["cleanup"]["state"], "settled");
}

#[test]
fn started_exited_evidence_is_required_for_success() {
    for post in [
        PostState::Created,
        PostState::Dead,
        PostState::Oom,
        PostState::ErrorState,
        PostState::MissingTimes,
        PostState::DefaultZero,
    ] {
        let fixture = Fixture::new("no-proof");
        let scenario = Scenario {
            post,
            ..Scenario::default()
        };
        let (execution, _) = run_scenario(&fixture, scenario);
        assert_eq!(
            execution.status,
            Status::InfrastructureError,
            "post state must not count as success"
        );
        assert_ne!(execution.exit_code(), 0);
        let receipt = read_receipt(&execution);
        assert_eq!(receipt["status"], "infrastructure_error");
        assert_eq!(receipt["cleanup"]["state"], "settled");
    }
}

#[test]
fn image_os_architecture_and_digest_prerequisites_refuse_before_create() {
    for scenario in [
        Scenario {
            daemon_os: "windows",
            ..Scenario::default()
        },
        Scenario {
            image_arch: "arm64",
            ..Scenario::default()
        },
        Scenario {
            image_missing: true,
            ..Scenario::default()
        },
        Scenario {
            image_digest_wrong: true,
            ..Scenario::default()
        },
        Scenario {
            info_fail: true,
            ..Scenario::default()
        },
    ] {
        let fixture = Fixture::new("prereq");
        let (execution, fake) = run_scenario(&fixture, scenario);
        assert_eq!(execution.status, Status::InfrastructureError);
        assert!(!fake
            .state()
            .create_argv
            .iter()
            .any(|arg| arg == "create"));
        let receipt = read_receipt(&execution);
        assert_eq!(receipt["cleanup"]["state"], "settled");
    }
}

#[test]
fn setup_stderr_is_retained_bounded_only_in_private_command_receipt() {
    let fixture = Fixture::new("setup-diagnostics");
    let (execution, fake) = run_scenario(&fixture, Scenario {
        info_fail: true,
        ..Scenario::default()
    });
    assert_eq!(execution.status, Status::InfrastructureError);
    assert!(fake.state().create_argv.is_empty());
    let receipt = read_receipt(&execution);
    let info = receipt["commands"].as_array().unwrap().iter()
        .find(|command| command["name"] == "info").unwrap();
    let excerpt = info["stderr_excerpt"].as_str().unwrap();
    assert!(excerpt.starts_with("private setup diagnostic;"));
    assert_eq!(excerpt.len(), 4096);
    assert!(info["stderr_bytes"].as_u64().unwrap() > 4096);
    assert_eq!(info["exit_code"], 1);
    assert_eq!(info["direct_child_reaped"], true);
    assert_eq!(info["reader_settled"], true);
    assert_eq!(receipt["cleanup"]["state"], "settled");
    assert!(receipt["stderr_file"].is_null());
    assert!(!fixture.output.join("stderr.txt").exists());
}

#[test]
fn prestart_inspection_mismatches_refuse_start_and_cleanup_owned_container() {
    for mismatch in ["token", "mount", "security", "image"] {
        let fixture = Fixture::new("inspect-mismatch");
        let scenario = Scenario {
            inspect_mismatch: Some(mismatch),
            ..Scenario::default()
        };
        let (execution, fake) = run_scenario(&fixture, scenario);
        assert_eq!(execution.status, Status::Refused, "{mismatch}");
        assert!(!fake
            .state()
            .requests
            .iter()
            .any(|request| request.args.iter().any(|arg| arg == "start")));
        assert!(fake.state().rm_ids.contains(&"a".repeat(64)));
        assert_eq!(read_receipt(&execution)["cleanup"]["state"], "settled");
    }
}

#[test]
fn prestart_source_drift_refuses_start() {
    let fixture = Fixture::new("source-drift");
    let scenario = Scenario {
        drift_on_inspect: true,
        ..Scenario::default()
    };
    let (execution, fake) = run_scenario(&fixture, scenario);
    assert_eq!(execution.status, Status::Refused);
    assert!(!fake
        .state()
        .requests
        .iter()
        .any(|request| request.args.iter().any(|arg| arg == "start")));
    assert_eq!(read_receipt(&execution)["diagnostic"], "workspace_changed_before_start");
}

#[test]
fn create_collision_is_failure_without_removing_existing_name() {
    let fixture = Fixture::new("collision");
    let scenario = Scenario {
        create_collision: true,
        ..Scenario::default()
    };
    let (execution, fake) = run_scenario(&fixture, scenario);
    assert_eq!(execution.status, Status::InfrastructureError);
    let state = fake.state();
    assert!(state.rm_ids.is_empty());
    assert!(!state.started);
    assert!(!state
        .requests
        .iter()
        .any(|request| request.args.get(3).map(String::as_str) == Some("ls")));
    let receipt = read_receipt(&execution);
    assert_eq!(receipt["diagnostic"], "create_collision");
    assert_eq!(receipt["cleanup"]["method"], "not_created");
}

#[test]
fn ambiguous_create_with_owned_match_is_removed_but_never_succeeds() {
    let fixture = Fixture::new("ambiguous-owned");
    let scenario = Scenario {
        create_timeout: true,
        ambiguous_owned: true,
        ..Scenario::default()
    };
    let (execution, fake) = run_scenario(&fixture, scenario);
    assert_eq!(execution.status, Status::InfrastructureError);
    let receipt = read_receipt(&execution);
    assert_eq!(receipt["cleanup"]["state"], "settled");
    assert_eq!(receipt["cleanup"]["method"], "rm_force_ambiguous");
    assert!(fake.state().rm_ids.contains(&"a".repeat(64)));
    assert!(!fake.state().requests.iter().any(|request| request
        .args
        .iter()
        .any(|arg| arg == "start")));
}

#[test]
fn ambiguous_create_empty_or_unrelated_listing_stays_unknown() {
    for scenario in [
        Scenario {
            create_timeout: true,
            ..Scenario::default()
        },
        Scenario {
            create_timeout: true,
            ambiguous_unrelated: true,
            ..Scenario::default()
        },
        Scenario {
            create_malformed: true,
            ..Scenario::default()
        },
    ] {
        let fixture = Fixture::new("ambiguous-unknown");
        let (execution, fake) = run_scenario(&fixture, scenario);
        assert_eq!(execution.status, Status::InfrastructureError);
        let receipt = read_receipt(&execution);
        assert_eq!(receipt["cleanup"]["state"], "unknown");
        assert!(fake.state().rm_ids.is_empty());
    }
}

#[test]
fn cleanup_failure_or_absence_mismatch_overrides_success_with_unknown() {
    for scenario in [
        Scenario {
            cleanup_rm_fail: true,
            ..Scenario::default()
        },
        Scenario {
            cleanup_rm_timeout: true,
            ..Scenario::default()
        },
        Scenario {
            cleanup_list_fail: true,
            ..Scenario::default()
        },
        Scenario {
            cleanup_list_orphan: true,
            ..Scenario::default()
        },
        Scenario {
            cleanup_identity_mismatch: true,
            ..Scenario::default()
        },
    ] {
        let fixture = Fixture::new("cleanup-failure");
        let (execution, _) = run_scenario(&fixture, scenario);
        assert_eq!(execution.status, Status::InfrastructureError);
        let receipt = read_receipt(&execution);
        assert_eq!(receipt["cleanup"]["state"], "unknown");
        assert_eq!(receipt["public_check_exit"], 0);
        assert!(!receipt["cleanup"]["removal_verified"].as_bool().unwrap());
    }
}

#[test]
fn post_attach_inspection_timeout_is_infrastructure_with_settled_cleanup() {
    let fixture = Fixture::new("post-timeout");
    let scenario = Scenario {
        post_inspect_timeout: true,
        ..Scenario::default()
    };
    let (execution, _) = run_scenario(&fixture, scenario);
    assert_eq!(execution.status, Status::InfrastructureError);
    let receipt = read_receipt(&execution);
    assert_eq!(receipt["cleanup"]["state"], "settled");
    let post = receipt["commands"]
        .as_array()
        .unwrap()
        .iter()
        .find(|command| command["name"] == "inspect_post_attach")
        .unwrap();
    assert_eq!(post["timed_out"], true);
}

#[test]
fn receipt_write_failure_is_reported_and_not_claimed() {
    let fixture = Fixture::new("receipt-failure");
    let scenario = Scenario {
        plant_receipt: true,
        ..Scenario::default()
    };
    let fake = FakeDocker::new(&fixture.workspace, &fixture.output, scenario);
    let error = execute(&fixture.config(), &fake).unwrap_err();
    assert!(
        error.contains("receipt_write_failed"),
        "unexpected error {error}"
    );
    assert_eq!(
        fs::read_to_string(fixture.output.join("receipt.json")).unwrap(),
        "planted"
    );
}

#[test]
fn existing_output_is_refused_before_spawn_and_preserved() {
    let fixture = Fixture::new("existing-output");
    fs::create_dir(&fixture.output).unwrap();
    fs::write(fixture.output.join("sentinel"), b"preserve").unwrap();
    let fake = FakeDocker::new(&fixture.workspace, &fixture.output, Scenario::default());
    let error = execute(&fixture.config(), &fake).unwrap_err();
    assert!(error.contains("output_exists"), "unexpected error {error}");
    assert_eq!(
        fs::read_to_string(fixture.output.join("sentinel")).unwrap(),
        "preserve"
    );
    assert!(fake.state().requests.is_empty());
}

#[test]
fn attach_timeout_overflow_and_cancellation_keep_category_with_settled_cleanup() {
    for (scenario, expected) in [
        (
            Scenario {
                start_timeout: true,
                post: PostState::Created,
                ..Scenario::default()
            },
            Status::Timeout,
        ),
        (
            Scenario {
                start_overflow: true,
                post: PostState::Created,
                ..Scenario::default()
            },
            Status::OutputLimit,
        ),
        (
            Scenario {
                start_cancelled: true,
                post: PostState::Created,
                ..Scenario::default()
            },
            Status::Cancelled,
        ),
    ] {
        let fixture = Fixture::new("attach-boundaries");
        let (execution, _) = run_scenario(&fixture, scenario);
        assert_eq!(execution.status, expected);
        let receipt = read_receipt(&execution);
        assert_eq!(receipt["cleanup"]["state"], "settled");
    }
}

#[test]
fn validation_refuses_gate_output_and_workspace_contained_docker_without_receipt() {
    let fixture = Fixture::new("validation");
    let mut config = fixture.config();
    config.execute = false;
    assert!(execute(&config, &FakeDocker::new(&fixture.workspace, &fixture.output, Scenario::default())).is_err());
    assert!(!fixture.output.exists());

    let mut config = fixture.config();
    config.workspace = PathBuf::from("relative workspace");
    assert!(execute(&config, &FakeDocker::new(&fixture.workspace, &fixture.output, Scenario::default())).is_err());
    assert!(!fixture.output.exists());

    let mut config = fixture.config();
    config.output_dir = fixture.workspace.join("inside");
    assert!(execute(&config, &FakeDocker::new(&fixture.workspace, &fixture.output, Scenario::default())).is_err());
    assert!(!fixture.workspace.join("inside").exists());

    let mut config = fixture.config();
    config.docker = fixture.workspace.join("docker.bin");
    fs::write(&config.docker, b"fake").unwrap();
    assert!(execute(&config, &FakeDocker::new(&fixture.workspace, &fixture.output, Scenario::default())).is_err());
    assert!(!fixture.output.exists());
}

#[test]
fn cli_refuses_without_execute_and_never_creates_output() {
    let fixture = Fixture::new("cli-refusal");
    let binary = env!("CARGO_BIN_EXE_container-check");
    let output = Command::new(binary)
        .args([
            "--workspace",
            fixture.workspace.to_str().unwrap(),
            "--run-id",
            "cli-run",
            "--output-dir",
            fixture.output.to_str().unwrap(),
            "--docker",
            fixture.docker.to_str().unwrap(),
        ])
        .output()
        .unwrap();
    assert_eq!(output.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&output.stderr).contains("execute_required"));
    assert!(!fixture.output.exists());

    let missing_docker = fixture.root.join("missing-docker.exe");
    let output = Command::new(binary)
        .arg("--workspace")
        .arg(&fixture.workspace)
        .arg("--run-id")
        .arg("cli-run")
        .arg("--output-dir")
        .arg(&fixture.output)
        .arg("--docker")
        .arg(&missing_docker)
        .arg("--execute")
        .output()
        .unwrap();
    assert_eq!(output.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&output.stderr).contains("invalid_docker"));
    assert!(!fixture.output.exists());
}

#[test]
fn ambiguous_listing_uses_no_trunc_and_default_width_would_be_unknown() {
    let fixture = Fixture::new("no-trunc");
    let (execution, fake) = run_scenario(
        &fixture,
        Scenario {
            create_timeout: true,
            ambiguous_owned: true,
            ..Scenario::default()
        },
    );
    assert_eq!(execution.status, Status::InfrastructureError);
    assert_eq!(read_receipt(&execution)["cleanup"]["state"], "settled");
    let requests = fake.state().requests.clone();
    let listing = requests
        .iter()
        .find(|request| request.args.get(3) == Some(&"ls".to_string()))
        .expect("listing requested");
    assert!(listing.args.iter().any(|arg| arg == "--no-trunc"));

    struct StripNoTrunc<'a>(&'a FakeDocker);
    impl CommandRunner for StripNoTrunc<'_> {
        fn run(&self, request: &CommandRequest) -> CommandOutcome {
            let mut request = request.clone();
            if request.args.get(3) == Some(&"ls".to_string()) {
                request.args.retain(|arg| arg != "--no-trunc");
            }
            self.0.run(&request)
        }
    }
    let fixture = Fixture::new("no-trunc-stripped");
    let fake = FakeDocker::new(
        &fixture.workspace,
        &fixture.output,
        Scenario {
            create_timeout: true,
            ambiguous_owned: true,
            ..Scenario::default()
        },
    );
    let execution = execute(&fixture.config(), &StripNoTrunc(&fake)).unwrap();
    let receipt = read_receipt(&execution);
    assert_eq!(receipt["cleanup"]["state"], "unknown");
    assert!(fake.state().rm_ids.is_empty());
}

#[test]
fn security_control_variants_refuse_before_start_and_cleanup_owned() {
    for field in ["security", "privileged", "tmpfs", "command"] {
        let mut scenario = Scenario::default();
        match field {
            "security" => scenario.security_disabled = true,
            "privileged" => scenario.privileged_missing = true,
            "tmpfs" => scenario.extra_tmpfs = true,
            "command" => scenario.command_nonstring = true,
            _ => unreachable!(),
        }
        let fixture = Fixture::new("unsafe-control");
        let (execution, fake) = run_scenario(&fixture, scenario);
        assert_eq!(execution.status, Status::Refused, "{field}");
        assert!(!fake.state().started, "{field} started");
        assert!(
            fake.state().rm_ids.contains(&"a".repeat(64)),
            "{field} did not clean up owned container"
        );
        assert_eq!(
            read_receipt(&execution)["cleanup"]["state"],
            "settled",
            "{field}"
        );
    }
}

#[test]
fn post_attach_incomplete_or_capture_failed_evidence_cannot_pass() {
    for field in ["oom", "error", "timestamps", "capture"] {
        let mut scenario = Scenario::default();
        match field {
            "oom" => scenario.post_oom_missing = true,
            "error" => scenario.post_error_missing = true,
            "timestamps" => scenario.post_bad_times = true,
            "capture" => scenario.post_capture_failure = true,
            _ => unreachable!(),
        }
        let fixture = Fixture::new("bad-start-evidence");
        let (execution, _) = run_scenario(&fixture, scenario);
        let receipt = read_receipt(&execution);
        assert_eq!(execution.status, Status::InfrastructureError, "{field}");
        assert!(receipt["public_check_exit"].is_null(), "{field}");
        assert_eq!(receipt["cleanup"]["state"], "settled", "{field}");
    }
}

#[test]
fn nonzero_malformed_create_queries_and_cleans_owned_container() {
    let fixture = Fixture::new("nonzero-malformed");
    let scenario = Scenario {
        create_nonzero_malformed: true,
        ..Scenario::default()
    };
    let (execution, fake) = run_scenario(&fixture, scenario);
    let receipt = read_receipt(&execution);
    assert_eq!(execution.status, Status::InfrastructureError);
    assert_eq!(receipt["cleanup"]["state"], "settled");
    assert_eq!(receipt["cleanup"]["method"], "rm_force_ambiguous");
    assert!(fake.state().rm_ids.contains(&"a".repeat(64)));
    assert!(!fake.state().started);
}

#[test]
fn create_with_full_id_and_failed_capture_never_starts_and_reconciles_owned_id() {
    let fixture = Fixture::new("create-capture-failed");
    let scenario = Scenario {
        create_capture_failure: true,
        ..Scenario::default()
    };
    let (execution, fake) = run_scenario(&fixture, scenario);
    assert_eq!(execution.status, Status::InfrastructureError);
    let receipt = read_receipt(&execution);
    assert_eq!(receipt["cleanup"]["method"], "rm_force_ambiguous");
    assert_eq!(receipt["cleanup"]["removal_verified"], true);
    let state = fake.state();
    assert!(!state.started);
    assert_eq!(state.rm_ids, vec!["a".repeat(64)]);
}

fn helper_request(program: PathBuf, args: Vec<String>, timeout: Duration, cap: usize) -> CommandRequest {
    CommandRequest {
        program,
        args,
        env: Vec::new(),
        timeout,
        stream_limit: cap,
        ignore_cancellation: true,
        cancellation: Arc::new(AtomicBool::new(false)),
    }
}

#[cfg(windows)]
#[test]
fn stderr_overflow_after_stdout_eof_is_detected_on_either_stream() {
    let helper = fake_executable();
    let outcome = ProcessRunner.run(&helper_request(
        helper,
        vec!["stderr-overflow".into()],
        Duration::from_secs(2),
        1024,
    ));
    assert!(outcome.direct_child_reaped);
    assert_eq!(outcome.stderr.observed_bytes, 1025);
    assert_eq!(outcome.stderr.bytes.len(), 1024);
    assert!(outcome.output_limit);
    assert!(!outcome.timed_out);
}

#[cfg(windows)]
#[test]
fn cleanup_allowance_bounds_process_and_reader_settlement() {
    let helper = fake_executable();
    let fixture = Fixture::new("cleanup-deadline");
    let fake = FakeDocker::new(&fixture.workspace, &fixture.output, Scenario::default());
    struct PipeCleanup<'a>(&'a FakeDocker, PathBuf);
    impl CommandRunner for PipeCleanup<'_> {
        fn run(&self, request: &CommandRequest) -> CommandOutcome {
            if request.ignore_cancellation && request.args.get(3) == Some(&"inspect".to_string()) {
                let mut redirected = request.clone();
                redirected.program = self.1.clone();
                redirected.args = vec!["pipe-parent".into()];
                redirected.env.clear();
                ProcessRunner.run(&redirected)
            } else {
                self.0.run(request)
            }
        }
    }
    let mut config = fixture.config();
    config.deadlines.cleanup_total = Duration::from_millis(100);
    let started = std::time::Instant::now();
    let execution = execute(&config, &PipeCleanup(&fake, helper)).unwrap();
    let elapsed = started.elapsed().as_millis();
    let receipt = read_receipt(&execution);
    assert_eq!(execution.status, Status::InfrastructureError);
    assert_eq!(receipt["cleanup"]["state"], "unknown");
    let cleanup = receipt["commands"]
        .as_array()
        .unwrap()
        .iter()
        .find(|command| command["name"] == "cleanup_inspect")
        .unwrap();
    assert!(
        cleanup["elapsed_ms"].as_u64().unwrap() < 250,
        "cleanup command exceeded its allowance under parallel harness load: {}",
        cleanup["elapsed_ms"]
    );
    assert!(cleanup["direct_child_reaped"].is_boolean());
    assert!(cleanup["reader_settled"].is_boolean());
    assert!(elapsed < 600, "lifecycle exceeded bounded cleanup: {elapsed}");
    std::thread::sleep(Duration::from_millis(900));
}

fn fake_executable() -> PathBuf {
    static FAKE: OnceLock<PathBuf> = OnceLock::new();
    FAKE.get_or_init(|| {
        let directory = scratch_root().join("fake-build");
        fs::create_dir_all(&directory).unwrap();
        let output = directory.join("fake-docker.exe");
        let source = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/fake_docker.rs");
        let status = Command::new("rustc")
            .arg("--edition=2021")
            .arg(&source)
            .arg("-o")
            .arg(&output)
            .status()
            .unwrap();
        assert!(status.success(), "fake docker helper must compile");
        output
    })
    .clone()
}

fn fake_copy(mode: &str) -> PathBuf {
    let directory = unique_dir("fake-process");
    let executable = directory.join("docker-fake.exe");
    fs::copy(fake_executable(), &executable).unwrap();
    fs::write(directory.join("mode.txt"), mode).unwrap();
    executable
}

#[test]
fn production_wrapper_bounded_capture_timeout_and_reap_with_spawned_helper() {
    let executable = fake_copy("flood");
    let request = CommandRequest {
        program: executable,
        args: vec!["--host".into(), DOCKER_ENDPOINT.into(), "info".into()],
        env: Vec::new(),
        timeout: std::time::Duration::from_secs(2),
        stream_limit: 1024,
        ignore_cancellation: false,
        cancellation: Arc::new(AtomicBool::new(false)),
    };
    let outcome = ProcessRunner.run(&request);
    assert!(outcome.pid.is_some_and(|pid| pid > 0));
    assert!(outcome.direct_child_reaped);
    assert!(outcome.output_limit);
    assert_eq!(outcome.stdout.observed_bytes, 1025);
    assert!(outcome.stdout.bytes.len() <= 1024);
    assert!(outcome.elapsed_ms < 5000);
}

#[test]
fn executor_lifecycle_with_spawned_sleep_helper_times_out_reaps_and_writes_receipt() {
    let fixture = Fixture::new("spawned-lifecycle");
    let executable = fake_copy("sleep");
    let mut config = fixture.config();
    config.docker = executable;
    config.deadlines = Deadlines {
        setup_total: std::time::Duration::from_secs(1),
        setup_command: std::time::Duration::from_millis(300),
        execution: std::time::Duration::from_secs(1),
        post_attach: std::time::Duration::from_millis(200),
        cleanup_total: std::time::Duration::from_secs(1),
        stream_limit: 4096,
    };
    let execution = execute(&config, &ProcessRunner).expect("receipt should be written");
    assert_eq!(execution.status, Status::InfrastructureError);
    let receipt = read_receipt(&execution);
    assert_eq!(receipt["evidence_kind"], "synthetic_executor_control");
    assert_eq!(receipt["status"], "infrastructure_error");
    let commands = receipt["commands"].as_array().unwrap();
    let info = commands.iter().find(|command| command["name"] == "info").unwrap();
    assert_eq!(info["timed_out"], true);
    assert!(info["pid"].as_u64().is_some_and(|pid| pid > 0));
    assert_eq!(info["direct_child_reaped"], true);
    assert_eq!(receipt["cleanup"]["state"], "settled");
    assert_eq!(receipt["cleanup"]["method"], "not_created");
}

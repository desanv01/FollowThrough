//! Focused source-plan tests for P2-ISOLATION-PLAN/r2.
//!
//! These tests exercise only the inert planner and the plan-writing CLI. They
//! never execute Docker, a container, a shell, or candidate Python. Synthetic
//! workspace files contain inert text.

#[path = "../src/isolation_plan.rs"]
mod isolation_plan;

use isolation_plan::{
    build_plan, validate_mount_path, validate_run_id, FileRole, CANDIDATE_FILES,
    CLEANUP_DEADLINE_SECONDS, EVIDENCE_KIND, IMAGE, KIND, MAX_RUN_ID_BYTES,
    PER_FILE_LIMIT_BYTES, PUBLIC_CHECKS, RUNTIME_SECONDS, SCHEMA_VERSION, STREAM_LIMIT_BYTES,
    TOTAL_LIMIT_BYTES,
};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::{
    env, fs,
    path::{Path, PathBuf},
    process::{Command, Output},
    sync::atomic::{AtomicUsize, Ordering},
};

const INERT_CANDIDATE: &[u8] = b"# inert synthetic processor fixture; never executed\n";
const INERT_PREVIEW: &[u8] = b"# inert synthetic preview fixture; never executed\n";
const INERT_CHECKS: &[u8] = b"# inert synthetic public checks; never executed\n";
const INERT_TASK: &[u8] = b"# inert synthetic task description\n";
const INERT_SPEC: &[u8] = b"# inert synthetic specification\n";
const INERT_WORKFLOW: &[u8] = b"# inert synthetic workflow\n";
const INERT_HISTORY: &[u8] = b"[]\n";

static COUNTER: AtomicUsize = AtomicUsize::new(0);

fn scratch_root() -> PathBuf {
    let base = Path::new(env!("CARGO_MANIFEST_DIR")).join("../tmp/tests");
    fs::create_dir_all(&base).unwrap();
    fs::canonicalize(base).unwrap()
}

fn unique_dir(label: &str) -> PathBuf {
    let serial = COUNTER.fetch_add(1, Ordering::Relaxed);
    let path = scratch_root().join(format!("{label}-{}-{serial}", std::process::id()));
    fs::create_dir(&path).unwrap();
    path
}

fn retry_files() -> Vec<(String, Vec<u8>)> {
    vec![
        ("processor.py".to_string(), INERT_CANDIDATE.to_vec()),
        (PUBLIC_CHECKS.to_string(), INERT_CHECKS.to_vec()),
        ("TASK.md".to_string(), INERT_TASK.to_vec()),
    ]
}

fn cache_files() -> Vec<(String, Vec<u8>)> {
    vec![
        ("preview.py".to_string(), INERT_PREVIEW.to_vec()),
        (PUBLIC_CHECKS.to_string(), INERT_CHECKS.to_vec()),
        ("SPEC.md".to_string(), INERT_SPEC.to_vec()),
        ("WORKFLOW.md".to_string(), INERT_WORKFLOW.to_vec()),
        ("HISTORY.json".to_string(), INERT_HISTORY.to_vec()),
    ]
}

fn create_workspace(label: &str, files: &[(String, Vec<u8>)]) -> PathBuf {
    let workspace = unique_dir(label).join("workspace");
    fs::create_dir(&workspace).unwrap();
    write_files(&workspace, files);
    workspace
}

fn write_files(workspace: &Path, files: &[(String, Vec<u8>)]) {
    for (name, bytes) in files {
        fs::write(workspace.join(name), bytes).unwrap();
    }
}

fn sha256_hex(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn plan_error_code(workspace: &Path, run_id: &str) -> &'static str {
    build_plan(workspace, run_id).unwrap_err().code()
}

fn display_workspace(workspace: &Path) -> String {
    let canonical = fs::canonicalize(workspace).unwrap();
    let text = canonical.to_str().unwrap().to_string();
    #[cfg(windows)]
    {
        if let Some(rest) = text.strip_prefix("\\\\?\\UNC\\") {
            return format!("\\\\{rest}");
        }
        if let Some(rest) = text.strip_prefix("\\\\?\\") {
            return rest.to_string();
        }
    }
    text
}

fn expected_argv(workspace: &str, run_id: &str) -> Vec<String> {
    vec![
        "run".to_string(),
        "--pull=never".to_string(),
        "--rm".to_string(),
        "--name".to_string(),
        format!("ft-check-{run_id}"),
        "--label".to_string(),
        format!("org.followthrough.run_id={run_id}"),
        "--network=none".to_string(),
        "--read-only".to_string(),
        "--cap-drop=ALL".to_string(),
        "--security-opt=no-new-privileges".to_string(),
        "--pids-limit=64".to_string(),
        "--memory=256m".to_string(),
        "--cpus=1".to_string(),
        "--user=65534:65534".to_string(),
        "--tmpfs".to_string(),
        "/tmp:rw,noexec,nosuid,size=16m".to_string(),
        "--workdir=/workspace".to_string(),
        "--mount".to_string(),
        format!("type=bind,src={workspace},dst=/workspace,readonly"),
        "--entrypoint=python".to_string(),
        IMAGE.to_string(),
        "-I".to_string(),
        "-B".to_string(),
        "/workspace/public_checks.py".to_string(),
    ]
}

fn directory_snapshot(directory: &Path) -> Vec<(String, u64, String)> {
    let mut entries: Vec<(String, u64, String)> = fs::read_dir(directory)
        .unwrap()
        .map(|entry| {
            let entry = entry.unwrap();
            let name = entry.file_name().to_str().unwrap().to_string();
            let bytes = fs::read(entry.path()).unwrap();
            (name, bytes.len() as u64, sha256_hex(&bytes))
        })
        .collect();
    entries.sort();
    entries
}

#[test]
fn fixed_plan_has_exact_argv_and_actual_hashes() {
    let workspace = create_workspace("valid retry workspace with spaces", &retry_files());
    let plan = build_plan(&workspace, "run_01-abc").unwrap();

    assert_eq!(plan.schema_version, SCHEMA_VERSION);
    assert_eq!(plan.kind, KIND);
    assert_eq!(plan.evidence_kind, EVIDENCE_KIND);
    assert_eq!(plan.run_id, "run_01-abc");
    assert_eq!(plan.container_name, "ft-check-run_01-abc");
    assert_eq!(plan.image, IMAGE);
    assert_eq!(plan.program, "docker");
    assert_eq!(plan.workspace, display_workspace(&workspace));
    assert!(plan.workspace.contains(' '));
    #[cfg(windows)]
    assert!(plan.workspace.contains(':'));
    #[cfg(windows)]
    assert!(!plan.workspace.starts_with("\\\\?\\"));
    let emitted = Path::new(&plan.workspace);
    assert_eq!(
        fs::canonicalize(emitted).unwrap(),
        fs::canonicalize(&workspace).unwrap(),
        "emitted workspace must resolve to the validated directory"
    );
    assert_eq!(plan.candidate_file, "processor.py");

    let files = &plan.inventory.files;
    assert_eq!(plan.inventory.file_count, 3);
    assert_eq!(
        plan.inventory.total_bytes,
        (INERT_CANDIDATE.len() + INERT_CHECKS.len() + INERT_TASK.len()) as u64
    );
    assert_eq!(plan.inventory.per_file_limit_bytes, PER_FILE_LIMIT_BYTES);
    assert_eq!(plan.inventory.total_limit_bytes, TOTAL_LIMIT_BYTES);
    let names: Vec<&str> = files.iter().map(|file| file.name.as_str()).collect();
    assert_eq!(names, ["TASK.md", "processor.py", "public_checks.py"]);
    for file in files {
        let expected_role = match file.name.as_str() {
            "processor.py" => FileRole::Candidate,
            PUBLIC_CHECKS => FileRole::PublicChecks,
            _ => FileRole::Context,
        };
        assert_eq!(file.role, expected_role);
        let bytes = fs::read(emitted.join(&file.name)).unwrap();
        assert_eq!(file.bytes, bytes.len() as u64);
        assert_eq!(file.sha256, sha256_hex(&bytes));
    }

    assert_eq!(
        plan.argv,
        expected_argv(&plan.workspace, "run_01-abc"),
        "argv must be the exact fixed vector"
    );
    assert_eq!(plan.argv.len(), 25);
    assert_eq!(
        plan.argv.iter().filter(|arg| arg.as_str() == "--mount").count(),
        1
    );
    assert_eq!(
        plan.argv.iter().filter(|arg| arg.as_str() == IMAGE).count(),
        1
    );
    for forbidden in [
        "--env",
        "-e",
        "--volume",
        "-v",
        "--privileged",
        "--add-host",
        "--pid=host",
        "--ipc=host",
        "--uts=host",
        "--userns=host",
    ] {
        assert!(
            !plan.argv.iter().any(|arg| arg == forbidden),
            "unexpected override {forbidden}"
        );
    }
    assert_eq!(
        plan.argv
            .iter()
            .filter(|arg| arg.as_str() == "--entrypoint=python")
            .count(),
        1
    );
    assert_eq!(plan.resources.read_only_root, true);
    assert_eq!(plan.resources.workspace_read_only, true);
    assert_eq!(plan.resources.drop_all_capabilities, true);
    assert_eq!(plan.resources.no_new_privileges, true);
    assert_eq!(plan.resources.network, "none");
    assert_eq!(plan.resources.pids_limit, 64);
    assert_eq!(plan.resources.memory, "256m");
    assert_eq!(plan.resources.cpus, "1");
    assert_eq!(plan.resources.user, "65534:65534");
    assert_eq!(plan.resources.user_uid, 65534);
    assert_eq!(plan.resources.user_gid, 65534);
    assert_eq!(plan.resources.tmpfs, "/tmp:rw,noexec,nosuid,size=16m");
    assert_eq!(plan.resources.pull, "never");
    assert!(plan.resources.remove_after_exit);
    assert_eq!(
        plan.executor_policy.enforcement,
        "declared_policy_only_not_enforced"
    );
    assert_eq!(plan.executor_policy.runtime_seconds, RUNTIME_SECONDS);
    assert_eq!(
        plan.executor_policy.stdout_limit_bytes,
        STREAM_LIMIT_BYTES
    );
    assert_eq!(
        plan.executor_policy.stderr_limit_bytes,
        STREAM_LIMIT_BYTES
    );
    assert_eq!(
        plan.executor_policy.cleanup_deadline_seconds,
        CLEANUP_DEADLINE_SECONDS
    );

    let json = serde_json::to_value(&plan).unwrap();
    assert_eq!(json["schema_version"], SCHEMA_VERSION);
    assert_eq!(json["kind"], KIND);
    assert_eq!(json["evidence_kind"], EVIDENCE_KIND);
    for receipt_field in ["pid", "exit_code", "grade", "execution_success", "executed"] {
        assert!(
            json.get(receipt_field).is_none(),
            "plan must not contain {receipt_field}"
        );
    }
    let text = json.to_string();
    assert!(!text.contains("\"env\""));
    assert!(!text.contains("\"command\""));
}

#[test]
fn stable_hashes_and_planner_does_not_mutate_workspace() {
    let workspace = create_workspace("valid cache workspace stable", &cache_files());
    let before = directory_snapshot(&workspace);
    let first = build_plan(&workspace, "stable-1").unwrap();
    let second = build_plan(&workspace, "stable-1").unwrap();
    let after = directory_snapshot(&workspace);

    assert_eq!(before, after);
    let first_hashes: Vec<(&str, &str)> = first
        .inventory
        .files
        .iter()
        .map(|file| (file.name.as_str(), file.sha256.as_str()))
        .collect();
    let second_hashes: Vec<(&str, &str)> = second
        .inventory
        .files
        .iter()
        .map(|file| (file.name.as_str(), file.sha256.as_str()))
        .collect();
    assert_eq!(first_hashes, second_hashes);
    for file in &first.inventory.files {
        let expected = before
            .iter()
            .find(|entry| entry.0 == file.name)
            .expect("inventory file exists in snapshot");
        assert_eq!(file.sha256, expected.2);
        assert_eq!(file.bytes, expected.1);
    }
}

#[test]
fn preview_candidate_and_optional_context_files_are_supported() {
    let workspace = create_workspace("valid preview workspace", &cache_files());
    let plan = build_plan(&workspace, "preview-run").unwrap();
    assert_eq!(plan.candidate_file, "preview.py");
    assert_eq!(plan.inventory.file_count, 5);
    let names: Vec<&str> = plan
        .inventory
        .files
        .iter()
        .map(|file| file.name.as_str())
        .collect();
    assert_eq!(
        names,
        [
            "HISTORY.json",
            "SPEC.md",
            "WORKFLOW.md",
            "preview.py",
            "public_checks.py"
        ]
    );
    assert!(!plan.argv.iter().any(|arg| arg == "preview.py"));
    assert!(plan
        .inventory
        .files
        .iter()
        .any(|file| file.name == "preview.py" && file.role == FileRole::Candidate));
    for name in ["SPEC.md", "WORKFLOW.md", "HISTORY.json"] {
        assert!(plan.inventory.files.iter().any(|file| file.name == name));
    }
    assert_eq!(CANDIDATE_FILES, ["processor.py", "preview.py"]);
}

#[test]
fn rejects_both_missing_and_incomplete_candidate_sets() {
    let base = unique_dir("candidate sets");

    let mut both = retry_files();
    both.push(("preview.py".to_string(), INERT_PREVIEW.to_vec()));
    let both_workspace = base.join("both");
    fs::create_dir(&both_workspace).unwrap();
    write_files(&both_workspace, &both);
    assert_eq!(plan_error_code(&both_workspace, "run"), "both_candidates");

    let none = vec![(PUBLIC_CHECKS.to_string(), INERT_CHECKS.to_vec())];
    let none_workspace = base.join("none");
    fs::create_dir(&none_workspace).unwrap();
    write_files(&none_workspace, &none);
    assert_eq!(plan_error_code(&none_workspace, "run"), "missing_candidate");

    let no_public = vec![("processor.py".to_string(), INERT_CANDIDATE.to_vec())];
    let no_public_workspace = base.join("no public");
    fs::create_dir(&no_public_workspace).unwrap();
    write_files(&no_public_workspace, &no_public);
    assert_eq!(
        plan_error_code(&no_public_workspace, "run"),
        "missing_public_checks"
    );

    let empty = base.join("empty");
    fs::create_dir(&empty).unwrap();
    assert_eq!(plan_error_code(&empty, "run"), "missing_candidate");
}

#[test]
fn rejects_unexpected_files_directories_and_metadata() {
    let extra_workspace = create_workspace("extra file", &retry_files());
    fs::write(extra_workspace.join("notes.txt"), b"not allowlisted").unwrap();
    assert_eq!(plan_error_code(&extra_workspace, "run"), "unexpected_entry");

    let backup_workspace = create_workspace("backup file", &retry_files());
    fs::write(backup_workspace.join("processor.py.bak"), b"backup").unwrap();
    assert_eq!(plan_error_code(&backup_workspace, "run"), "unexpected_entry");

    let env_workspace = create_workspace("dot env", &retry_files());
    fs::write(env_workspace.join(".env"), b"INERT=1").unwrap();
    assert_eq!(plan_error_code(&env_workspace, "run"), "unexpected_entry");

    let secret_workspace = create_workspace("secret dir", &retry_files());
    fs::create_dir(secret_workspace.join("secrets")).unwrap();
    assert_eq!(
        plan_error_code(&secret_workspace, "run"),
        "unexpected_directory"
    );

    let nested_workspace = create_workspace("nested dir", &retry_files());
    fs::create_dir(nested_workspace.join("nested")).unwrap();
    fs::write(nested_workspace.join("nested/processor.py"), b"nested").unwrap();
    assert_eq!(
        plan_error_code(&nested_workspace, "run"),
        "unexpected_directory"
    );
}

#[test]
fn enforces_per_file_and_total_size_limits_from_actual_bytes() {
    let base = unique_dir("size limits");

    let large = base.join("large file");
    fs::create_dir(&large).unwrap();
    write_files(
        &large,
        &[
            (
                "processor.py".to_string(),
                vec![b'a'; PER_FILE_LIMIT_BYTES as usize + 1],
            ),
            (PUBLIC_CHECKS.to_string(), INERT_CHECKS.to_vec()),
        ],
    );
    assert_eq!(plan_error_code(&large, "run"), "file_size");

    let exact = base.join("exact per file");
    fs::create_dir(&exact).unwrap();
    write_files(
        &exact,
        &[
            (
                "processor.py".to_string(),
                vec![b'b'; PER_FILE_LIMIT_BYTES as usize],
            ),
            (PUBLIC_CHECKS.to_string(), b"#".to_vec()),
        ],
    );
    let plan = build_plan(&exact, "exact").unwrap();
    assert_eq!(plan.inventory.total_bytes, PER_FILE_LIMIT_BYTES + 1);

    let total = base.join("total");
    fs::create_dir(&total).unwrap();
    write_files(
        &total,
        &[
            (
                "processor.py".to_string(),
                vec![b'c'; PER_FILE_LIMIT_BYTES as usize],
            ),
            (
                PUBLIC_CHECKS.to_string(),
                vec![b'd'; PER_FILE_LIMIT_BYTES as usize],
            ),
            ("TASK.md".to_string(), b"e".to_vec()),
        ],
    );
    assert_eq!(plan_error_code(&total, "run"), "total_size");
    assert_eq!(
        (PER_FILE_LIMIT_BYTES * 2 + 1),
        TOTAL_LIMIT_BYTES + 1
    );
}

#[test]
fn validates_run_ids_and_refuses_mount_delimiters() {
    let longest = "x".repeat(MAX_RUN_ID_BYTES);
    for valid in ["a", "A0_-", longest.as_str()] {
        validate_run_id(valid).unwrap();
    }
    let too_long = "x".repeat(MAX_RUN_ID_BYTES + 1);
    for invalid in [
        "".to_string(),
        "bad id".to_string(),
        "bad/id".to_string(),
        "bad.id".to_string(),
        "ümlaut".to_string(),
        "bad\nid".to_string(),
        too_long,
    ] {
        assert_eq!(
            validate_run_id(&invalid).unwrap_err().code(),
            "invalid_run_id",
            "run id {invalid:?}"
        );
    }

    for path in [
        "C:\\valid path",
        "C:\\valid:colon",
        "/tmp/valid path",
        "relative path",
    ] {
        validate_mount_path(path).unwrap();
    }
    for path in [
        "C:\\bad,comma",
        "C:\\bad\nline",
        "C:\\bad\rline",
        "C:\\bad\0nul",
        "",
    ] {
        assert!(
            validate_mount_path(path).is_err(),
            "mount path {path:?} must be refused"
        );
    }

    let base = unique_dir("mount delimiters");
    let comma = base.join("workspace,comma");
    fs::create_dir(&comma).unwrap();
    write_files(&comma, &retry_files());
    assert_eq!(plan_error_code(&comma, "run"), "mount_delimiter");

    for (label, name) in [("newline", "workspace\nline"), ("carriage", "workspace\rline")] {
        let path = base.join(name);
        match fs::create_dir(&path) {
            Ok(()) => {
                write_files(&path, &retry_files());
                assert_eq!(
                    plan_error_code(&path, "run"),
                    "mount_delimiter",
                    "{label} workspace must be refused"
                );
            }
            Err(error) => {
                eprintln!("SKIP {label} workspace creation is not supported: {error}");
            }
        }
    }
}

#[cfg(unix)]
fn make_dir_link(src: &Path, dst: &Path) -> std::io::Result<()> {
    std::os::unix::fs::symlink(src, dst)
}

#[cfg(unix)]
fn make_file_link(src: &Path, dst: &Path) -> std::io::Result<()> {
    std::os::unix::fs::symlink(src, dst)
}

#[cfg(windows)]
fn make_dir_link(src: &Path, dst: &Path) -> std::io::Result<()> {
    std::os::windows::fs::symlink_dir(src, dst)
}

#[cfg(windows)]
fn make_file_link(src: &Path, dst: &Path) -> std::io::Result<()> {
    std::os::windows::fs::symlink_file(src, dst)
}

#[test]
fn rejects_symlink_or_reparse_components_when_supported() {
    let base = unique_dir("links");
    let real = create_workspace("real linked workspace", &retry_files());
    let alias = base.join("alias workspace");

    match make_dir_link(&real, &alias) {
        Ok(()) => {
            assert_eq!(plan_error_code(&alias, "run"), "link_component");
        }
        Err(error) => {
            eprintln!("SKIP directory symlink/reparse creation is not supported: {error}");
        }
    }

    let file_link_workspace = create_workspace("file link workspace", &retry_files());
    let file_alias = file_link_workspace.join("alias.py");
    match make_file_link(&file_link_workspace.join("processor.py"), &file_alias) {
        Ok(()) => {
            assert_eq!(
                plan_error_code(&file_link_workspace, "run"),
                "link_component"
            );
        }
        Err(error) => {
            eprintln!("SKIP file symlink/reparse creation is not supported: {error}");
        }
    }
}

#[test]
fn rejects_relative_and_non_directory_workspaces() {
    let relative = build_plan(Path::new("relative workspace"), "run").unwrap_err();
    assert_eq!(relative.code(), "invalid_workspace");
    assert!(relative.message().contains("absolute"));

    let base = unique_dir("bad workspaces");
    let file = base.join("workspace file");
    fs::write(&file, b"not a directory").unwrap();
    assert_eq!(plan_error_code(&file, "run"), "invalid_workspace");

    let missing = base.join("missing workspace");
    assert_eq!(plan_error_code(&missing, "run"), "invalid_workspace");

    let workspace = create_workspace("run id check", &retry_files());
    assert_eq!(plan_error_code(&workspace, "bad id"), "invalid_run_id");
}

fn cli() -> Command {
    Command::new(env!("CARGO_BIN_EXE_isolation-plan"))
}

fn run_cli(args: &[&std::ffi::OsStr], cwd: &Path) -> Output {
    cli().args(args).current_dir(cwd).output().unwrap()
}

fn as_os(path: &Path) -> &std::ffi::OsStr {
    path.as_os_str()
}

#[test]
fn cli_external_cwd_spaces_writes_new_plan_and_refuses_overwrite() {
    let base = unique_dir("cli spaces");
    let workspace = base.join("work space");
    fs::create_dir(&workspace).unwrap();
    write_files(&workspace, &retry_files());
    let external = base.join("external cwd");
    fs::create_dir(&external).unwrap();
    let plans = external.join("plan outputs");
    fs::create_dir(&plans).unwrap();
    let output = plans.join("plan one.json");

    let args = [
        std::ffi::OsStr::new("--workspace"),
        as_os(&workspace),
        std::ffi::OsStr::new("--run-id"),
        std::ffi::OsStr::new("cli-run-1"),
        std::ffi::OsStr::new("--output"),
        std::ffi::OsStr::new("plan outputs/plan one.json"),
    ];
    let first = run_cli(&args, &external);
    assert_eq!(
        first.status.code(),
        Some(0),
        "{}",
        String::from_utf8_lossy(&first.stderr)
    );
    assert!(String::from_utf8_lossy(&first.stdout).contains("no execution was attempted"));
    let bytes = fs::read(&output).unwrap();
    let json: Value = serde_json::from_slice(&bytes).unwrap();
    assert_eq!(json["schema_version"], SCHEMA_VERSION);
    assert_eq!(json["kind"], KIND);
    assert_eq!(json["evidence_kind"], EVIDENCE_KIND);
    assert_eq!(json["run_id"], "cli-run-1");
    assert_eq!(json["container_name"], "ft-check-cli-run-1");
    assert_eq!(json["workspace"], display_workspace(&workspace));
    assert_eq!(json["argv"].as_array().unwrap().len(), 25);
    assert_eq!(json["argv"][0], "run");
    assert_eq!(json["inventory"]["file_count"], 3);
    assert_eq!(json["executor_policy"]["enforcement"], "declared_policy_only_not_enforced");
    let emitted = Path::new(json["workspace"].as_str().unwrap());
    assert_eq!(
        fs::canonicalize(emitted).unwrap(),
        fs::canonicalize(&workspace).unwrap(),
        "CLI-emitted workspace must resolve to the validated directory"
    );
    let emitted_candidate = fs::read(emitted.join("processor.py")).unwrap();
    let recorded_candidate = json["inventory"]["files"]
        .as_array()
        .unwrap()
        .iter()
        .find(|file| file["name"] == "processor.py")
        .unwrap();
    assert_eq!(recorded_candidate["sha256"], sha256_hex(&emitted_candidate));
    assert_eq!(
        json["argv"][19],
        format!(
            "type=bind,src={},dst=/workspace,readonly",
            json["workspace"].as_str().unwrap()
        )
    );

    let before = fs::read(&output).unwrap();
    let second = run_cli(&args, &external);
    assert_eq!(second.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&second.stderr).contains("output_exists"));
    assert_eq!(fs::read(&output).unwrap(), before);
}

#[test]
fn cli_refuses_output_inside_workspace_and_invalid_input() {
    let base = unique_dir("cli invalid");
    let workspace = base.join("work space");
    fs::create_dir(&workspace).unwrap();
    write_files(&workspace, &retry_files());
    let external = base.join("external");
    fs::create_dir(&external).unwrap();

    let inside = workspace.join("plan.json");
    let inside_args = [
        std::ffi::OsStr::new("--workspace"),
        as_os(&workspace),
        std::ffi::OsStr::new("--run-id"),
        std::ffi::OsStr::new("run"),
        std::ffi::OsStr::new("--output"),
        as_os(&inside),
    ];
    let output = run_cli(&inside_args, &external);
    assert_eq!(output.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&output.stderr).contains("invalid_output"));
    assert!(!inside.exists());

    let bad_id_output = base.join("bad-id.json");
    let bad_id_args = [
        std::ffi::OsStr::new("--workspace"),
        as_os(&workspace),
        std::ffi::OsStr::new("--run-id"),
        std::ffi::OsStr::new("bad id"),
        std::ffi::OsStr::new("--output"),
        as_os(&bad_id_output),
    ];
    let output = run_cli(&bad_id_args, &external);
    assert_eq!(output.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&output.stderr).contains("invalid_run_id"));
    assert!(!bad_id_output.exists());

    let missing_workspace = base.join("missing workspace");
    let missing_output = base.join("missing-workspace.json");
    let missing_args = [
        std::ffi::OsStr::new("--workspace"),
        as_os(&missing_workspace),
        std::ffi::OsStr::new("--run-id"),
        std::ffi::OsStr::new("run"),
        std::ffi::OsStr::new("--output"),
        as_os(&missing_output),
    ];
    let output = run_cli(&missing_args, &external);
    assert_eq!(output.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&output.stderr).contains("invalid_workspace"));
    assert!(!missing_output.exists());

    let unknown_output = base.join("unknown.json");
    let unknown_args = [
        std::ffi::OsStr::new("--workspace"),
        as_os(&workspace),
        std::ffi::OsStr::new("--run-id"),
        std::ffi::OsStr::new("run"),
        std::ffi::OsStr::new("--output"),
        as_os(&unknown_output),
        std::ffi::OsStr::new("--network=host"),
    ];
    let output = run_cli(&unknown_args, &external);
    assert_eq!(output.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&output.stderr).contains("invalid_arguments"));
    assert!(!unknown_output.exists());

    let help = cli().arg("--help").output().unwrap();
    assert_eq!(help.status.code(), Some(0));
    assert!(String::from_utf8_lossy(&help.stdout).contains("--workspace"));
}

#[cfg(windows)]
fn create_windows_literal_workspace(path: &Path, marker: &str) {
    fs::create_dir(path).unwrap();
    fs::write(
        path.join("processor.py"),
        format!("# inert {marker} candidate; never executed\n"),
    )
    .unwrap();
    fs::write(path.join(PUBLIC_CHECKS), INERT_CHECKS).unwrap();
}

#[cfg(windows)]
#[test]
fn windows_trailing_dot_and_space_mount_aliases_are_refused() {
    let base = unique_dir("windows mount alias");
    assert!(
        base.to_str().unwrap().starts_with("\\\\?\\"),
        "the owned Windows scratch root must be a verbatim path for literal trailing names"
    );
    for (label, literal_sibling) in [("trailing dot", "ordinary."), ("trailing space", "ordinary ")] {
        let case = base.join(label);
        fs::create_dir(&case).unwrap();
        let ordinary = case.join("ordinary");
        create_windows_literal_workspace(&ordinary, "ordinary");
        let alias = case.join(literal_sibling);
        create_windows_literal_workspace(&alias, "aliased");

        let ordinary_bytes = fs::read(ordinary.join("processor.py")).unwrap();
        let alias_bytes = fs::read(alias.join("processor.py")).unwrap();
        assert_ne!(ordinary_bytes, alias_bytes, "{label} fixtures must differ");

        let error = build_plan(&alias, "f1-alias").unwrap_err();
        assert_eq!(error.code(), "mount_path_identity", "{label}");

        let plan = build_plan(&ordinary, "f1-ordinary").unwrap();
        assert!(!plan.workspace.starts_with("\\\\?\\"), "{label}");
        assert_eq!(
            fs::canonicalize(Path::new(&plan.workspace)).unwrap(),
            fs::canonicalize(&ordinary).unwrap(),
            "{label} emitted workspace identity"
        );
        let emitted_candidate =
            fs::read(Path::new(&plan.workspace).join("processor.py")).unwrap();
        assert_eq!(emitted_candidate, ordinary_bytes, "{label}");
        let recorded = plan
            .inventory
            .files
            .iter()
            .find(|file| file.name == "processor.py")
            .unwrap();
        assert_eq!(recorded.sha256, sha256_hex(&emitted_candidate), "{label}");
        assert_ne!(recorded.sha256, sha256_hex(&alias_bytes), "{label}");
        assert_eq!(
            plan.argv[19],
            format!("type=bind,src={},dst=/workspace,readonly", plan.workspace),
            "{label}"
        );
        assert_eq!(plan.evidence_kind, EVIDENCE_KIND);
    }
}

#[cfg(windows)]
#[test]
fn windows_cli_refuses_mount_alias_and_emits_verified_identity() {
    let base = unique_dir("windows cli alias");
    assert!(base.to_str().unwrap().starts_with("\\\\?\\"));
    let ordinary = base.join("ordinary");
    create_windows_literal_workspace(&ordinary, "ordinary");
    let alias = base.join("ordinary.");
    create_windows_literal_workspace(&alias, "aliased");
    let external = base.join("external");
    fs::create_dir(&external).unwrap();

    let alias_output = external.join("alias.json");
    let alias_args = [
        std::ffi::OsStr::new("--workspace"),
        as_os(&alias),
        std::ffi::OsStr::new("--run-id"),
        std::ffi::OsStr::new("alias-run"),
        std::ffi::OsStr::new("--output"),
        as_os(&alias_output),
    ];
    let refused = run_cli(&alias_args, &external);
    assert_eq!(refused.status.code(), Some(2));
    assert!(
        String::from_utf8_lossy(&refused.stderr).contains("mount_path_identity"),
        "{}",
        String::from_utf8_lossy(&refused.stderr)
    );
    assert!(!alias_output.exists());

    let output = external.join("ordinary.json");
    let ordinary_args = [
        std::ffi::OsStr::new("--workspace"),
        as_os(&ordinary),
        std::ffi::OsStr::new("--run-id"),
        std::ffi::OsStr::new("ordinary-run"),
        std::ffi::OsStr::new("--output"),
        as_os(&output),
    ];
    let accepted = run_cli(&ordinary_args, &external);
    assert_eq!(
        accepted.status.code(),
        Some(0),
        "{}",
        String::from_utf8_lossy(&accepted.stderr)
    );
    let json: Value = serde_json::from_slice(&fs::read(&output).unwrap()).unwrap();
    let emitted = json["workspace"].as_str().unwrap();
    assert_eq!(
        fs::canonicalize(emitted).unwrap(),
        fs::canonicalize(&ordinary).unwrap()
    );
    let candidate = fs::read(Path::new(emitted).join("processor.py")).unwrap();
    let recorded = json["inventory"]["files"]
        .as_array()
        .unwrap()
        .iter()
        .find(|file| file["name"] == "processor.py")
        .unwrap();
    assert_eq!(recorded["sha256"], sha256_hex(&candidate));
    assert_eq!(
        json["argv"][19],
        format!("type=bind,src={emitted},dst=/workspace,readonly")
    );
    assert_eq!(json["evidence_kind"], EVIDENCE_KIND);
}

#[test]
fn cli_has_no_process_runner_and_does_not_touch_docker_sentinel() {
    for source in [
        include_str!("../src/isolation_plan.rs"),
        include_str!("../src/bin/isolation-plan.rs"),
    ] {
        assert!(!source.contains("std::process::Command"));
        assert!(!source.contains("Command::new"));
        assert!(!source.contains("process::Command"));
    }

    let base = unique_dir("docker sentinel");
    let workspace = base.join("workspace");
    fs::create_dir(&workspace).unwrap();
    write_files(&workspace, &retry_files());
    let external = base.join("external");
    fs::create_dir(&external).unwrap();
    let fake_bin = base.join("fake bin");
    fs::create_dir(&fake_bin).unwrap();
    let sentinel = base.join("docker-was-invoked.txt");
    let sentinel_text = sentinel.to_str().unwrap();
    #[cfg(windows)]
    let fake = fake_bin.join("docker.cmd");
    #[cfg(not(windows))]
    let fake = fake_bin.join("docker");
    #[cfg(windows)]
    fs::write(
        &fake,
        format!("@echo invoked>\"{sentinel_text}\"\r\n"),
    )
    .unwrap();
    #[cfg(not(windows))]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::write(
            &fake,
            format!("#!/bin/sh\necho invoked > \"{sentinel_text}\"\n"),
        )
        .unwrap();
        fs::set_permissions(&fake, fs::Permissions::from_mode(0o755)).unwrap();
    }
    assert!(fake.exists());

    let output_path = base.join("plan.json");
    let args = [
        std::ffi::OsStr::new("--workspace"),
        as_os(&workspace),
        std::ffi::OsStr::new("--run-id"),
        std::ffi::OsStr::new("sentinel-run"),
        std::ffi::OsStr::new("--output"),
        as_os(&output_path),
    ];
    let mut paths = vec![fake_bin.clone()];
    paths.extend(env::split_paths(&env::var_os("PATH").unwrap_or_default()));
    let output = cli()
        .args(args)
        .current_dir(&external)
        .env("PATH", env::join_paths(paths).unwrap())
        .env("ISOLATION_DOCKER_SENTINEL", sentinel_text)
        .output()
        .unwrap();
    assert_eq!(
        output.status.code(),
        Some(0),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        !sentinel.exists(),
        "the fake docker sentinel must never be invoked"
    );
    assert!(output_path.exists());
}

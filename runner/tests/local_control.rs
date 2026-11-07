//! Fake boundary tests. The Python subprocess runs locally and simulates the grader protocol.

use serde_json::Value;
use sha2::{Digest, Sha256};
use std::{
    env, fs,
    path::{Path, PathBuf},
    process::{Command, Output},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

struct TestRun {
    root: PathBuf,
    output: PathBuf,
}

impl TestRun {
    fn new(mode: &str) -> Self {
        let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap();
        let base = Path::new(env!("CARGO_MANIFEST_DIR")).join("target/test-runs");
        fs::create_dir_all(&base).unwrap();
        let root = base.join(format!(
            "fake-boundary-{mode}-{}-{}",
            std::process::id(),
            now.as_nanos()
        ));
        fs::create_dir(&root).unwrap();
        fs::create_dir(root.join("grader")).unwrap();
        fs::write(
            root.join("grader/retry_processing.py"),
            include_str!("fake_grader.py"),
        )
        .unwrap();
        fs::write(root.join("processor.py"), format!("# {mode}")).unwrap();
        let output = root.join("run evidence");
        Self { root, output }
    }

    fn run(&self, timeout: &str) -> Output {
        self.run_candidate(Path::new("processor.py"), timeout)
    }

    fn run_candidate(&self, candidate: &Path, timeout: &str) -> Output {
        let python = env::var_os("FOLLOWTHROUGH_TEST_PYTHON").unwrap_or_else(|| "python".into());
        Command::new(env!("CARGO_BIN_EXE_followthrough"))
            .arg("control")
            .arg("--project-root")
            .arg(&self.root)
            .arg("--candidate")
            .arg(candidate)
            .arg("--python")
            .arg(python)
            .arg("--output-dir")
            .arg(&self.output)
            .args(["--timeout-seconds", timeout])
            .env("FOLLOWTHROUGH_FAKE_SECRET", "boundary-test-marker")
            .output()
            .unwrap()
    }

    fn read(&self, filename: &str) -> Value {
        serde_json::from_slice(&fs::read(self.output.join(filename)).unwrap()).unwrap()
    }

    fn assert_status(&self, command: &Output, code: i32, status: &str) -> Value {
        assert_eq!(
            command.status.code(),
            Some(code),
            "{}",
            String::from_utf8_lossy(&command.stderr)
        );
        let summary = self.read("summary.json");
        assert_eq!(summary["status"], status);
        assert_eq!(summary["evidence_kind"], "real_local_control");
        summary
    }
}

#[test]
fn fake_grader_observes_snapshot_and_ordered_events() {
    let test = TestRun::new("mutate_original");
    let summary = test.assert_status(&test.run("5"), 0, "functional_pass");
    let bytes = fs::read(test.output.join("checkpoint/processor.py")).unwrap();
    let expected_hash = format!("{:x}", Sha256::digest(&bytes));
    assert_eq!(bytes, b"# mutate_original");
    assert_ne!(bytes, fs::read(test.root.join("processor.py")).unwrap());
    assert_eq!(summary["checkpoint"]["sha256"], expected_hash);
    assert_eq!(summary["grade"]["candidate"]["sha256"], expected_hash);
    assert_eq!(summary["grade"]["check_count"], 10);
    let grade_path = Path::new(summary["grade"]["candidate"]["path"].as_str().unwrap());
    let checkpoint_path = Path::new(summary["checkpoint"]["snapshot_path"].as_str().unwrap());
    assert_eq!(fs::canonicalize(grade_path).unwrap(), checkpoint_path);

    let jsonl = fs::read_to_string(test.output.join("events.jsonl")).unwrap();
    assert!(!jsonl.contains("boundary-test-marker"));
    let events: Vec<Value> = jsonl
        .lines()
        .map(|line| serde_json::from_str(line).unwrap())
        .collect();
    let expected = [
        "run_started",
        "checkpoint",
        "grader_launch_requested",
        "grader_started",
        "grader_finished",
        "grade_result",
        "run_finished",
    ];
    assert_eq!(events.len(), expected.len());
    for (index, (event, kind)) in events.iter().zip(expected).enumerate() {
        assert_eq!(event["event_type"], kind);
        assert_eq!(event["seq"], index + 1);
        assert_eq!(event["schema_version"], "0.1");
        assert_eq!(event["evidence_kind"], "real_local_control");
        assert_eq!(event["run_id"], summary["run_id"]);
        assert!(event["timestamp_utc"].as_u64().is_some());
    }
    assert!(summary["grader"]["pid"].as_u64().unwrap() > 0);
    assert_eq!(summary["grader"]["exit_code"], 0);
    assert_eq!(summary["grader"]["direct_child_reaped"], true);
}

#[test]
fn distinguishes_functional_failure_from_execution_and_infrastructure() {
    let test = TestRun::new("fail");
    let summary = test.assert_status(&test.run("5"), 1, "functional_fail");
    assert_eq!(summary["grader"]["exit_code"], 0);
    assert_eq!(summary["grade"]["functional_pass"], false);
    assert_eq!(summary["grade"]["check_count"], 10);

    for (mode, expected) in [
        ("candidate", "candidate_execution"),
        ("grader", "infrastructure_error"),
    ] {
        let test = TestRun::new(mode);
        let summary = test.assert_status(&test.run("5"), 2, expected);
        assert_eq!(summary["grader"]["exit_code"], 18);
        assert_eq!(summary["grade"]["check_count"], 1);
        assert_eq!(summary["grade"]["error_count"], 1);
        assert_eq!(test.read("grade.json")["errors"][0]["origin"], mode);
    }

    let test = TestRun::new("no_grade_nonzero");
    let summary = test.assert_status(&test.run("5"), 2, "infrastructure_error");
    assert_eq!(summary["grader"]["exit_code"], 17);
    assert!(summary["grade"].is_null());
}

#[test]
fn rejects_missing_invalid_and_mismatched_grades() {
    for (mode, issue) in [
        ("missing", "grade read failed"),
        ("invalid", "invalid grade JSON"),
        ("wrong_path", "candidate identity differs"),
        ("wrong_hash", "candidate identity differs"),
        ("mutate_snapshot", "candidate identity differs"),
    ] {
        let test = TestRun::new(mode);
        let summary = test.assert_status(&test.run("5"), 2, "infrastructure_error");
        assert_eq!(summary["grader"]["exit_code"], 0);
        assert!(summary["grade"].is_null());
        assert!(summary["issue"].as_str().unwrap().contains(issue));
    }
}

#[test]
fn rejects_omitted_duplicate_and_unknown_completed_check_ids() {
    for (mode, check_count) in [
        ("omitted_check", 9),
        ("duplicate_check", 10),
        ("unknown_check", 10),
    ] {
        let test = TestRun::new(mode);
        let summary = test.assert_status(&test.run("5"), 2, "infrastructure_error");
        assert_eq!(summary["grader"]["exit_code"], 0);
        assert!(summary["grade"].is_null());
        assert_eq!(
            summary["issue"],
            "complete grade must include each required retry check ID exactly once"
        );
        assert_eq!(
            test.read("grade.json")["checks"].as_array().unwrap().len(),
            check_count
        );
    }
}

#[test]
fn timeout_stops_and_reaps_direct_child() {
    let test = TestRun::new("timeout");
    let started = Instant::now();
    let summary = test.assert_status(&test.run("1"), 2, "infrastructure_error");
    assert!(started.elapsed() < Duration::from_secs(5));
    assert_eq!(summary["grader"]["issue"], "timed_out");
    assert_eq!(summary["grader"]["kill_requested"], true);
    assert_eq!(summary["grader"]["direct_child_reaped"], true);
    assert_eq!(
        summary["grader"]["cleanup_scope"],
        "direct_child_only_descendants_not_tracked"
    );
}

#[test]
fn bounds_both_streams_and_omits_raw_output() {
    for (mode, stream) in [("stdout_overflow", "stdout"), ("stderr_overflow", "stderr")] {
        let test = TestRun::new(mode);
        let summary = test.assert_status(&test.run("5"), 2, "infrastructure_error");
        assert_eq!(summary["grader"]["issue"], "output_limit_exceeded");
        assert_eq!(summary["grader"][stream]["limit_exceeded"], true);
        assert_eq!(summary["grader"][stream]["bytes_observed"], 32769);
        let jsonl = fs::read_to_string(test.output.join("events.jsonl")).unwrap();
        assert!(!jsonl.contains(&"x".repeat(128)));
    }
}

#[test]
fn refuses_existing_output_and_candidate_escape() {
    let test = TestRun::new("pass");
    let outside = TestRun::new("pass");
    let command = test.run_candidate(&outside.root.join("processor.py"), "5");
    assert_eq!(command.status.code(), Some(2));
    assert!(!test.output.exists());

    fs::create_dir(&test.output).unwrap();
    let sentinel = test.output.join("sentinel");
    fs::write(&sentinel, "preserve").unwrap();
    let command = test.run("5");
    assert_eq!(command.status.code(), Some(2));
    assert_eq!(fs::read_to_string(sentinel).unwrap(), "preserve");
    assert!(!test.output.join("events.jsonl").exists());
}

#[cfg(unix)]
#[test]
fn refuses_symlink_traversal() {
    let test = TestRun::new("pass");
    std::os::unix::fs::symlink(test.root.join("processor.py"), test.root.join("alias.py")).unwrap();
    let command = test.run_candidate(Path::new("alias.py"), "5");
    assert_eq!(command.status.code(), Some(2));
    assert!(!test.output.exists());

    std::os::unix::fs::symlink(&test.root, test.root.join("alias_root")).unwrap();
    let aliased = TestRun {
        root: test.root.join("alias_root/."),
        output: test.output,
    };
    let command = aliased.run("5");
    assert_eq!(command.status.code(), Some(2));
    assert!(!aliased.output.exists());
}

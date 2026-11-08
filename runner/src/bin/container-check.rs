//! `container-check` CLI: execute the fixed container check lifecycle.
//!
//! This is the production entry point. It builds the accepted plan itself and
//! requires an explicit `--execute` gate. It exposes no command, image, mount,
//! network, environment or deadline override. This assignment does not invoke
//! installed Docker; offline tests use a synthetic library boundary.

#[path = "../container_exec.rs"]
mod container_exec;

use container_exec::{execute, Config, EvidenceKind, ProcessRunner};
use std::{
    env,
    ffi::OsString,
    path::PathBuf,
    process::ExitCode,
    sync::{
        atomic::AtomicBool,
        Arc,
    },
};

const HELP: &str = "container-check --workspace <absolute-flat-directory> --run-id <id> --output-dir <new-absolute-directory> --docker <absolute-executable> --execute

Builds the accepted immutable container plan, performs bounded daemon/image
prerequisites, creates a stopped restricted container, inspects it, attaches the
bounded public check, inspects actual exit state, and removes only the verified
owned container. Without --execute it refuses before spawning. No Docker-invoked
qualification or isolation claim is made by this source-preparation build.

Exit status: 0 check_passed, 1 check_failed, 2 refused/error.";

struct Args {
    workspace: PathBuf,
    run_id: String,
    output_dir: PathBuf,
    docker: PathBuf,
    execute: bool,
}

fn parse_args(mut args: impl Iterator<Item = OsString>) -> Result<Args, String> {
    let mut workspace = None;
    let mut run_id = None;
    let mut output_dir = None;
    let mut docker = None;
    let mut execute = false;
    while let Some(flag) = args.next() {
        match flag.to_str() {
            Some("--execute") => {
                if execute {
                    return Err("invalid_arguments: duplicate option --execute".into());
                }
                execute = true;
                continue;
            }
            Some("--workspace") => {
                let value = value(&mut args, "--workspace")?;
                set_once(&mut workspace, value, "--workspace")?;
            }
            Some("--run-id") => {
                let value = value(&mut args, "--run-id")?;
                set_once(&mut run_id, value, "--run-id")?;
            }
            Some("--output-dir") => {
                let value = value(&mut args, "--output-dir")?;
                set_once(&mut output_dir, value, "--output-dir")?;
            }
            Some("--docker") => {
                let value = value(&mut args, "--docker")?;
                set_once(&mut docker, value, "--docker")?;
            }
            _ => {
                return Err(
                    "invalid_arguments: unknown option (only --workspace, --run-id, --output-dir, --docker and --execute are accepted)"
                        .into(),
                )
            }
        }
    }
    let run_id = run_id
        .ok_or("invalid_arguments: missing --run-id")?
        .to_str()
        .ok_or("invalid_arguments: --run-id must be Unicode")?
        .to_string();
    Ok(Args {
        workspace: workspace
            .map(PathBuf::from)
            .ok_or("invalid_arguments: missing --workspace")?,
        run_id,
        output_dir: output_dir
            .map(PathBuf::from)
            .ok_or("invalid_arguments: missing --output-dir")?,
        docker: docker
            .map(PathBuf::from)
            .ok_or("invalid_arguments: missing --docker")?,
        execute,
    })
}

fn value(
    args: &mut impl Iterator<Item = OsString>,
    flag: &str,
) -> Result<OsString, String> {
    args.next()
        .filter(|value| !value.is_empty())
        .ok_or_else(|| format!("invalid_arguments: missing value for {flag}"))
}

fn set_once(
    slot: &mut Option<OsString>,
    value: OsString,
    flag: &str,
) -> Result<(), String> {
    if slot.is_some() {
        return Err(format!("invalid_arguments: duplicate option {flag}"));
    }
    *slot = Some(value);
    Ok(())
}

fn main() -> ExitCode {
    let args: Vec<OsString> = env::args_os().skip(1).collect();
    if args.len() == 1 && matches!(args[0].to_str(), Some("--help" | "-h")) {
        println!("{HELP}");
        return ExitCode::SUCCESS;
    }
    let parsed = parse_args(args.into_iter()).and_then(|args| {
        let config = Config {
            workspace: args.workspace,
            run_id: args.run_id,
            output_dir: args.output_dir,
            docker: args.docker,
            execute: args.execute,
            evidence_kind: EvidenceKind::RealContainerCheck,
            deadlines: Default::default(),
            cancellation: Arc::new(AtomicBool::new(false)),
        };
        execute(&config, &ProcessRunner)
            .map_err(|error| format!("{error}"))
    });
    match parsed {
        Ok(execution) => {
            println!(
                "container-check: {} receipt={}",
                match execution.status {
                    container_exec::Status::CheckPassed => "check_passed",
                    container_exec::Status::CheckFailed => "check_failed",
                    container_exec::Status::Refused => "refused",
                    container_exec::Status::Timeout => "timeout",
                    container_exec::Status::OutputLimit => "output_limit",
                    container_exec::Status::Cancelled => "cancelled",
                    container_exec::Status::InfrastructureError => "infrastructure_error",
                },
                execution.receipt_path.display()
            );
            if let Some(diagnostic) = execution.diagnostic.as_deref() {
                eprintln!("container-check: {diagnostic}");
            }
            ExitCode::from(execution.exit_code())
        }
        Err(error) => {
            eprintln!("container-check: {error}");
            ExitCode::from(2)
        }
    }
}

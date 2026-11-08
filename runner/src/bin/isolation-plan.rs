//! `isolation-plan` CLI: validate a workspace, hash its files, write a plan.
//!
//! This binary prepares JSON only. It never starts Docker, a container, a shell,
//! a provider, or candidate code, and it makes no isolation or execution claim.

#[path = "../isolation_plan.rs"]
mod isolation_plan;

use isolation_plan::{build_plan, PlanError};
use std::{
    env,
    ffi::OsString,
    fs::{self, OpenOptions},
    io::Write,
    path::{Component, Path, PathBuf},
    process::ExitCode,
};

const HELP: &str = "isolation-plan --workspace <existing-flat-directory> --run-id <id> --output <new-json-file>

Validates a prepared check workspace, hashes its allowlisted files, and writes a
new JSON container command plan. The plan is not executed. --output must name a
new file outside the workspace.

Exit status: 0 plan written; 2 invalid input or write failure.";

struct Args {
    workspace: PathBuf,
    run_id: String,
    output: PathBuf,
}

fn parse_args(mut args: impl Iterator<Item = OsString>) -> Result<Args, String> {
    let mut workspace = None;
    let mut run_id = None;
    let mut output = None;
    while let Some(flag) = args.next() {
        let field = match flag.to_str() {
            Some("--workspace") => &mut workspace,
            Some("--run-id") => &mut run_id,
            Some("--output") => &mut output,
            _ => {
                return Err(
                    "invalid_arguments: unknown option (only --workspace, --run-id and --output are accepted)"
                        .into(),
                )
            }
        };
        if field.is_some() {
            return Err(format!(
                "invalid_arguments: duplicate option {}",
                flag.to_string_lossy()
            ));
        }
        let value = args.next().filter(|arg| !arg.is_empty()).ok_or_else(|| {
            format!(
                "invalid_arguments: missing value for {}",
                flag.to_string_lossy()
            )
        })?;
        *field = Some(value);
    }

    let workspace = workspace
        .map(PathBuf::from)
        .ok_or("invalid_arguments: missing --workspace")?;
    let run_id = run_id
        .ok_or("invalid_arguments: missing --run-id")?
        .to_str()
        .ok_or("invalid_arguments: --run-id must be valid Unicode")?
        .to_string();
    let output = output
        .map(PathBuf::from)
        .ok_or("invalid_arguments: missing --output")?;
    Ok(Args {
        workspace,
        run_id,
        output,
    })
}

fn resolve_path(base: &Path, path: &Path, flag: &str) -> Result<PathBuf, String> {
    if path.to_str().is_none() {
        return Err(format!(
            "invalid_arguments: {flag} must be valid Unicode"
        ));
    }
    if path.as_os_str().is_empty() {
        return Err(format!("invalid_arguments: {flag} must not be empty"));
    }
    if path.components().any(|part| part == Component::ParentDir) {
        return Err(format!(
            "invalid_arguments: {flag} must not contain parent directory components"
        ));
    }
    #[cfg(windows)]
    if path
        .components()
        .any(|part| matches!(part, Component::Normal(name) if name.to_string_lossy().contains(':')))
    {
        return Err(format!(
            "invalid_arguments: {flag} must not contain alternate data stream components"
        ));
    }
    if path.is_absolute() {
        Ok(path.to_path_buf())
    } else if path.has_root()
        || matches!(path.components().next(), Some(Component::Prefix(_)))
    {
        Err(format!(
            "invalid_arguments: {flag} must not be drive-relative or rooted-relative"
        ))
    } else {
        Ok(base.join(path))
    }
}

fn output_parent(output: &Path) -> Result<PathBuf, String> {
    if output
        .to_str()
        .is_some_and(|text| text.ends_with(['\\', '/']))
    {
        return Err("invalid_output: output must name a new file, not a directory".into());
    }
    let name = output
        .file_name()
        .ok_or("invalid_output: output must name a new file")?;
    if output.components().last() != Some(Component::Normal(name)) {
        return Err("invalid_output: output must name a new file".into());
    }
    let parent = output
        .parent()
        .ok_or("invalid_output: output parent directory is missing")?;
    let ancestors: Vec<&Path> = parent.ancestors().collect();
    for ancestor in ancestors.into_iter().rev() {
        if ancestor.file_name().is_some() {
            let metadata = fs::symlink_metadata(ancestor).map_err(|error| {
                format!(
                    "invalid_output: output parent is not accessible ({})",
                    error.kind()
                )
            })?;
            if is_link(&metadata) {
                return Err(
                    "invalid_output: output path traverses a symlink or reparse point".into(),
                );
            }
        }
    }
    let canonical = fs::canonicalize(parent).map_err(|error| {
        format!(
            "invalid_output: output parent cannot be canonicalized ({})",
            error.kind()
        )
    })?;
    if !canonical.is_dir() {
        return Err("invalid_output: output parent must be a directory".into());
    }
    Ok(canonical)
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

fn plan_error(error: PlanError) -> String {
    format!("{}: {}", error.code(), error.message())
}

fn run(args: Args) -> Result<(), String> {
    let cwd = env::current_dir()
        .map_err(|error| format!("invalid_arguments: current directory unavailable ({error})"))?;
    let workspace = resolve_path(&cwd, &args.workspace, "--workspace")?;
    let plan = build_plan(&workspace, &args.run_id).map_err(plan_error)?;

    let workspace_canonical = fs::canonicalize(&workspace).map_err(|error| {
        format!(
            "invalid_workspace: workspace cannot be canonicalized ({})",
            error.kind()
        )
    })?;
    let output = resolve_path(&cwd, &args.output, "--output")?;
    let parent = output_parent(&output)?;
    if parent.starts_with(&workspace_canonical) {
        return Err(
            "invalid_output: output must be outside the workspace; the plan hashes are not a receipt"
                .into(),
        );
    }
    let output_path = parent.join(
        output
            .file_name()
            .ok_or("invalid_output: output must name a new file")?,
    );
    match fs::symlink_metadata(&output_path) {
        Ok(_) => return Err("output_exists: refusing to overwrite an existing output".into()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => {
            return Err(format!(
                "invalid_output: output cannot be inspected ({})",
                error.kind()
            ))
        }
    }

    let mut payload = serde_json::to_vec_pretty(&plan)
        .map_err(|error| format!("plan_serialization: {error}"))?;
    payload.push(b'\n');
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&output_path)
        .map_err(|error| {
            if error.kind() == std::io::ErrorKind::AlreadyExists {
                "output_exists: refusing to overwrite an existing output".to_string()
            } else {
                format!(
                    "output_write: cannot create the new output file ({})",
                    error.kind()
                )
            }
        })?;
    if let Err(error) = file.write_all(&payload) {
        drop(file);
        let _ = fs::remove_file(&output_path);
        return Err(format!(
            "output_write: cannot write the output file ({})",
            error.kind()
        ));
    }
    println!("isolation-plan: plan written; no execution was attempted");
    Ok(())
}

fn main() -> ExitCode {
    let args: Vec<OsString> = env::args_os().skip(1).collect();
    if args.len() == 1 && matches!(args[0].to_str(), Some("--help" | "-h")) {
        println!("{HELP}");
        return ExitCode::SUCCESS;
    }
    let result = parse_args(args.into_iter()).and_then(run);
    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("isolation-plan: {error}");
            ExitCode::from(2)
        }
    }
}

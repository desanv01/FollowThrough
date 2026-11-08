//! Typed Docker command-plan builder for prepared FollowThrough check workspaces.
//!
//! The planner is deliberately inert: it reads and hashes allowlisted bytes and
//! returns a serializable plan. It never starts Docker, a container, a shell, a
//! provider, or candidate code, and it makes no execution or isolation claim.
//! Plan generation and execution qualification are separate future steps.

use serde::Serialize;
use sha2::{Digest, Sha256};
use std::{
    fmt,
    fs::{self, File},
    io::Read,
    path::{Component, Path, PathBuf},
};

pub const SCHEMA_VERSION: &str = "0.1";
pub const KIND: &str = "agent_check_plan";
pub const EVIDENCE_KIND: &str = "execution_plan_not_run";
/// Immutable digest fixed by the isolation-plan contract. Availability must be
/// rechecked by the later execution owner; this planner does not pull images.
pub const IMAGE: &str = "python@sha256:d29f48a31a8b408ed19272ca1e7b10ebae13b240a27e862d3d4217c528e2e0c3";
pub const PROGRAM: &str = "docker";
pub const MAX_RUN_ID_BYTES: usize = 48;
pub const PER_FILE_LIMIT_BYTES: u64 = 1024 * 1024;
pub const TOTAL_LIMIT_BYTES: u64 = 2 * 1024 * 1024;
pub const RUNTIME_SECONDS: u64 = 60;
pub const STREAM_LIMIT_BYTES: u64 = 32 * 1024;
pub const CLEANUP_DEADLINE_SECONDS: u64 = 5;
pub const CANDIDATE_FILES: [&str; 2] = ["processor.py", "preview.py"];
pub const PUBLIC_CHECKS: &str = "public_checks.py";
pub const CONTEXT_FILES: [&str; 4] = ["TASK.md", "SPEC.md", "WORKFLOW.md", "HISTORY.json"];
pub const WORKSPACE_CONTAINER_PATH: &str = "/workspace";

#[cfg(windows)]
const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x400;

/// A validation or planning failure with a stable code and a sanitized message.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PlanError {
    code: &'static str,
    message: String,
}

impl PlanError {
    fn new(code: &'static str, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }

    /// Stable machine-readable error code.
    pub fn code(&self) -> &'static str {
        self.code
    }

    /// Human-readable diagnostic that never includes file contents.
    pub fn message(&self) -> &str {
        &self.message
    }
}

impl fmt::Display for PlanError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "{}: {}", self.code, self.message)
    }
}

impl std::error::Error for PlanError {}

/// Role of one validated flat workspace file.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum FileRole {
    Candidate,
    PublicChecks,
    Context,
}

/// One allowlisted regular file with the exact bytes that were hashed.
#[derive(Debug, Serialize)]
pub struct InventoryFile {
    pub name: String,
    pub role: FileRole,
    pub bytes: u64,
    pub sha256: String,
}

/// Flat workspace inventory. Only these bytes are visible to the container.
#[derive(Debug, Serialize)]
pub struct Inventory {
    pub files: Vec<InventoryFile>,
    pub file_count: usize,
    pub total_bytes: u64,
    pub per_file_limit_bytes: u64,
    pub total_limit_bytes: u64,
}

/// Fixed restrictions declared to the later execution owner.
#[derive(Debug, Serialize)]
pub struct Resources {
    pub read_only_root: bool,
    pub workspace_read_only: bool,
    pub network: &'static str,
    pub drop_all_capabilities: bool,
    pub no_new_privileges: bool,
    pub pids_limit: u16,
    pub memory: &'static str,
    pub cpus: &'static str,
    pub user: &'static str,
    pub user_uid: u32,
    pub user_gid: u32,
    pub tmpfs: &'static str,
    pub pull: &'static str,
    pub remove_after_exit: bool,
}

/// Policy fields a future executor must enforce. They are declarations here,
/// not observations and not enforcement claims.
#[derive(Debug, Serialize)]
pub struct ExecutorPolicy {
    pub enforcement: &'static str,
    pub runtime_seconds: u64,
    pub stdout_limit_bytes: u64,
    pub stderr_limit_bytes: u64,
    pub cleanup_deadline_seconds: u64,
}

/// The complete validated plan. Serializing it is not an execution receipt.
#[derive(Debug, Serialize)]
pub struct Plan {
    pub schema_version: &'static str,
    pub kind: &'static str,
    pub evidence_kind: &'static str,
    pub run_id: String,
    pub container_name: String,
    pub image: &'static str,
    pub program: &'static str,
    pub workspace: String,
    pub candidate_file: String,
    pub inventory: Inventory,
    pub resources: Resources,
    pub argv: Vec<String>,
    pub executor_policy: ExecutorPolicy,
}

/// Validate a run ID: 1..=48 ASCII alphanumeric, `_` or `-`.
pub fn validate_run_id(run_id: &str) -> Result<(), PlanError> {
    if run_id.is_empty()
        || run_id.len() > MAX_RUN_ID_BYTES
        || !run_id
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_' || byte == b'-')
    {
        return Err(PlanError::new(
            "invalid_run_id",
            format!(
                "run ID must be 1-{MAX_RUN_ID_BYTES} ASCII alphanumeric, '_' or '-' characters"
            ),
        ));
    }
    Ok(())
}

/// Refuse Docker `--mount` delimiter injection in a path used as a mount source.
///
/// Ordinary Windows drive colons and spaces are valid. Commas, CR, LF and NUL
/// are refused because Docker's `--mount` option uses comma-separated fields and
/// line/NUL delimiters are unsafe for command-line transport.
pub fn validate_mount_path(path: &str) -> Result<(), PlanError> {
    if path.is_empty() {
        return Err(PlanError::new("mount_path", "workspace path must not be empty"));
    }
    if path
        .chars()
        .any(|character| matches!(character, ',' | '\n' | '\r' | '\0'))
    {
        return Err(PlanError::new(
            "mount_delimiter",
            "workspace path contains a Docker --mount delimiter; comma, CR, LF and NUL are refused",
        ));
    }
    Ok(())
}

/// Build a validated plan for `workspace` and `run_id`.
///
/// Reading and hashing input files is the only filesystem effect. The workspace
/// must be an existing absolute directory with no symlink or reparse component.
pub fn build_plan(workspace: &Path, run_id: &str) -> Result<Plan, PlanError> {
    validate_run_id(run_id)?;
    let workspace_path = validate_workspace(workspace)?;
    let inventory = read_inventory(&workspace_path)?;
    // Derive the emitted mount source only after hashing the validated inventory,
    // then prove the conventional path still names that same canonical directory.
    let workspace_display = canonical_mount_path(&workspace_path)?;
    validate_mount_path(&workspace_display)?;
    let candidate_file = inventory
        .files
        .iter()
        .find(|file| file.role == FileRole::Candidate)
        .map(|file| file.name.clone())
        .expect("inventory validation guarantees exactly one candidate");
    let container_name = format!("ft-check-{run_id}");
    let argv = fixed_argv(run_id, &container_name, &workspace_display);
    Ok(Plan {
        schema_version: SCHEMA_VERSION,
        kind: KIND,
        evidence_kind: EVIDENCE_KIND,
        run_id: run_id.to_string(),
        container_name,
        image: IMAGE,
        program: PROGRAM,
        workspace: workspace_display,
        candidate_file,
        inventory,
        resources: fixed_resources(),
        argv,
        executor_policy: ExecutorPolicy {
            enforcement: "declared_policy_only_not_enforced",
            runtime_seconds: RUNTIME_SECONDS,
            stdout_limit_bytes: STREAM_LIMIT_BYTES,
            stderr_limit_bytes: STREAM_LIMIT_BYTES,
            cleanup_deadline_seconds: CLEANUP_DEADLINE_SECONDS,
        },
    })
}

fn fixed_resources() -> Resources {
    Resources {
        read_only_root: true,
        workspace_read_only: true,
        network: "none",
        drop_all_capabilities: true,
        no_new_privileges: true,
        pids_limit: 64,
        memory: "256m",
        cpus: "1",
        user: "65534:65534",
        user_uid: 65534,
        user_gid: 65534,
        tmpfs: "/tmp:rw,noexec,nosuid,size=16m",
        pull: "never",
        remove_after_exit: true,
    }
}

fn fixed_argv(run_id: &str, container_name: &str, workspace: &str) -> Vec<String> {
    let mount = format!("type=bind,src={workspace},dst={WORKSPACE_CONTAINER_PATH},readonly");
    vec![
        "run".to_string(),
        "--pull=never".to_string(),
        "--rm".to_string(),
        "--name".to_string(),
        container_name.to_string(),
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
        mount,
        "--entrypoint=python".to_string(),
        IMAGE.to_string(),
        "-I".to_string(),
        "-B".to_string(),
        "/workspace/public_checks.py".to_string(),
    ]
}

fn validate_workspace(workspace: &Path) -> Result<PathBuf, PlanError> {
    if workspace.to_str().is_none() {
        return Err(PlanError::new(
            "unrepresentable_path",
            "workspace path must be representable as Unicode",
        ));
    }
    if !workspace.is_absolute() {
        return Err(PlanError::new(
            "invalid_workspace",
            "workspace must be an absolute existing directory",
        ));
    }
    if workspace
        .components()
        .any(|part| part == Component::ParentDir)
    {
        return Err(PlanError::new(
            "invalid_workspace",
            "workspace path must not contain parent components",
        ));
    }
    #[cfg(windows)]
    if workspace.components().any(
        |part| matches!(part, Component::Normal(name) if name.to_string_lossy().contains(':')),
    ) {
        return Err(PlanError::new(
            "unrepresentable_path",
            "workspace path must not contain alternate data stream components",
        ));
    }

    let normalized = normalize(workspace);
    let ancestors: Vec<&Path> = normalized.ancestors().collect();
    for ancestor in ancestors.into_iter().rev() {
        if ancestor.file_name().is_some() {
            let metadata = fs::symlink_metadata(ancestor).map_err(|error| {
                PlanError::new(
                    "invalid_workspace",
                    format!(
                        "workspace path is not accessible ({}); it must exist",
                        error.kind()
                    ),
                )
            })?;
            if is_link(&metadata) {
                return Err(PlanError::new(
                    "link_component",
                    "workspace path traverses a symlink or reparse point",
                ));
            }
        }
    }
    let metadata = fs::symlink_metadata(&normalized).map_err(|error| {
        PlanError::new(
            "invalid_workspace",
            format!("workspace metadata is unavailable ({})", error.kind()),
        )
    })?;
    if !metadata.is_dir() {
        return Err(PlanError::new(
            "invalid_workspace",
            "workspace must be a directory",
        ));
    }
    let canonical = fs::canonicalize(&normalized).map_err(|error| {
        PlanError::new(
            "invalid_workspace",
            format!("workspace path cannot be canonicalized ({})", error.kind()),
        )
    })?;
    if !canonical.is_dir() || canonical.to_str().is_none() {
        return Err(PlanError::new(
            "unrepresentable_path",
            "canonical workspace must be a Unicode directory",
        ));
    }
    Ok(canonical)
}

fn normalize(path: &Path) -> PathBuf {
    path.components()
        .filter(|part| *part != Component::CurDir)
        .collect()
}

/// Emit a conventional Docker mount source for the validated canonical directory.
///
/// Windows `fs::canonicalize` returns an extended-length path. Removing the
/// verbatim prefix can change Win32 pathname interpretation, notably for names
/// with trailing dots or spaces. Only canonical drive and UNC forms are converted,
/// and the converted path must resolve back to the exact validated canonical
/// directory before it is emitted. This is a stable-path identity check, not a
/// claim of race-proof filesystem access.
fn canonical_mount_path(canonical: &Path) -> Result<String, PlanError> {
    let canonical_text = canonical.to_str().ok_or_else(|| {
        PlanError::new(
            "unrepresentable_path",
            "canonical workspace must be representable as Unicode",
        )
    })?;
    let conventional = conventional_mount_source(canonical_text)?;
    let resolved = fs::canonicalize(Path::new(&conventional)).map_err(|error| {
        PlanError::new(
            "mount_path_identity",
            format!(
                "conventional workspace path cannot be resolved back to the validated directory ({})",
                error.kind()
            ),
        )
    })?;
    if resolved.as_path() != canonical {
        return Err(PlanError::new(
            "mount_path_identity",
            "conventional workspace path resolves to a different directory than the validated canonical workspace",
        ));
    }
    Ok(conventional)
}

#[cfg(windows)]
fn conventional_mount_source(canonical: &str) -> Result<String, PlanError> {
    if let Some(rest) = canonical.strip_prefix("\\\\?\\UNC\\") {
        let mut parts = rest.splitn(3, '\\');
        let server = parts.next().unwrap_or_default();
        let share = parts.next().unwrap_or_default();
        if server.is_empty() || share.is_empty() {
            return Err(unsupported_mount_namespace());
        }
        return Ok(format!("\\\\{rest}"));
    }
    if let Some(rest) = canonical.strip_prefix("\\\\?\\") {
        let bytes = rest.as_bytes();
        if bytes.len() >= 3
            && bytes[0].is_ascii_alphabetic()
            && bytes[1] == b':'
            && bytes[2] == b'\\'
        {
            return Ok(rest.to_string());
        }
        return Err(unsupported_mount_namespace());
    }
    Err(unsupported_mount_namespace())
}

#[cfg(windows)]
fn unsupported_mount_namespace() -> PlanError {
    PlanError::new(
        "unsupported_mount_namespace",
        "canonical workspace path uses an unsupported Windows namespace; only drive and UNC forms can be emitted as Docker mount sources",
    )
}

#[cfg(not(windows))]
fn conventional_mount_source(canonical: &str) -> Result<String, PlanError> {
    Ok(canonical.to_string())
}

#[cfg(all(test, windows))]
mod windows_mount_source_tests {
    use super::*;

    #[test]
    fn converts_only_supported_canonical_drive_and_unc_forms() {
        assert_eq!(
            conventional_mount_source("\\\\?\\C:\\dir with space").unwrap(),
            "C:\\dir with space"
        );
        assert_eq!(
            conventional_mount_source("\\\\?\\UNC\\server\\share\\dir").unwrap(),
            "\\\\server\\share\\dir"
        );
        for unsupported in [
            "\\\\?\\C:",
            "\\\\?\\Volume{00000000-0000-0000-0000-000000000000}\\dir",
            "\\\\?\\GLOBALROOT\\Device\\HarddiskVolume1\\dir",
            "\\\\.\\C:\\dir",
            "C:\\dir",
            "\\\\server\\share\\dir",
            "\\\\?\\UNC\\server",
        ] {
            let error = conventional_mount_source(unsupported).unwrap_err();
            assert_eq!(
                error.code(),
                "unsupported_mount_namespace",
                "unsupported namespace {unsupported:?}"
            );
        }
    }
}

fn read_inventory(workspace: &Path) -> Result<Inventory, PlanError> {
    let entries = fs::read_dir(workspace).map_err(|error| {
        PlanError::new(
            "invalid_workspace",
            format!("workspace cannot be listed ({})", error.kind()),
        )
    })?;
    let mut names = Vec::new();
    let mut candidates = 0usize;
    let mut has_public_checks = false;
    for entry in entries {
        let entry = entry.map_err(|error| {
            PlanError::new(
                "invalid_workspace",
                format!("workspace entry cannot be read ({})", error.kind()),
            )
        })?;
        let name = entry.file_name();
        let name = name.to_str().ok_or_else(|| {
            PlanError::new(
                "unrepresentable_path",
                "workspace entry names must be representable as Unicode",
            )
        })?;
        let metadata = fs::symlink_metadata(entry.path()).map_err(|error| {
            PlanError::new(
                "invalid_workspace",
                format!("workspace entry metadata failed ({})", error.kind()),
            )
        })?;
        if is_link(&metadata) {
            return Err(PlanError::new(
                "link_component",
                format!(
                    "workspace entry {:?} is a symlink or reparse point",
                    name
                ),
            ));
        }
        if metadata.is_dir() {
            return Err(PlanError::new(
                "unexpected_directory",
                format!(
                    "workspace entry {:?} is a directory; the inventory must be flat",
                    name
                ),
            ));
        }
        if !metadata.is_file() {
            return Err(PlanError::new(
                "not_regular_file",
                format!("workspace entry {:?} is not a regular file", name),
            ));
        }
        if CANDIDATE_FILES.contains(&name) {
            candidates += 1;
        } else if name == PUBLIC_CHECKS {
            has_public_checks = true;
        } else if !CONTEXT_FILES.contains(&name) {
            return Err(PlanError::new(
                "unexpected_entry",
                format!(
                    "unexpected workspace entry {:?}; only the flat allowlist is accepted",
                    name
                ),
            ));
        }
        names.push(name.to_string());
    }
    if candidates > 1 {
        return Err(PlanError::new(
            "both_candidates",
            "workspace must contain exactly one candidate file, not both processor.py and preview.py",
        ));
    }
    if candidates == 0 {
        return Err(PlanError::new(
            "missing_candidate",
            "workspace must contain exactly one candidate file: processor.py or preview.py",
        ));
    }
    if !has_public_checks {
        return Err(PlanError::new(
            "missing_public_checks",
            "workspace must contain public_checks.py",
        ));
    }

    names.sort();
    let mut files = Vec::with_capacity(names.len());
    let mut total_bytes = 0u64;
    for name in names {
        let bytes = read_bounded(&workspace.join(&name), &name)?;
        total_bytes = total_bytes
            .checked_add(bytes.len() as u64)
            .ok_or_else(|| PlanError::new("total_size", "workspace size overflowed"))?;
        if total_bytes > TOTAL_LIMIT_BYTES {
            return Err(PlanError::new(
                "total_size",
                format!("workspace exceeds the total {TOTAL_LIMIT_BYTES}-byte limit"),
            ));
        }
        files.push(InventoryFile {
            role: role_for(&name),
            sha256: sha256_hex(&bytes),
            bytes: bytes.len() as u64,
            name,
        });
    }
    Ok(Inventory {
        file_count: files.len(),
        files,
        total_bytes,
        per_file_limit_bytes: PER_FILE_LIMIT_BYTES,
        total_limit_bytes: TOTAL_LIMIT_BYTES,
    })
}

fn read_bounded(path: &Path, name: &str) -> Result<Vec<u8>, PlanError> {
    let metadata = fs::symlink_metadata(path).map_err(|error| {
        PlanError::new(
            "read_failed",
            format!("workspace file {:?} is unavailable ({})", name, error.kind()),
        )
    })?;
    if metadata.len() > PER_FILE_LIMIT_BYTES {
        return Err(PlanError::new(
            "file_size",
            format!(
                "workspace file {:?} exceeds the per-file {PER_FILE_LIMIT_BYTES}-byte limit",
                name
            ),
        ));
    }
    let file = File::open(path).map_err(|error| {
        PlanError::new(
            "read_failed",
            format!("workspace file {:?} cannot be opened ({})", name, error.kind()),
        )
    })?;
    let mut bytes = Vec::new();
    file.take(PER_FILE_LIMIT_BYTES + 1)
        .read_to_end(&mut bytes)
        .map_err(|error| {
            PlanError::new(
                "read_failed",
                format!("workspace file {:?} cannot be read ({})", name, error.kind()),
            )
        })?;
    if bytes.len() as u64 > PER_FILE_LIMIT_BYTES {
        return Err(PlanError::new(
            "file_size",
            format!(
                "workspace file {:?} exceeds the per-file {PER_FILE_LIMIT_BYTES}-byte limit",
                name
            ),
        ));
    }
    if bytes.len() as u64 != metadata.len() {
        return Err(PlanError::new(
            "workspace_changed",
            format!(
                "workspace file {:?} changed while it was being hashed; retry from a stable workspace",
                name
            ),
        ));
    }
    Ok(bytes)
}

fn role_for(name: &str) -> FileRole {
    if CANDIDATE_FILES.contains(&name) {
        FileRole::Candidate
    } else if name == PUBLIC_CHECKS {
        FileRole::PublicChecks
    } else {
        FileRole::Context
    }
}

fn sha256_hex(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn is_link(metadata: &fs::Metadata) -> bool {
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
    }
    #[cfg(not(windows))]
    {
        metadata.file_type().is_symlink()
    }
}

use std::{
    fs::{self, Metadata},
    io::{self, Read},
    path::{Component, Path, PathBuf},
};

fn is_link(metadata: &Metadata) -> bool {
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

fn invalid(message: impl Into<String>) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidInput, message.into())
}

pub fn absolute_from(base: &Path, path: &Path) -> io::Result<PathBuf> {
    if path.to_str().is_none() || base.to_str().is_none() {
        return Err(invalid("run paths must be representable as Unicode"));
    }
    if path.as_os_str().is_empty()
        || (!path.is_absolute()
            && (path.has_root() || matches!(path.components().next(), Some(Component::Prefix(_)))))
        || path.components().any(|part| part == Component::ParentDir)
    {
        return Err(invalid(
            "empty, drive-relative, rooted-relative, and parent paths are invalid",
        ));
    }
    #[cfg(windows)]
    if path
        .components()
        .any(|part| matches!(part, Component::Normal(name) if name.to_string_lossy().contains(':')))
    {
        return Err(invalid("alternate data stream paths are invalid"));
    }
    Ok(base.join(path))
}

pub fn non_link_metadata(path: &Path) -> io::Result<Metadata> {
    let metadata = fs::symlink_metadata(path)?;
    if is_link(&metadata) {
        return Err(invalid("symlink or reparse-point traversal refused"));
    }
    Ok(metadata)
}

pub fn checked_existing(path: &Path) -> io::Result<PathBuf> {
    // A trailing slash or dot can make symlink_metadata follow the final link.
    let path: PathBuf = path
        .components()
        .filter(|part| *part != Component::CurDir)
        .collect();
    let ancestors: Vec<_> = path.ancestors().collect();
    for component in ancestors.into_iter().rev() {
        if component.file_name().is_some() {
            non_link_metadata(component)?;
        }
    }
    let canonical = fs::canonicalize(&path)?;
    if canonical.to_str().is_none() {
        return Err(invalid(
            "canonical run paths must be representable as Unicode",
        ));
    }
    Ok(canonical)
}

pub fn project_file(root: &Path, path: &Path) -> io::Result<PathBuf> {
    let absolute = absolute_from(root, path)?;
    let canonical = checked_existing(&absolute)?;
    if !canonical.starts_with(root) || !non_link_metadata(&canonical)?.is_file() {
        return Err(invalid("expected a regular file inside the project root"));
    }
    Ok(canonical)
}

pub fn create_output_dir(cwd: &Path, path: &Path) -> io::Result<PathBuf> {
    let absolute = absolute_from(cwd, path)?;
    let name = absolute
        .file_name()
        .ok_or_else(|| invalid("missing output directory name"))?;
    let parent = checked_existing(
        absolute
            .parent()
            .ok_or_else(|| invalid("missing output parent"))?,
    )?;
    let output = parent.join(name);
    fs::create_dir(&output)?;
    Ok(output)
}

pub fn read_bounded(path: &Path, limit: usize) -> io::Result<Vec<u8>> {
    if !non_link_metadata(path)?.is_file() {
        return Err(invalid("expected a regular file"));
    }
    let mut bytes = Vec::new();
    fs::File::open(path)?
        .take((limit + 1) as u64)
        .read_to_end(&mut bytes)?;
    if bytes.len() > limit {
        return Err(invalid(format!("file exceeds {limit} bytes")));
    }
    Ok(bytes)
}

"""Clean-package reproduction helpers: ZIP validation, manifests, exclusions.

Read-only against inputs; extraction only into a new owned directory. No
network, credentials, Git or provider access.
"""
from __future__ import annotations

import hashlib
import json
import re
import stat
import zipfile
from pathlib import Path

DRIVE_RE = re.compile(r"^[A-Za-z]:")
FORBIDDEN_PARTS = {".env", ".coordination", "runs", "tmp", ".git", "__pycache__"}
FORBIDDEN_NAMES = {".env.local", "provider-budget.sqlite3"}
SECRET_SUFFIXES = (".sqlite", ".sqlite3", ".db", ".pem", ".key", ".pfx")
SECRET_WORDS = ("credential", "secret", "api_key", "apikey", "token")


class ReproductionError(ValueError):
    """Unsafe or inconsistent package/archive."""


def _sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _sha_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def inspect_zip(zip_path, expected_comment):
    """Validate archive identity and entry safety without extracting."""
    zip_path = Path(zip_path)
    if not zip_path.is_file():
        raise ReproductionError("zip missing")
    refusals = []
    files = []
    with zipfile.ZipFile(zip_path) as zf:
        comment = zf.comment.decode("utf-8", "replace").strip()
        if comment != expected_comment:
            raise ReproductionError("zip comment does not match the required identity")
        names = [i.filename for i in zf.infolist()]
        # Determine the single source prefix from entries that are themselves safe
        # relative names; unsafe absolute/traversal names are recorded as refusals later.
        tops = set()
        for name in names:
            if ("\x00" in name or "\\" in name or name.startswith("/")
                    or DRIVE_RE.match(name)):
                continue
            stripped = name.rstrip("/")
            parts = stripped.split("/")
            if any(p in ("", ".", "..") for p in parts):
                continue
            if len(parts) >= 2:
                tops.add(parts[0])
        if len(tops) != 1:
            raise ReproductionError("archive must have a single top-level prefix")
        prefix = next(iter(tops))
        seen = set()
        total = 0
        for info in zf.infolist():
            name = info.filename
            if "\x00" in name or "\\" in name:
                refusals.append({"name": name, "reason": "nul_or_backslash"})
                continue
            if name.startswith("/") or DRIVE_RE.match(name):
                refusals.append({"name": name, "reason": "absolute_or_drive"})
                continue
            stripped = name.rstrip("/")
            parts = stripped.split("/")
            if any(p in ("", ".", "..") for p in parts):
                refusals.append({"name": name, "reason": "traversal_or_empty_segment"})
                continue
            if parts[0] != prefix or len(parts) == 1:
                if stripped == prefix and name.endswith("/"):
                    continue
                refusals.append({"name": name, "reason": "outside_single_prefix"})
                continue
            rel = "/".join(parts[1:])
            key = rel.casefold()
            if key in seen:
                refusals.append({"name": name, "reason": "duplicate_destination"})
                continue
            seen.add(key)
            mode = (info.external_attr >> 16) & 0xFFFF
            filetype = mode & 0o170000
            is_dir = name.endswith("/")
            if filetype == 0o120000:
                refusals.append({"name": name, "reason": "symlink"})
                continue
            if filetype not in (0, 0o040000, 0o100000):
                refusals.append({"name": name, "reason": "special_file", "mode": oct(mode)})
                continue
            if is_dir and filetype not in (0, 0o040000):
                refusals.append({"name": name, "reason": "dir_name_non_dir_mode"})
                continue
            if not is_dir and filetype not in (0, 0o100000):
                refusals.append({"name": name, "reason": "file_name_non_file_mode"})
                continue
            if is_dir:
                continue
            h = hashlib.sha256()
            size = 0
            with zf.open(info) as src:
                for chunk in iter(lambda: src.read(1 << 20), b""):
                    h.update(chunk)
                    size += len(chunk)
            files.append({"rel": rel, "bytes": size, "sha256": h.hexdigest()})
            total += size
    files.sort(key=lambda f: f["rel"])
    tree = hashlib.sha256()
    for f in files:
        tree.update(f'{f["rel"]}\t{f["bytes"]}\t{f["sha256"]}\n'.encode())
    return {
        "zip_bytes": zip_path.stat().st_size,
        "zip_sha256": _sha_file(zip_path),
        "comment": expected_comment,
        "prefix": prefix,
        "entries": len(names),
        "files": len(files),
        "bytes": total,
        "tree_manifest_sha256": tree.hexdigest(),
        "refusals": refusals,
        "safe": not refusals,
        "entries_detail": files,
    }


def safe_extract(zip_path, dest, expected_comment):
    """Extract after full validation into a new directory with exclusive creates."""
    dest = Path(dest)
    if dest.exists():
        raise ReproductionError("destination already exists")
    inspection = inspect_zip(zip_path, expected_comment)
    if not inspection["safe"]:
        raise ReproductionError("unsafe archive entries refused; nothing extracted")
    prefix = inspection["prefix"]
    dest.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = info.filename
            stripped = name.rstrip("/")
            parts = stripped.split("/")
            if len(parts) == 1:
                continue  # single prefix root entry
            rel = "/".join(parts[1:])
            target = dest / rel
            if name.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "xb") as out:
                for chunk in iter(lambda: src.read(1 << 20), b""):
                    out.write(chunk)
    return {"dest": str(dest), "prefix": prefix, "extracted": inspection["files"],
            "tree_manifest_sha256": inspection["tree_manifest_sha256"]}


def manifest_tree(root):
    root = Path(root)
    if not root.is_dir():
        raise ReproductionError("package root is not a directory")
    entries = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ReproductionError(f"symlink in package: {path}")
        if path.is_file():
            rel = path.relative_to(root).as_posix()
            entries.append({"path": rel, "bytes": path.stat().st_size, "sha256": _sha_file(path)})
    return entries


def verify_tree(root, entries):
    root = Path(root)
    actual = {e["path"]: e for e in manifest_tree(root)}
    expected = {e["path"]: e for e in entries}
    problems = []
    for path in sorted(set(expected) - set(actual)):
        problems.append({"path": path, "problem": "missing"})
    for path in sorted(set(actual) - set(expected)):
        problems.append({"path": path, "problem": "unexpected"})
    for path in sorted(set(expected) & set(actual)):
        if (expected[path]["bytes"] != actual[path]["bytes"]
                or expected[path]["sha256"] != actual[path]["sha256"]):
            problems.append({"path": path, "problem": "identity_mismatch"})
    return {"root": str(root), "entries": len(actual), "problems": problems,
            "ok": not problems}


def recursive_subset(expected, actual, path=""):
    """Every supplied expected value must match; extra actual fields are allowed."""
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [{"path": path or "<root>", "expected": expected, "actual": actual}]
        diffs = []
        for key, value in expected.items():
            if key not in actual:
                diffs.append({"path": f"{path}/{key}", "problem": "missing"})
            else:
                diffs.extend(recursive_subset(value, actual[key], f"{path}/{key}"))
        return diffs
    if expected != actual:
        return [{"path": path, "expected": expected, "actual": actual}]
    return []


def exclusion_scan(paths, allowed_fixture_paths=()):
    """Flag private/secret-looking package paths; explicit fixtures may be allowlisted."""
    allowed = set(allowed_fixture_paths)
    violations = []
    for raw in paths:
        rel = raw.replace("\\", "/")
        if rel in allowed:
            continue
        parts = [p for p in rel.split("/") if p]
        lowered = [p.lower() for p in parts]
        name = lowered[-1] if lowered else ""
        reason = None
        if any(p in FORBIDDEN_PARTS for p in lowered):
            reason = "private_or_scratch_component"
        elif name in FORBIDDEN_NAMES:
            reason = "credential_or_budget_file"
        elif name.endswith(SECRET_SUFFIXES):
            reason = "database_or_key_suffix"
        elif any(word in name for word in SECRET_WORDS):
            reason = "secret_like_name"
        if reason:
            violations.append({"path": rel, "reason": reason})
    return {"checked": len(paths), "violations": violations,
            "ok": not violations,
            "note": "Heuristic path check only; not a full secret scanner."}

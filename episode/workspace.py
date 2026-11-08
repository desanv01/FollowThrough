"""Safe, host-only workspace reads and candidate replacement for episode control.

Workspace files are data, including Python source; nothing here imports, executes,
evaluates or shells out to them.  The workspace is a flat allowlisted directory whose
files and path components are checked for symlinks/reparse points top-down before
any descendant test or read.  Candidate edits use a staging file in the protected
output directory and an atomic replace, with before/after hashes returned to the
controller.  Output, checkpoints and controller records live outside the workspace.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat


ALLOWED_NAMES = frozenset({
    "processor.py", "preview.py", "public_checks.py",
    "TASK.md", "SPEC.md", "WORKFLOW.md", "HISTORY.json",
})
CANDIDATE_NAMES = ("processor.py", "preview.py")
REQUIRED_NAMES = ALLOWED_NAMES - frozenset(CANDIDATE_NAMES)
MAX_FILE_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024
MAX_READ_BYTES = 32768
MAX_EDIT_BYTES = 65536
MAX_SPEC_BYTES = 1024 * 1024
_FILE_ATTRIBUTE_REPARSE_POINT = getattr(
    stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400
)
# Guard refusals are distinct from staging/replace I/O infrastructure failures.
POLICY_ERRORS = frozenset({
    "filename_not_allowed",
    "filename_not_writable",
    "candidate_filename_mismatch",
    "content_not_utf8",
    "edit_too_large",
    "expected_source_hash_mismatch",
    "read_too_large",
    "read_not_utf8",
    "file_too_large",
    "workspace_too_large",
    "source_changed_during_edit",
    "spec_too_large",
})


class WorkspaceError(Exception):
    """Fixed workspace/tool error carrying only a bounded code."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _is_reparse(metadata):
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0)
        & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def assert_no_reparse_chain(path):
    """Check every existing component top-down before using the final path."""
    current = os.path.abspath(path)
    components = []
    while True:
        components.append(current)
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    for component in reversed(components):
        try:
            metadata = os.lstat(component)
        except FileNotFoundError:
            continue
        except OSError:
            raise WorkspaceError("path_metadata_failed") from None
        if _is_reparse(metadata):
            raise WorkspaceError("reparse_component")


def _regular_file(path):
    try:
        metadata = os.lstat(path)
    except OSError:
        raise WorkspaceError("file_metadata_failed") from None
    if _is_reparse(metadata):
        raise WorkspaceError("reparse_component")
    if not stat.S_ISREG(metadata.st_mode):
        raise WorkspaceError("not_regular_file")
    return metadata


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


class EpisodeWorkspace:
    """Validated agent workspace plus a fresh protected output directory."""

    def __init__(self, workspace, output_dir):
        workspace = os.fsdecode(workspace)
        output_dir = os.fsdecode(output_dir)
        if not os.path.isabs(workspace) or not os.path.isabs(output_dir):
            raise WorkspaceError("relative_path")
        workspace = os.path.normpath(workspace)
        output_dir = os.path.normpath(output_dir)
        if os.path.normcase(workspace) == os.path.normcase(output_dir):
            raise WorkspaceError("workspace_output_equal")
        if self._contains(output_dir, workspace) or self._contains(workspace, output_dir):
            raise WorkspaceError("workspace_output_overlap")
        # Top-down component guards run before any directory predicate, listing,
        # read or output creation.
        assert_no_reparse_chain(workspace)
        assert_no_reparse_chain(output_dir)
        if not os.path.isdir(workspace):
            raise WorkspaceError("workspace_missing")
        parent = os.path.dirname(output_dir)
        assert_no_reparse_chain(parent)
        if not os.path.isdir(parent):
            raise WorkspaceError("output_parent_missing")
        if os.path.exists(output_dir):
            raise WorkspaceError("output_exists")
        try:
            os.mkdir(output_dir)
        except OSError:
            raise WorkspaceError("output_create_failed") from None
        assert_no_reparse_chain(output_dir)
        self.workspace = workspace
        self.output_dir = output_dir
        self.events_path = os.path.join(output_dir, "events.jsonl")
        self.result_path = os.path.join(output_dir, "result.json")
        self._staging_counter = 0
        self.inventory = self._validate_inventory()
        self.candidate_filename = self._candidate_filename()
        self.initial_candidate_sha256 = self.inventory[self.candidate_filename][1]

    def revalidate(self):
        """Recheck allowlist/reparse/size state and return the actual candidate hash."""
        self._refresh()
        return self.current_sha256(self.candidate_filename)

    @staticmethod
    def _contains(ancestor, descendant):
        ancestor = os.path.normcase(os.path.abspath(ancestor))
        descendant = os.path.normcase(os.path.abspath(descendant))
        try:
            return os.path.commonpath([ancestor, descendant]) == ancestor
        except ValueError:
            return False

    def _validate_inventory(self):
        assert_no_reparse_chain(self.workspace)
        try:
            names = sorted(os.listdir(self.workspace))
        except OSError:
            raise WorkspaceError("workspace_list_failed") from None
        name_set = set(names)
        if not name_set <= ALLOWED_NAMES:
            raise WorkspaceError("workspace_names_invalid")
        if not REQUIRED_NAMES <= name_set:
            raise WorkspaceError("workspace_names_invalid")
        inventory = {}
        total = 0
        for name in names:
            path = os.path.join(self.workspace, name)
            metadata = _regular_file(path)
            if metadata.st_size > MAX_FILE_BYTES:
                raise WorkspaceError("file_too_large")
            total += metadata.st_size
            try:
                with open(path, "rb") as handle:
                    data = handle.read(MAX_FILE_BYTES + 1)
            except OSError:
                raise WorkspaceError("file_read_failed") from None
            if len(data) != metadata.st_size:
                raise WorkspaceError("file_changed")
            inventory[name] = (data, _sha256(data))
        if total > MAX_TOTAL_BYTES:
            raise WorkspaceError("workspace_too_large")
        return inventory

    def _candidate_filename(self):
        present = [name for name in CANDIDATE_NAMES if name in self.inventory]
        if len(present) != 1:
            raise WorkspaceError("candidate_ambiguous")
        return present[0]

    def _refresh(self):
        """Revalidate names/regular files/reparse chains before a read or edit."""
        assert_no_reparse_chain(self.workspace)
        try:
            names = sorted(os.listdir(self.workspace))
        except OSError:
            raise WorkspaceError("workspace_list_failed") from None
        name_set = set(names)
        if not name_set <= ALLOWED_NAMES:
            raise WorkspaceError("workspace_names_invalid")
        if not REQUIRED_NAMES <= name_set:
            raise WorkspaceError("workspace_names_invalid")
        sizes = {}
        for name in names:
            path = os.path.join(self.workspace, name)
            metadata = _regular_file(path)
            if metadata.st_size > MAX_FILE_BYTES:
                raise WorkspaceError("file_too_large")
            sizes[name] = metadata.st_size
        if sum(sizes.values()) > MAX_TOTAL_BYTES:
            raise WorkspaceError("workspace_too_large")
        self.inventory = {
            name: (self.inventory[name][0], self.inventory[name][1])
            for name in names
        }
        return sizes

    def current_bytes(self, filename):
        if filename not in ALLOWED_NAMES:
            raise WorkspaceError("filename_not_allowed")
        assert_no_reparse_chain(self.workspace)
        path = os.path.join(self.workspace, filename)
        metadata = _regular_file(path)
        if metadata.st_size > MAX_FILE_BYTES:
            raise WorkspaceError("file_too_large")
        try:
            with open(path, "rb") as handle:
                data = handle.read(MAX_FILE_BYTES + 1)
        except OSError:
            raise WorkspaceError("file_read_failed") from None
        if len(data) != metadata.st_size:
            raise WorkspaceError("file_changed")
        return data

    def current_sha256(self, filename):
        return _sha256(self.current_bytes(filename))

    def read(self, filename):
        """Return bounded UTF-8 text for one exact allowlisted filename."""
        if filename not in ALLOWED_NAMES:
            raise WorkspaceError("filename_not_allowed")
        data = self.current_bytes(filename)
        if len(data) > MAX_READ_BYTES:
            raise WorkspaceError("read_too_large")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise WorkspaceError("read_not_utf8") from None
        return text

    def replace_candidate(self, filename, content, expected_sha256):
        """Host-only W1 candidate replacement with stale-hash and size guards."""
        if filename not in CANDIDATE_NAMES:
            raise WorkspaceError("filename_not_writable")
        if filename != self.candidate_filename:
            raise WorkspaceError("candidate_filename_mismatch")
        try:
            payload = content.encode("utf-8")
        except UnicodeEncodeError:
            raise WorkspaceError("content_not_utf8") from None
        if len(payload) > MAX_EDIT_BYTES:
            raise WorkspaceError("edit_too_large")
        before = self.current_bytes(filename)
        before_hash = _sha256(before)
        if expected_sha256 != before_hash:
            raise WorkspaceError("expected_source_hash_mismatch")
        sizes = self._refresh()
        prospective = dict(sizes)
        prospective[filename] = len(payload)
        if prospective[filename] > MAX_FILE_BYTES:
            raise WorkspaceError("file_too_large")
        if sum(prospective.values()) > MAX_TOTAL_BYTES:
            raise WorkspaceError("workspace_too_large")
        staging = self._stage(payload)
        target = os.path.join(self.workspace, filename)
        assert_no_reparse_chain(self.workspace)
        metadata = _regular_file(target)
        if metadata.st_size != len(before):
            raise WorkspaceError("source_changed_during_edit")
        try:
            os.replace(staging, target)
        except OSError:
            # Staging remains owned evidence; the original was never partially written.
            raise WorkspaceError("replace_failed") from None
        after = self.current_bytes(filename)
        after_hash = _sha256(after)
        self.inventory[filename] = (after, after_hash)
        return {"before_sha256": before_hash, "after_sha256": after_hash,
                "bytes": len(after)}

    def replace_spec(self, content):
        """Controller-only requirements SPEC replacement with inventory bounds."""
        try:
            payload = content.encode("utf-8")
        except UnicodeEncodeError:
            raise WorkspaceError("content_not_utf8") from None
        if len(payload) > MAX_SPEC_BYTES:
            raise WorkspaceError("spec_too_large")
        sizes = self._refresh()
        prospective = dict(sizes)
        prospective["SPEC.md"] = len(payload)
        if prospective["SPEC.md"] > MAX_FILE_BYTES:
            raise WorkspaceError("file_too_large")
        if sum(prospective.values()) > MAX_TOTAL_BYTES:
            raise WorkspaceError("workspace_too_large")
        staging = self._stage(payload)
        target = os.path.join(self.workspace, "SPEC.md")
        assert_no_reparse_chain(self.workspace)
        metadata = _regular_file(target)
        if metadata.st_size != sizes["SPEC.md"]:
            raise WorkspaceError("source_changed_during_edit")
        try:
            os.replace(staging, target)
        except OSError:
            raise WorkspaceError("replace_failed") from None
        data = self.current_bytes("SPEC.md")
        digest = _sha256(data)
        self.inventory["SPEC.md"] = (data, digest)
        return {"sha256": digest, "bytes": len(data)}

    def _stage(self, payload):
        self._staging_counter += 1
        name = "staging-%06d.py" % self._staging_counter
        path = os.path.join(self.output_dir, name)
        assert_no_reparse_chain(self.output_dir)
        try:
            handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except OSError:
            raise WorkspaceError("staging_failed") from None
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            raise WorkspaceError("staging_write_failed") from None
        return path

    def write_checkpoint(self, sequence, data):
        """Write candidate bytes to cp-<seq>.py outside the workspace, exclusively."""
        name = "cp-%d.py" % int(sequence)
        path = os.path.join(self.output_dir, name)
        assert_no_reparse_chain(self.output_dir)
        try:
            handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except OSError:
            raise WorkspaceError("checkpoint_failed") from None
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            raise WorkspaceError("checkpoint_write_failed") from None
        return path, name, _sha256(data)

    def write_result(self, result):
        assert_no_reparse_chain(self.output_dir)
        try:
            with open(self.result_path, "x", encoding="utf-8", newline="\n") as handle:
                json.dump(result, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError:
            raise WorkspaceError("result_write_failed") from None


__all__ = [
    "ALLOWED_NAMES",
    "CANDIDATE_NAMES",
    "EpisodeWorkspace",
    "POLICY_ERRORS",
    "WorkspaceError",
    "assert_no_reparse_chain",
]

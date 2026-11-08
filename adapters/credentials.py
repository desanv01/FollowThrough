"""Bounded credential loading for the project's DeepSeek adapter.

The loader reads either an explicitly supplied environment mapping or the
single ``.env.local`` file of the explicit project root.  It never searches
parent directories, never uses the current working directory implicitly and
never mutates the process environment.  An empty or NUL-containing root is
rejected before any file fallback.  The returned key is a secret handed only to
its trusted caller; all loader diagnostics are fixed codes that never include
file contents, environment values, raw path errors or OS error strings, and the
error paths raise outside the raw handler so no file bytes are retained through
``__context__``.
"""

import os


ENV_NAME = "DEEPSEEK_API_KEY"
ENV_FILE_NAME = ".env.local"
MAX_FILE_BYTES = 16 * 1024
MAX_KEY_CHARS = 4096

_ABSENT = object()
_NAME_FIRST = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_")
_NAME_REST = _NAME_FIRST | set("0123456789")


class CredentialError(Exception):
    """Credential failure carrying only a fixed diagnostic code."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _valid_key(value):
    return (
        type(value) is str
        and 0 < len(value) <= MAX_KEY_CHARS
        and all(33 <= ord(char) <= 126 for char in value)
    )


def _valid_name(name):
    return bool(name) and name[0] in _NAME_FIRST and all(char in _NAME_REST for char in name[1:])


def _decoded_root(project_root):
    decoded = None
    invalid = False
    try:
        decoded = os.fsdecode(project_root)
    except (TypeError, ValueError):
        invalid = True
    if not invalid and (decoded == "" or "\x00" in decoded):
        invalid = True
    if invalid:
        raise CredentialError("credential_root_invalid")
    return decoded


def _unquote(value):
    """Remove one pair of matching quotes; never process escapes or expansions."""
    if value[:1] in ("'", '"'):
        quote = value[0]
        if len(value) < 2 or value[-1] != quote:
            raise CredentialError("credential_file_malformed")
        return value[1:-1]
    return value


def _parse_env_file(text):
    """Parse the documented literal subset and return the single target value."""
    target = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            raise CredentialError("credential_file_malformed")
        raw_name, raw_value = stripped.split("=", 1)
        name = raw_name.strip()
        if not _valid_name(name):
            raise CredentialError("credential_file_malformed")
        value = _unquote(raw_value.strip())
        if name != ENV_NAME:
            continue
        if target is not None:
            raise CredentialError("credential_file_duplicate")
        target = value
    if target is None:
        raise CredentialError("credential_missing")
    if target == "":
        raise CredentialError("credential_empty")
    if not _valid_key(target):
        raise CredentialError("credential_invalid")
    return target


def _read_project_env_file(project_root):
    root = _decoded_root(project_root)
    path = os.path.join(root, ENV_FILE_NAME)
    payload = None
    read_error = None
    try:
        with open(path, "rb") as handle:
            payload = handle.read(MAX_FILE_BYTES + 1)
    except FileNotFoundError:
        read_error = "credential_missing"
    except (OSError, ValueError):
        read_error = "credential_file_unreadable"
    if read_error is not None:
        raise CredentialError(read_error)
    if len(payload) > MAX_FILE_BYTES:
        raise CredentialError("credential_file_too_large")
    text = None
    decode_error = None
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        decode_error = "credential_file_encoding"
    if decode_error is not None:
        raise CredentialError(decode_error)
    return _parse_env_file(text)


def _environment_entry(environ):
    present = False
    membership_error = None
    try:
        present = ENV_NAME in environ
    except TypeError:
        membership_error = "credential_environment_invalid"
    if membership_error is not None:
        raise CredentialError(membership_error)
    if not present:
        return _ABSENT
    value = None
    lookup_error = None
    try:
        value = environ[ENV_NAME]
    except (KeyError, TypeError):
        lookup_error = "credential_environment_invalid"
    if lookup_error is not None:
        raise CredentialError(lookup_error)
    return value


def load_deepseek_key(project_root, *, environ=None):
    """Return the validated DeepSeek key for one explicit project root.

    An explicitly present ``DEEPSEEK_API_KEY`` entry takes precedence.  It is
    validated strictly and never falls back to the project file.  Otherwise
    only ``<project_root>/.env.local`` is consulted, and the root must be a
    nonempty decoded path without embedded NUL characters.
    """
    environment = os.environ if environ is None else environ
    supplied = _environment_entry(environment)
    if supplied is not _ABSENT:
        if type(supplied) is not str:
            raise CredentialError("credential_invalid")
        if supplied == "":
            raise CredentialError("credential_empty")
        if not _valid_key(supplied):
            raise CredentialError("credential_invalid")
        return supplied
    return _read_project_env_file(project_root)


__all__ = ["CredentialError", "ENV_FILE_NAME", "ENV_NAME", "MAX_FILE_BYTES", "load_deepseek_key"]

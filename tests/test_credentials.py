"""Offline tests for the bounded project credential loader.

Every root, environment mapping and key here is synthetic.  The real project
``.env.local`` is never opened and no test asserts on a real secret value.
"""

import io
import os
from collections import defaultdict
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import shutil
import traceback
import unittest
from unittest.mock import patch
import uuid

from adapters import credentials


OVERLAY_ROOT = Path(__file__).resolve().parents[1]
SCRATCH_ROOT = OVERLAY_ROOT / "tmp"
SYNTHETIC_ENV_KEY = "synthetic-env-key-AAAA1111"
SYNTHETIC_FILE_KEY = "synthetic-file-key-BBBB2222"
SYNTHETIC_OTHER_KEY = "synthetic-other-key-CCCC3333"


class CredentialLoaderTest(unittest.TestCase):
    def setUp(self):
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        self.root = SCRATCH_ROOT / ("credentials-" + uuid.uuid4().hex[:12])
        os.makedirs(self.root, exist_ok=False)
        self.addCleanup(shutil.rmtree, self.root, True)

    def write_env(self, content, *, path=None):
        target = self.root / ".env.local" if path is None else Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
        return target

    def assert_key(self, actual, expected):
        self.assertTrue(actual == expected, "loader returned an unexpected synthetic key")

    def assert_error(self, code, *, environ=None, root=None):
        that_root = self.root if root is None else root
        with self.assertRaises(credentials.CredentialError) as raised:
            credentials.load_deepseek_key(
                that_root, environ={} if environ is None else environ
            )
        self.assertEqual(raised.exception.code, code)
        self.assertEqual(str(raised.exception), code)
        self.assertFalse(
            SYNTHETIC_ENV_KEY in repr(raised.exception)
            or SYNTHETIC_FILE_KEY in repr(raised.exception),
            "credential error representation leaked a synthetic key",
        )
        return raised.exception

    def test_environment_entry_takes_precedence_over_project_file(self):
        self.write_env(f"DEEPSEEK_API_KEY={SYNTHETIC_FILE_KEY}\n")
        key = credentials.load_deepseek_key(
            self.root, environ={"DEEPSEEK_API_KEY": SYNTHETIC_ENV_KEY}
        )
        self.assert_key(key, SYNTHETIC_ENV_KEY)

    def test_present_invalid_environment_entry_never_falls_back(self):
        self.write_env(f"DEEPSEEK_API_KEY={SYNTHETIC_FILE_KEY}\n")
        cases = (
            ("", "credential_empty"),
            ("short key", "credential_invalid"),
            ("line\nbreak", "credential_invalid"),
            ("synthetic-\u00e9-key", "credential_invalid"),
            ("x" * (credentials.MAX_KEY_CHARS + 1), "credential_invalid"),
            (None, "credential_invalid"),
            (123, "credential_invalid"),
        )
        for value, code in cases:
            with self.subTest(expected=code):
                self.assert_error(code, environ={"DEEPSEEK_API_KEY": value})

    def test_missing_environment_and_missing_file(self):
        self.assert_error("credential_missing")

    def test_file_without_target_assignment(self):
        self.write_env(f"OTHER_NAME={SYNTHETIC_OTHER_KEY}\n")
        self.assert_error("credential_missing")

    def test_empty_target_value(self):
        self.write_env("DEEPSEEK_API_KEY=\n")
        self.assert_error("credential_empty")
        self.write_env("DEEPSEEK_API_KEY=''\n")
        self.assert_error("credential_empty")

    def test_duplicate_target_assignment(self):
        self.write_env(
            f"DEEPSEEK_API_KEY={SYNTHETIC_ENV_KEY}\nDEEPSEEK_API_KEY={SYNTHETIC_FILE_KEY}\n"
        )
        self.assert_error("credential_file_duplicate")

    def test_malformed_lines_are_rejected(self):
        malformed = (
            "DEEPSEEK_API_KEY\n",
            "=value\n",
            "1BAD=value\n",
            "DEEPSEEK_API_KEY='unterminated\n",
            "export DEEPSEEK_API_KEY=value\n",
            "DEEPSEEK_API KEY=value\n",
        )
        for content in malformed:
            with self.subTest(content=content.replace(SYNTHETIC_ENV_KEY, "<key>")):
                file = self.write_env(content)
                self.assert_error("credential_file_malformed")
                file.unlink()

    def test_oversized_file_is_rejected_without_parsing(self):
        target = self.write_env(f"DEEPSEEK_API_KEY={SYNTHETIC_FILE_KEY}\n")
        oversized = target.read_bytes() + b"#" + b"p" * (credentials.MAX_FILE_BYTES + 8)
        target.write_bytes(oversized)
        self.assertGreater(len(oversized), credentials.MAX_FILE_BYTES)
        self.assert_error("credential_file_too_large")

    def test_exact_file_size_limit_is_accepted(self):
        line = f"DEEPSEEK_API_KEY={SYNTHETIC_FILE_KEY}\n".encode("ascii")
        padding = b"#" + b"p" * (credentials.MAX_FILE_BYTES - len(line) - 2) + b"\n"
        payload = line + padding
        self.assertEqual(len(payload), credentials.MAX_FILE_BYTES)
        self.write_env(payload)
        self.assert_key(
            credentials.load_deepseek_key(self.root, environ={}), SYNTHETIC_FILE_KEY
        )

    def test_non_utf8_file_is_rejected(self):
        self.write_env(b"DEEPSEEK_API_KEY=\xff\xfe-synthetic\n")
        self.assert_error("credential_file_encoding")

    def test_optional_bom_and_crlf_are_accepted(self):
        self.write_env(
            b"\xef\xbb\xbf# synthetic comment\r\n"
            b"OTHER_NAME=synthetic-other\r\n"
            + f"DEEPSEEK_API_KEY={SYNTHETIC_FILE_KEY}\r\n".encode("ascii")
        )
        self.assert_key(
            credentials.load_deepseek_key(self.root, environ={}), SYNTHETIC_FILE_KEY
        )

    def test_matching_quotes_are_delimiters_not_content(self):
        for content in (
            f"DEEPSEEK_API_KEY='{SYNTHETIC_FILE_KEY}'\n",
            f'DEEPSEEK_API_KEY="{SYNTHETIC_FILE_KEY}"\n',
            f"  DEEPSEEK_API_KEY  =  {SYNTHETIC_FILE_KEY}  \n",
        ):
            with self.subTest(single_quoted=content.startswith("DEEPSEEK_API_KEY='")):
                self.write_env(content)
                self.assert_key(
                    credentials.load_deepseek_key(self.root, environ={}),
                    SYNTHETIC_FILE_KEY,
                )

    def test_whitespace_inside_a_quoted_value_is_invalid(self):
        for content in (
            f"DEEPSEEK_API_KEY='  {SYNTHETIC_FILE_KEY}  '\n",
            f'DEEPSEEK_API_KEY="{SYNTHETIC_FILE_KEY} "\n',
        ):
            with self.subTest(trailing_space=True):
                self.write_env(content)
                self.assert_error("credential_invalid")

    def test_values_are_literal_with_no_expansion_or_escape_processing(self):
        literal = "${SYNTHETIC_EXPANSION}"
        self.write_env(f"DEEPSEEK_API_KEY='{literal}'\n")
        self.assert_key(credentials.load_deepseek_key(self.root, environ={}), literal)

        self.write_env("DEEPSEEK_API_KEY=\"backslash\\nsequence\"\n")
        self.assert_key(
            credentials.load_deepseek_key(self.root, environ={}),
            "backslash\\nsequence",
        )

    def test_unrelated_assignments_and_whole_line_comments_are_ignored(self):
        self.write_env(
            "# full-line comment\n\n"
            "OTHER_NAME=synthetic-other\n"
            "ANOTHER_ONE='value with spaces'\n"
            f"DEEPSEEK_API_KEY={SYNTHETIC_FILE_KEY}\n"
        )
        self.assert_key(
            credentials.load_deepseek_key(self.root, environ={}), SYNTHETIC_FILE_KEY
        )

    def test_no_parent_or_implicit_cwd_lookup(self):
        self.write_env(f"DEEPSEEK_API_KEY={SYNTHETIC_FILE_KEY}\n")
        child = self.root / "nested" / "project"
        child.mkdir(parents=True)
        self.assert_error("credential_missing", root=child)

        sibling = self.root / "sibling"
        sibling.mkdir()
        previous = os.getcwd()
        try:
            os.chdir(self.root)
            self.assert_error("credential_missing", root=sibling)
        finally:
            os.chdir(previous)

    def test_loader_never_mutates_an_injected_mapping(self):
        self.write_env(f"DEEPSEEK_API_KEY={SYNTHETIC_FILE_KEY}\n")
        mapping = {"UNRELATED": "synthetic"}
        original = dict(mapping)
        self.assert_key(
            credentials.load_deepseek_key(self.root, environ=mapping),
            SYNTHETIC_FILE_KEY,
        )
        self.assertTrue(mapping == original, "loader mutated the injected mapping")

    def test_default_environment_is_read_without_mutation(self):
        with patch.dict(
            os.environ, {"DEEPSEEK_API_KEY": SYNTHETIC_ENV_KEY}, clear=False
        ):
            before = dict(os.environ)
            self.assert_key(
                credentials.load_deepseek_key(self.root), SYNTHETIC_ENV_KEY
            )
            self.assertTrue(
                dict(os.environ) == before,
                "loader mutated the process environment mapping",
            )

    def test_file_read_errors_and_invalid_root_are_fixed_codes(self):
        (self.root / ".env.local").mkdir()
        self.assert_error("credential_file_unreadable")
        self.assert_error("credential_root_invalid", root=123)

    def test_loader_emits_no_diagnostics(self):
        self.write_env(f"DEEPSEEK_API_KEY={SYNTHETIC_FILE_KEY}\n")
        with redirect_stdout(io.StringIO()) as stdout, redirect_stderr(
            io.StringIO()
        ) as stderr:
            key = credentials.load_deepseek_key(self.root, environ={})
        self.assert_key(key, SYNTHETIC_FILE_KEY)
        self.assertFalse(
            SYNTHETIC_FILE_KEY in stdout.getvalue() + stderr.getvalue(),
            "loader emitted a synthetic key",
        )

    def test_empty_or_nul_root_is_rejected_before_cwd_fallback(self):
        self.write_env(f"DEEPSEEK_API_KEY={SYNTHETIC_FILE_KEY}\n")
        previous = os.getcwd()
        try:
            os.chdir(self.root)
            for root in ("", b"", "\x00synthetic-root", "synthetic\x00root"):
                with self.subTest(root_kind=type(root).__name__):
                    with self.assertRaises(credentials.CredentialError) as raised:
                        credentials.load_deepseek_key(root, environ={})
                    error = raised.exception
                    self.assertEqual(error.code, "credential_root_invalid")
                    self.assertIsNone(error.__context__)
                    self.assertIsNone(error.__cause__)
                    self.assertFalse(
                        SYNTHETIC_FILE_KEY in repr(error),
                        "root diagnostic leaked a synthetic key",
                    )
        finally:
            os.chdir(previous)

    def test_invalid_root_types_use_fixed_errors_without_chaining(self):
        for root in (None, 123, object()):
            with self.subTest(root_type=type(root).__name__):
                with self.assertRaises(credentials.CredentialError) as raised:
                    credentials.load_deepseek_key(root, environ={})
                self.assertEqual(raised.exception.code, "credential_root_invalid")
                self.assertIsNone(raised.exception.__context__)
                self.assertIsNone(raised.exception.__cause__)

    def test_defaultdict_membership_does_not_mutate_or_mask_fallback(self):
        self.write_env(f"DEEPSEEK_API_KEY={SYNTHETIC_FILE_KEY}\n")
        mapping = defaultdict(list)
        key = credentials.load_deepseek_key(self.root, environ=mapping)
        self.assert_key(key, SYNTHETIC_FILE_KEY)
        self.assertFalse(
            credentials.ENV_NAME in mapping,
            "loader inserted a missing key into a defaultdict",
        )
        self.assertTrue(len(mapping) == 0, "loader mutated the injected mapping")
        mapping[credentials.ENV_NAME] = SYNTHETIC_ENV_KEY
        self.assert_key(
            credentials.load_deepseek_key(self.root, environ=mapping),
            SYNTHETIC_ENV_KEY,
        )
        self.assertTrue(len(mapping) == 1, "loader mutated a populated mapping")

    def test_encoding_and_read_errors_do_not_chain_raw_file_bytes(self):
        marker = SYNTHETIC_FILE_KEY
        self.write_env(
            f"DEEPSEEK_API_KEY={marker}".encode("ascii") + b"\xff\xfe\xff"
        )
        with self.assertRaises(credentials.CredentialError) as raised:
            credentials.load_deepseek_key(self.root, environ={})
        error = raised.exception
        self.assertEqual(error.code, "credential_file_encoding")
        self.assertIsNone(error.__context__)
        self.assertIsNone(error.__cause__)
        rendered = "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        )
        self.assertFalse(
            marker in rendered, "rendered credential error leaked a synthetic marker"
        )
        self.assertFalse(marker in repr(error))

        (self.root / ".env.local").unlink()
        (self.root / ".env.local").mkdir()
        with self.assertRaises(credentials.CredentialError) as read_raised:
            credentials.load_deepseek_key(self.root, environ={})
        self.assertEqual(read_raised.exception.code, "credential_file_unreadable")
        self.assertIsNone(read_raised.exception.__context__)
        self.assertIsNone(read_raised.exception.__cause__)


if __name__ == "__main__":
    unittest.main()

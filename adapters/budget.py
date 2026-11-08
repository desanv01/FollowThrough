"""Durable shared campaign spending reservations.

The ledger lives at exactly ``<project_root>/.coordination/provider-budget.sqlite3``.
It stores integer microdollars and one immutable campaign cap, and never refunds,
resets, tops up or retries.

Initialization is only legal for a genuinely new canonical file, claimed with an
exclusive create.  Existing files are validated read-only and refused unless they
are a complete ledger with the constructor's campaign and normalized cap.  Every
authorizing or mutating transaction follows read-only preflight and re-validates
persisted schema, metadata and operations under the write lock. Operation
connections use SQLite's ``mode=rw`` URI flag, so a missing database is never
silently recreated. Only rollback-journal databases on stable, owned paths are
supported.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Context, Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, localcontext
import os
from pathlib import Path
import re
import sqlite3
import stat


LEDGER_RELATIVE_PATH = os.path.join(".coordination", "provider-budget.sqlite3")
SCHEMA_VERSION = 1
MAX_CAP_USD = Decimal("20")
MICRODOLLARS_PER_USD = 1_000_000
MAX_ID_LENGTH = 128
LOCK_TIMEOUT_SECONDS = 5.0
OPERATION_OUTCOMES = ("success", "provider_error", "uncertain", "internal_error")
PENDING = "pending"
_VALID_STATUSES = frozenset((PENDING,) + OPERATION_OUTCOMES)

_MAX_AMOUNT_STRING = 64
_MAX_AMOUNT_DIGITS = 40
_MAX_AMOUNT_ADJUSTED = 6
_MIN_AMOUNT_ADJUSTED = -30
_ID_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
_AMOUNT_PATTERN = re.compile(
    r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]{1,4})?\Z"
)
_TABLE_DDL = {
    "ledger_meta": (
        "CREATE TABLE ledger_meta ("
        " key TEXT PRIMARY KEY NOT NULL CHECK (typeof(key) = 'text'),"
        " value TEXT NOT NULL CHECK (typeof(value) = 'text')) WITHOUT ROWID"
    ),
    "operations": (
        "CREATE TABLE operations ("
        " operation_id TEXT PRIMARY KEY NOT NULL CHECK (typeof(operation_id) = 'text'),"
        " amount_micro INTEGER NOT NULL"
        "  CHECK (typeof(amount_micro) = 'integer' AND amount_micro > 0),"
        " status TEXT NOT NULL"
        "  CHECK (typeof(status) = 'text' AND status IN"
        "   ('pending', 'success', 'provider_error', 'uncertain', 'internal_error')),"
        " estimated_cost_micro INTEGER"
        "  CHECK (estimated_cost_micro IS NULL OR"
        "   (typeof(estimated_cost_micro) = 'integer' AND estimated_cost_micro >= 0)),"
        " created_at TEXT NOT NULL CHECK (typeof(created_at) = 'text'),"
        " finished_at TEXT) WITHOUT ROWID"
    ),
}
_SQLITE_HEADER = b"SQLite format 3\x00"
_WAL_SIDECARS = ("-wal", "-shm")

# Values from the SQLite C API, kept local so older sqlite3 bindings still map errors.
_BUSY_CODES = (5, 6)
_CORRUPT_CODES = (11, 26)
_UNAVAILABLE_CODES = (3, 8, 14, 23)


class BudgetError(Exception):
    """Budget failure carrying only a fixed diagnostic code."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _storage_error(failure):
    code = getattr(failure, "sqlite_errorcode", None)
    if code is not None:
        primary = code & 0xFF  # SQLite extended result codes OR a primary code into the low byte.
        if primary in _BUSY_CODES:
            return BudgetError("budget_storage_locked")
        if primary in _CORRUPT_CODES:
            return BudgetError("budget_storage_corrupt")
        if primary in _UNAVAILABLE_CODES:
            return BudgetError("budget_storage_unavailable")
    if isinstance(failure, OSError):
        return BudgetError("budget_storage_unavailable")
    if isinstance(failure, sqlite3.DatabaseError):
        return BudgetError("budget_storage_corrupt")
    return BudgetError("budget_storage_error")


def _raise_storage(failure):
    """Raise a fixed storage error at a clean boundary (no raw exception chain)."""
    raise _storage_error(failure)


def _schema_error(failure):
    """Classify a schema read failure as storage corruption or ledger damage."""
    code = getattr(failure, "sqlite_errorcode", None)
    if code is not None:
        primary = code & 0xFF
        if primary in _BUSY_CODES:
            return BudgetError("budget_storage_locked")
        if primary in _CORRUPT_CODES:
            return BudgetError("budget_storage_corrupt")
        if primary in _UNAVAILABLE_CODES:
            return BudgetError("budget_storage_unavailable")
    return BudgetError("budget_ledger_corrupt")


def _validate_id(value, code):
    if type(value) is not str or not _ID_PATTERN.fullmatch(value):
        raise BudgetError(code)
    return value


def _decimal_amount(value, code, *, allow_zero=False):
    """Parse a bounded Decimal-compatible amount without ever using binary floats."""
    if isinstance(value, Decimal):
        amount = value
    elif type(value) is str:
        if len(value) > _MAX_AMOUNT_STRING or not _AMOUNT_PATTERN.fullmatch(value):
            raise BudgetError(code)
        try:
            amount = Decimal(value)
        except (InvalidOperation, ValueError):
            raise BudgetError(code) from None
    else:
        raise BudgetError(code)
    if not amount.is_finite():
        raise BudgetError(code)
    digits = len(amount.as_tuple().digits)
    if (
        digits > _MAX_AMOUNT_DIGITS
        or amount.adjusted() > _MAX_AMOUNT_ADJUSTED
        or amount.adjusted() < _MIN_AMOUNT_ADJUSTED
    ):
        raise BudgetError(code)
    if allow_zero:
        if amount < 0:
            raise BudgetError(code)
    elif amount <= 0:
        raise BudgetError(code)
    return amount


def _floor_microdollars(amount):
    with localcontext(Context(prec=_MAX_AMOUNT_DIGITS + 20, rounding=ROUND_FLOOR)):
        return int(amount.scaleb(6).to_integral_value(rounding=ROUND_FLOOR))


def _ceil_microdollars(amount):
    with localcontext(Context(prec=_MAX_AMOUNT_DIGITS + 20, rounding=ROUND_CEILING)):
        return int(amount.scaleb(6).to_integral_value(rounding=ROUND_CEILING))


def _cap_microdollars(cap_usd):
    amount = _decimal_amount(cap_usd, "invalid_cap")
    if amount > MAX_CAP_USD:
        raise BudgetError("invalid_cap")
    microdollars = _floor_microdollars(amount)
    if microdollars <= 0:
        raise BudgetError("invalid_cap")
    return microdollars


def _reservation_microdollars(amount_usd):
    amount = _decimal_amount(amount_usd, "invalid_amount")
    microdollars = _ceil_microdollars(amount)
    if microdollars <= 0:
        raise BudgetError("invalid_amount")
    return microdollars


def _estimate_microdollars(amount_usd):
    if amount_usd is None:
        return None
    amount = _decimal_amount(amount_usd, "invalid_estimate", allow_zero=True)
    return _ceil_microdollars(amount)


def _usd(microdollars):
    with localcontext(Context(prec=_MAX_AMOUNT_DIGITS + 20)):
        return format(Decimal(microdollars).scaleb(-6), "f")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _project_root_path(project_root):
    decoded = None
    invalid = False
    try:
        decoded = os.fsdecode(project_root)
    except (TypeError, ValueError):
        invalid = True
    if not invalid and (decoded == "" or "\x00" in decoded):
        invalid = True
    if invalid:
        raise BudgetError("budget_root_invalid")
    return os.path.abspath(decoded)


class CampaignLedger:
    """One campaign's durable, conservative authorization ledger."""

    def __init__(self, project_root, *, campaign_id, cap_usd="20"):
        self._campaign_id = _validate_id(campaign_id, "invalid_campaign_id")
        self._cap_microdollars = _cap_microdollars(cap_usd)
        root = _project_root_path(project_root)
        if not os.path.isdir(root):
            raise BudgetError("budget_storage_unavailable")
        self._project_root = root
        self._path = os.path.join(root, LEDGER_RELATIVE_PATH)
        directory_created = False
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            directory_created = True
        except OSError:
            pass
        if not directory_created:
            raise BudgetError("budget_storage_unavailable")
        self._open_or_initialize()

    @property
    def ledger_path(self):
        """Bound absolute SQLite path; contains no credential or conversation data."""
        return self._path

    @property
    def project_root(self):
        return self._project_root

    @property
    def campaign_id(self):
        return self._campaign_id

    @property
    def cap_microdollars(self):
        return self._cap_microdollars

    # -- connection and schema validation ---------------------------------

    def _require_absent_sidecars(self, suffixes):
        for suffix in suffixes:
            try:
                os.lstat(self._path + suffix)
            except FileNotFoundError:
                continue
            except OSError:
                raise BudgetError("budget_storage_unavailable") from None
            raise BudgetError("budget_storage_unavailable")

    def _check_rollback_storage(self):
        # SQLite mode=ro can still create WAL sidecars. Check persistent file
        # format bytes 18 and 19 before giving SQLite access to the path.
        self._require_absent_sidecars(_WAL_SIDECARS)
        try:
            if not stat.S_ISREG(os.stat(self._path).st_mode):
                raise BudgetError("budget_storage_unavailable")
            with open(self._path, "rb") as database:
                header = database.read(20)
        except OSError:
            raise BudgetError("budget_storage_unavailable") from None
        if not header:
            # An exclusive initializer uses the empty file. Read-only admission
            # of an existing empty file still fails because no schema exists.
            return
        if len(header) != 20 or not header.startswith(_SQLITE_HEADER):
            raise BudgetError("budget_storage_corrupt")
        if header[18:20] != b"\x01\x01":
            raise BudgetError("budget_storage_unavailable")

    def _connect(self, *, read_only=False):
        self._check_rollback_storage()
        # Existing-file validation and snapshots use SQLite's real read-only mode.
        # A hot journal therefore yields SQLITE_READONLY_ROLLBACK instead of being
        # rolled back (which mode=rw + query_only would still do).
        mode = "ro" if read_only else "rw"
        uri = Path(self._path).as_uri() + "?mode=" + mode
        connection = None
        failure = None
        try:
            connection = sqlite3.connect(
                uri, uri=True, timeout=LOCK_TIMEOUT_SECONDS, isolation_level=None
            )
            connection.execute("PRAGMA busy_timeout = %d" % int(LOCK_TIMEOUT_SECONDS * 1000))
        except sqlite3.Error as problem:
            failure = problem
        if failure is not None:
            if connection is not None:
                connection.close()
            _raise_storage(failure)
        return connection

    def _create_schema(self, connection):
        for ddl in _TABLE_DDL.values():
            connection.execute(ddl)
        connection.executemany(
            "INSERT INTO ledger_meta (key, value) VALUES (?, ?)",
            (
                ("schema_version", str(SCHEMA_VERSION)),
                ("campaign_id", self._campaign_id),
                ("cap_micro", str(self._cap_microdollars)),
            ),
        )

    def _load_metadata_table(self, connection):
        try:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
        except sqlite3.Error as failure:
            raise _schema_error(failure) from None
        if "ledger_meta" not in tables:
            if not tables:
                # Blank file, unrelated non-SQLite file, or a first-open window
                # before the initializer committed its schema.  Never initialize it.
                raise BudgetError("budget_storage_not_ready")
            raise BudgetError("budget_ledger_unrecognized")
        if "operations" not in tables:
            raise BudgetError("budget_ledger_corrupt")
        self._validate_supported_schema(connection)

    def _validate_supported_schema(self, connection):
        """Require the canonical DDL and account for every persisted object."""
        try:
            objects = connection.execute(
                "SELECT type, name, tbl_name, sql FROM main.sqlite_master ORDER BY name"
            ).fetchall()
            expected = [
                ("table", table, table, _TABLE_DDL[table]) for table in sorted(_TABLE_DDL)
            ]
            if objects != expected:
                raise BudgetError("budget_schema_unsupported")
            # WITHOUT ROWID primary keys appear in index_list without a separate
            # sqlite_master entry. Each table has exactly its one PK autoindex.
            for table in _TABLE_DDL:
                indexes = connection.execute(f'PRAGMA main.index_list("{table}")').fetchall()
                if indexes != [(0, f"sqlite_autoindex_{table}_1", 1, "pk", 0)]:
                    raise BudgetError("budget_schema_unsupported")
        except sqlite3.Error as failure:
            raise _schema_error(failure) from None

    def _validate_metadata_values(self, connection):
        try:
            rows = connection.execute("SELECT key, value FROM ledger_meta").fetchall()
        except sqlite3.Error as failure:
            raise _schema_error(failure) from None
        metadata = {}
        for key, value in rows:
            if type(key) is not str or type(value) is not str or key in metadata:
                raise BudgetError("budget_ledger_corrupt")
            metadata[key] = value
        if set(metadata) != {"schema_version", "campaign_id", "cap_micro"}:
            raise BudgetError("budget_ledger_corrupt")
        if metadata["schema_version"] != str(SCHEMA_VERSION):
            raise BudgetError("budget_schema_mismatch")
        if metadata["campaign_id"] != self._campaign_id:
            raise BudgetError("budget_campaign_mismatch")
        if metadata["cap_micro"] != str(self._cap_microdollars):
            raise BudgetError("budget_cap_mismatch")

    def _validated_operations(self, connection):
        try:
            rows = connection.execute(
                "SELECT operation_id, amount_micro, status, estimated_cost_micro,"
                " typeof(operation_id), typeof(amount_micro), typeof(status),"
                " typeof(estimated_cost_micro) FROM operations"
                " ORDER BY created_at, operation_id"
            ).fetchall()
        except sqlite3.Error as failure:
            raise _schema_error(failure) from None
        operations = []
        for (
            operation_id, amount_micro, status, estimated_cost_micro,
            operation_type, amount_type, status_type, estimate_type,
        ) in rows:
            if (
                operation_type != "text"
                or type(operation_id) is not str
                or not _ID_PATTERN.fullmatch(operation_id)
            ):
                raise BudgetError("budget_ledger_corrupt")
            if amount_type != "integer" or type(amount_micro) is not int or amount_micro <= 0:
                raise BudgetError("budget_ledger_corrupt")
            if status_type != "text" or type(status) is not str or status not in _VALID_STATUSES:
                raise BudgetError("budget_ledger_corrupt")
            if estimated_cost_micro is not None and (
                estimate_type != "integer"
                or type(estimated_cost_micro) is not int
                or estimated_cost_micro < 0
            ):
                raise BudgetError("budget_ledger_corrupt")
            operations.append((operation_id, amount_micro, status, estimated_cost_micro))
        total = sum(item[1] for item in operations)
        if total > self._cap_microdollars:
            raise BudgetError("budget_ledger_corrupt")
        return operations

    def _validate_existing(self):
        connection = self._connect(read_only=True)
        try:
            try:
                connection.execute("BEGIN")
                self._load_metadata_table(connection)
                self._validate_metadata_values(connection)
                operations = self._validated_operations(connection)
                connection.execute("COMMIT")
            except BudgetError:
                _rollback(connection)
                raise
            except sqlite3.Error as failure:
                _rollback(connection)
                _raise_storage(failure)
            except BaseException:
                _rollback(connection)
                raise
        finally:
            connection.close()
        return operations

    def _initialize_new(self):
        connection = self._connect()
        try:
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'ledger_meta'"
                ).fetchone()
                if existing is None:
                    self._create_schema(connection)
                    self._validate_supported_schema(connection)
                else:
                    # Only an external writer could get here; validate rather than reset.
                    self._load_metadata_table(connection)
                    self._validate_metadata_values(connection)
                    self._validated_operations(connection)
                connection.execute("COMMIT")
            except BudgetError:
                _rollback(connection)
                raise
            except sqlite3.Error as failure:
                _rollback(connection)
                _raise_storage(failure)
            except BaseException:
                _rollback(connection)
                raise
        finally:
            connection.close()

    def _open_or_initialize(self):
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if not os.path.lexists(self._path):
            # A genuinely new ledger must not adopt an orphaned SQLite sidecar.
            try:
                self._require_absent_sidecars(_WAL_SIDECARS + ("-journal",))
            except BudgetError:
                if not os.path.lexists(self._path):
                    raise
                # Another initializer claimed the database during the check.
        claimed = False
        try:
            descriptor = os.open(self._path, flags, 0o600)
        except FileExistsError:
            claimed = False
        except OSError:
            claimed = None
        else:
            os.close(descriptor)
            claimed = True
        if claimed is None:
            raise BudgetError("budget_storage_unavailable")
        if claimed:
            self._initialize_new()
        else:
            self._validate_existing()

    @contextmanager
    def _write_transaction(self):
        # BEGIN IMMEDIATE on a writable connection can recover a hot journal
        # before any SQL validation. A real read-only preflight fails instead.
        self._validate_existing()
        connection = self._connect()
        try:
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._load_metadata_table(connection)
                self._validate_metadata_values(connection)
                operations = self._validated_operations(connection)
            except sqlite3.Error as failure:
                _rollback(connection)
                _raise_storage(failure)
            except BaseException:
                _rollback(connection)
                raise
            try:
                yield connection, operations
            except BaseException:
                _rollback(connection)
                raise
            try:
                connection.execute("COMMIT")
            except sqlite3.Error as failure:
                _rollback(connection)
                _raise_storage(failure)
        finally:
            connection.close()

    # -- public API --------------------------------------------------------

    def reserve(self, operation_id, amount_usd):
        """Atomically reserve one operation's full envelope, or fail closed."""
        operation_id = _validate_id(operation_id, "invalid_operation_id")
        amount_micro = _reservation_microdollars(amount_usd)
        with self._write_transaction() as (connection, operations):
            if any(item[0] == operation_id for item in operations):
                raise BudgetError("duplicate_operation")
            committed = sum(item[1] for item in operations)
            if committed + amount_micro > self._cap_microdollars:
                raise BudgetError("budget_cap_exceeded")
            try:
                connection.execute(
                    "INSERT INTO operations"
                    " (operation_id, amount_micro, status, estimated_cost_micro,"
                    "  created_at, finished_at)"
                    " VALUES (?, ?, ?, NULL, ?, NULL)",
                    (operation_id, amount_micro, PENDING, _now()),
                )
            except sqlite3.IntegrityError:
                existing_row = connection.execute(
                    "SELECT 1 FROM operations WHERE operation_id = ?", (operation_id,)
                ).fetchone()
                if existing_row is not None:
                    raise BudgetError("duplicate_operation") from None
                raise BudgetError("budget_reservation_not_persisted") from None
            except sqlite3.Error as failure:
                _raise_storage(failure)
            # A successful SQL call is not proof of a durable row.  Verify the exact
            # pending operation inside this transaction before commit/transport.
            try:
                persisted = connection.execute(
                    "SELECT operation_id, amount_micro, status, estimated_cost_micro,"
                    " typeof(operation_id), typeof(amount_micro), typeof(status),"
                    " typeof(estimated_cost_micro)"
                    " FROM operations WHERE operation_id = ?",
                    (operation_id,),
                ).fetchall()
            except sqlite3.Error as failure:
                _raise_storage(failure)
            if len(persisted) != 1:
                raise BudgetError("budget_reservation_not_persisted")
            row = persisted[0]
            if (
                row[0] != operation_id
                or row[1] != amount_micro
                or row[2] != PENDING
                or row[3] is not None
                or row[4] != "text"
                or row[5] != "integer"
                or row[6] != "text"
                or row[7] != "null"
            ):
                raise BudgetError("budget_reservation_not_persisted")
        return amount_micro

    def finish(self, operation_id, outcome, *, estimated_cost_usd=None):
        """Record one terminal outcome without reducing the reserved amount."""
        operation_id = _validate_id(operation_id, "invalid_operation_id")
        if type(outcome) is not str or outcome not in OPERATION_OUTCOMES:
            raise BudgetError("invalid_outcome")
        estimate_micro = _estimate_microdollars(estimated_cost_usd)
        with self._write_transaction() as (connection, operations):
            target = next(
                (item for item in operations if item[0] == operation_id), None
            )
            if target is None:
                raise BudgetError("unknown_operation")
            if target[2] != PENDING:
                raise BudgetError("operation_already_finished")
            updated = None
            failure = None
            try:
                updated = connection.execute(
                    "UPDATE operations"
                    " SET status = ?, estimated_cost_micro = ?, finished_at = ?"
                    " WHERE operation_id = ? AND status = ?",
                    (outcome, estimate_micro, _now(), operation_id, PENDING),
                )
            except sqlite3.Error as problem:
                failure = problem
            if failure is not None:
                _raise_storage(failure)
            if updated.rowcount != 1:
                raise BudgetError("operation_already_finished")
        return None

    def snapshot(self):
        """Return JSON-compatible safe metadata and exact microdollar amounts."""
        operations = self._validate_existing()
        entries = []
        reserved = 0
        for operation_id, amount_micro, status, estimate_micro in operations:
            reserved += amount_micro
            entries.append({
                "operation_id": operation_id,
                "amount_microdollars": amount_micro,
                "amount_usd": _usd(amount_micro),
                "status": status,
                "estimated_cost_microdollars": estimate_micro,
                "estimated_cost_usd": (
                    None if estimate_micro is None else _usd(estimate_micro)
                ),
            })
        return {
            "schema_version": SCHEMA_VERSION,
            "campaign_id": self._campaign_id,
            "cap_microdollars": self._cap_microdollars,
            "cap_usd": _usd(self._cap_microdollars),
            "reserved_microdollars": reserved,
            "reserved_usd": _usd(reserved),
            "remaining_microdollars": self._cap_microdollars - reserved,
            "remaining_usd": _usd(self._cap_microdollars - reserved),
            "operations": entries,
        }

    def __repr__(self):
        return (
            "CampaignLedger("
            f"campaign_id={self._campaign_id!r}, cap_microdollars={self._cap_microdollars})"
        )


def _rollback(connection):
    try:
        connection.execute("ROLLBACK")
    except sqlite3.Error:
        pass


__all__ = [
    "BudgetError",
    "CampaignLedger",
    "LEDGER_RELATIVE_PATH",
    "MAX_CAP_USD",
    "OPERATION_OUTCOMES",
    "SCHEMA_VERSION",
    "validate_operation_id",
]


def validate_operation_id(operation_id):
    """Public validation helper for callers that must fail before reserving."""
    return _validate_id(operation_id, "invalid_operation_id")

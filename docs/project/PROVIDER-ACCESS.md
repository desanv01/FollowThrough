# Provider access and campaign budget

The P3-BUDGET/r4 offline candidate is pending independent review. This document
describes the credential loader, the durable spending ledger and the guarded
client. Evidence covers local authorization envelopes on synthetic fixtures.
Main owns integration and live dispatch decisions.

## Canonical ledger location

One campaign ledger exists per explicit project root:

```
<project_root>/.coordination/provider-budget.sqlite3
```

There is no alternate path, environment override or fallback ledger. The absolute
project path is bound when a ledger is constructed; a later change of working
directory cannot redirect it. Initialization is only legal for a genuinely new
canonical file, claimed with an exclusive create. An existing blank file, unrelated
SQLite database, damaged file or partial/unrecognized ledger is refused without
being repurposed and without having its bytes changed. Simultaneous first
constructors may initialize only one identity. Other constructors open the completed
valid ledger or receive a fixed bounded storage error. The ledger supports
rollback-journal storage. Before any SQLite open it checks the file header and
refuses WAL mode or existing WAL/SHM sidecars. A new file requires an empty
canonical location with no orphaned sidecars. Admission, snapshots and the
preflight before each existing-file write transaction open SQLite with
`mode=ro`. A hot rollback journal encountered during preflight produces a fixed
storage refusal and preserves the database and journal bytes. The exclusive
initializer and preflighted writes use no-create `mode=rw`. Deleting the file
makes reservations, finalizations and snapshots fail closed. A missing, unreadable, locked,
corrupt or mismatched database fails closed. The database is never deleted, reset,
replaced or topped up automatically. It stores integer microdollars (1 USD =
1,000,000 microdollars) and no prompt, credential, response body, reasoning text or
raw provider usage.

## Supported credential syntax

`adapters.credentials.load_deepseek_key(project_root, *, environ=None)`:

1. Uses `DEEPSEEK_API_KEY` from `environ` when `environ` is supplied, otherwise
   from `os.environ`. It never mutates that environment.
2. If that entry is present, it must be a valid key. An empty or invalid supplied
   entry fails; it never falls back to the file.
3. Otherwise it reads only `<project_root>/.env.local`. Parent directories and the
   implicit current working directory are never searched. An empty or
   NUL-containing decoded root is rejected with `credential_root_invalid` before
   any file access. Environment presence is tested with mapping membership before
   indexing, so a `defaultdict` missing-key hook cannot fabricate an entry or
   suppress the file fallback, and the mapping is never mutated.

The project file is bounded and literal:

- Maximum 16 KiB, read as UTF-8 with an optional UTF-8 BOM. Line endings may be
  LF, CRLF or CR.
- Blank lines and whole-line `#` comments are ignored.
- Every other line must be `NAME=VALUE`. Whitespace around `NAME` and `VALUE` is
  ignored. `NAME` must look like an unquoted shell identifier: an ASCII letter or
  `_`, followed by ASCII letters, digits or `_`.
- One matching pair of single or double quotes around a value is removed. Quotes
  are only delimiters: there is no escape processing, variable interpolation,
  command execution or inline-comment stripping.
- Unrelated well-formed assignments are ignored. Duplicate assignments of
  `DEEPSEEK_API_KEY` are rejected.
- The returned key must be a nonempty string of at most 4,096 characters, all in
  printable ASCII (`!` through `~`). Spaces, tabs, control characters and
  non-ASCII characters invalidate it.

Synthetic example file (never commit a real value):

```
# offline fixture only
DEEPSEEK_API_KEY=synthetic-example-key-not-real
UNRELATED_NAME='literal value with spaces'
NO_EXPANSION="literal-$UNRELATED_NAME"
```

`export DEEPSEEK_API_KEY=...`, inline trailing comments and an unmatched quote are
malformed and fail with a fixed code.

## Ledger state transitions

`adapters.budget.CampaignLedger(project_root, *, campaign_id, cap_usd="20")`:

```
(absent) --initialize--> metadata(schema=1, campaign, cap) immutable
new operation --reserve--> pending      (consumes the full rounded envelope)
pending --finish--> success | provider_error | uncertain | internal_error
```

- The cap is at most US$20 and is fixed when the ledger is created. Reopening
  requires the same campaign ID and the same normalized cap; a mismatch, an
  unknown schema version or corrupt metadata refuses to open and is never reset.
- The bound ledger path is absolute. Every reservation or finalization transaction
  re-reads and validates the persisted schema version, campaign ID, cap and all
  operation rows before authorizing or recording anything; cached constructor
  metadata alone is not trusted. Snapshots validate the same state.
- Admission and every authorizing/outcome transaction require the exact canonical
  table definitions, including constraints and conflict behavior. They check all
  persisted schema objects and allow only the expected engine primary-key index
  for each `WITHOUT ROWID` table. Other definitions, indexes or objects produce
  `budget_schema_unsupported`. Read-only validation that would require journal
  recovery fails with a fixed bounded storage error.
- `reserve` verifies inside the same transaction that exactly the requested
  operation ID, rounded microdollar amount and `pending` status (with a null
  estimate) exist before commit; successful SQL execution alone is not treated as
  durable proof. A suppressed or ignored insertion is refused with
  `budget_reservation_not_persisted` and no transport dispatch occurs.
- Existing rows with TEXT/REAL/negative/zero amounts, invalid estimates or
  statuses, invalid operation IDs, or a committed total above the immutable cap are
  refused as `budget_ledger_corrupt`; they are never coerced, repaired or deleted.
  New ledger schemas additionally enforce SQLite `typeof(...)` storage constraints,
  so ordinary SQL cannot silently change a valid integer reservation into TEXT or
  REAL.
- `reserve(operation_id, amount_usd)` runs in one `BEGIN IMMEDIATE` transaction:
  it rejects a duplicate operation ID, checks committed reservations plus the new
  rounded amount against the cap, then inserts `pending`. A unique primary key
  backs the duplicate rule. The reservation commits before any transport handoff
  and no database transaction is held during a request.
- Every terminal outcome permanently keeps the reserved amount. There is no
  release, refund, reset, top-up, budget-increase or automatic retry API.
- A pending reservation left by a restart still consumes allowance and its
  operation ID can never dispatch again. This initial implementation intentionally
  reserves the whole per-call `max_cost_usd` envelope even for an early failure;
  the lost utilization is the price of simple, conservative accounting.
- `finish(operation_id, outcome, *, estimated_cost_usd=None)` accepts only the four
  allowlisted outcomes, records an optional nonnegative estimate, and never changes
  the reserved amount. A second finish and an unknown operation fail closed.
- `snapshot()` returns only campaign metadata, integer microdollar totals, operation
  IDs, reserved amounts, statuses and optional estimates (plus exact decimal
  display strings).

## Amount handling

Only `Decimal` instances and decimal strings are accepted; floats, booleans and
integers are rejected. Inputs are bounded (length, significant digits and decimal
exponent) so pathological values fail predictably instead of consuming arbitrary
time or memory. NaN, infinity, zero, negative values and normalized zero are
rejected.

- The campaign cap is rounded **down** to a whole microdollar.
- Each requested per-call envelope is rounded **up** to a whole microdollar.
- Estimated usage cost is rounded **up** to a whole microdollar for display.

Examples: cap `"0.0000019"` becomes 1 microdollar; envelope `"0.0000001"` becomes
1 microdollar; envelope `"0.0000019"` becomes 2 microdollars; cap `"20.000001"`
is rejected as over US$20.

Operation and campaign IDs are 1-128 characters from `A-Z a-z 0-9 . _ : -` and must
not contain prompts, secrets or conversation content.

## Guarded client

```python
from adapters.budgeted_deepseek import BudgetedDeepSeekClient

client = BudgetedDeepSeekClient(
    "C:/synthetic/offline-project",
    campaign_id="synthetic-campaign-01",
    cap_usd="20",
    environ={},                 # synthetic environment; use None only in a real runtime
    transport=fake_transport,   # offline fake; the default would be a real HTTPS client
)
completion = client.complete(
    "op-0001",
    [{"role": "user", "content": "offline fixture only"}],
    max_cost_usd="0.01",
    dispatch=True,              # must be exactly True
)
print(client.snapshot())
```

`BudgetedDeepSeekClient.complete` requires `dispatch is True` before doing anything
else. Without it, nothing is reserved and nothing is dispatched. With it, the
wrapper:

1. reserves the full supplied `max_cost_usd` envelope in the durable ledger;
2. calls the unchanged accepted `DeepSeekClient.complete` exactly once with that
   same envelope and the same request arguments;
3. records one terminal outcome, persisting a normalized estimated usage cost only
   when the adapter exposes one, otherwise null;
4. returns the `Completion` only after the durable outcome record succeeds.

It never retries, never releases funds based on a guessed charge, never imports
private pricing helpers, and never persists prompts, keys, response bodies,
reasoning or raw provider usage. Future budgeted campaign code **must** use this
wrapper. The unchanged low-level client alone is not an aggregate budget boundary.

Sanitized public failures are constructed while handling the raw failure but raised
only after leaving that handler, so neither `__context__` nor `__cause__` retains
raw provider usage, an unexpected local exception or a finalization failure.
Rendered tracebacks contain only fixed codes. Credential decoding and read failures
use the same exception-boundary pattern. This is exception-boundary redaction, not
secure-memory or debugger protection.

A failure while finalizing the result after transport raises
`result_not_recorded` with `outcome_unknown=True`. The reservation stays committed,
and the operation remains pending or in an unverified terminal state; no
`Completion` is returned and no zero charge is claimed.

### Adapter assumptions

The ledger enforces the local authorization envelopes the caller supplies. Those
envelopes are only meaningful under the accepted adapter's published assumptions:
peak Flash token rates, twice the serialized request size plus 4,096 input tokens,
requested `max_tokens` capping all billed output including reasoning, and the
returned model's charges being bounded by the configured rates. The ledger is not a
provider billing record and cannot guarantee what the provider charges; keep an
external account spending limit in place.

## Safe error codes

Credential loader (`CredentialError.code`):

| Code | Meaning |
| --- | --- |
| `credential_missing` | No environment entry and no target assignment in the project file |
| `credential_empty` | The target value is empty |
| `credential_invalid` | The supplied key has an invalid type, length or character |
| `credential_file_malformed` | A non-comment line is not a valid literal assignment |
| `credential_file_duplicate` | The project file assigns the target more than once |
| `credential_file_too_large` | The project file exceeds 16 KiB |
| `credential_file_encoding` | The project file is not valid UTF-8 |
| `credential_file_unreadable` | The project file could not be read |
| `credential_root_invalid` | The supplied project root is not path-like, is empty, or contains NUL |
| `credential_environment_invalid` | The supplied environment mapping is unusable |

Ledger (`BudgetError.code`):

| Code | Meaning |
| --- | --- |
| `invalid_campaign_id` / `invalid_operation_id` | ID shape violation |
| `invalid_cap` / `invalid_amount` / `invalid_estimate` | Number rejected (type, bounds, non-finite, non-positive, over cap) |
| `budget_cap_exceeded` | Committed reservations plus this envelope exceed the campaign cap |
| `duplicate_operation` | This operation ID was already reserved; never redispatch |
| `unknown_operation` | Finish referenced an operation that does not exist |
| `operation_already_finished` | A terminal operation cannot be finalized again |
| `invalid_outcome` | Outcome is not one of the four allowlisted values |
| `budget_campaign_mismatch` / `budget_cap_mismatch` | Reopened ledger metadata differs |
| `budget_schema_mismatch` | Ledger schema version is unknown |
| `budget_ledger_corrupt` | Required metadata or tables are missing/malformed, or persisted operation rows are invalid or exceed the cap |
| `budget_schema_unsupported` | Persisted schema objects/shape (tables, columns, keys, triggers, views, indexes, foreign keys) are outside the supported ledger definition |
| `budget_reservation_not_persisted` | The transaction did not create and observe the exact pending row; no dispatch |
| `budget_storage_not_ready` | Existing file is blank or not yet a complete initialized ledger; it is never treated as new |
| `budget_ledger_unrecognized` | Existing SQLite file is not a recognizable ledger; it is never adopted or reset |
| `budget_storage_corrupt` / `budget_storage_unavailable` / `budget_storage_locked` | Storage fails closed; no dispatch |
| `budget_storage_error` | Other SQLite failure, reported without raw cause |
| `budget_root_invalid` | The project root is not a usable directory |

Guarded client (`BudgetedDeepSeekError.code`):

| Code | Meaning |
| --- | --- |
| `dispatch_not_authorized` | `dispatch` was not exactly `True`; nothing reserved or sent |
| `invalid_project_root` | The wrapper project root is not path-like, is empty, or contains NUL; nothing reserved or sent |
| Adapter code (for example `http_error`, `transport_timeout`) | Preserved with safe `status` and `outcome_unknown`; funds stay reserved |
| `internal_error` | Unexpected local failure; may or may not have dispatched, reported as uncertain |
| `result_not_recorded` | Durable outcome persistence failed after a possible dispatch; uncertainty reported |

Adapter `http_error` responses keep the HTTP status. Unknown outcomes map to the
ledger outcome `uncertain`; known adapter errors map to `provider_error`. No public
error carries a raw cause, traceback text, provider body or credential.

## Limitations

- Candidate overlay only: not installed, not integrated, not independently reviewed
  and not accepted by main.
- Offline evidence only: fake HTTP transports, synthetic roots and synthetic keys.
  Zero real provider requests were made.
- The local envelope assumes the accepted adapter's pricing/token model; it is not
  provider billing and offers no charge reconciliation.
- Enforcement is per chosen project root. Alternate roots and direct low-level
  client calls are outside this ledger's boundary.
- Application-level redaction only; this is not secure-memory erasure and does not
  protect against debuggers, process dumps or a compromised host. Exception-chain
  redaction covers the fixed public error objects, not arbitrary internal frames.
- Storage checks assume stable paths on a local, campaign-owned filesystem. Run
  concurrent campaign operations through the ledger and keep external database or
  journal changes quiescent during those operations. Header and read-only checks
  precede the write lock, so racing file replacement, journal-mode changes or an
  interrupted external writer in that gap are outside the storage guarantee.
  Ordinary ledger writes remain serialized by SQLite with finite lock timeouts.
- A reopened pending reservation is restart-state evidence, not proof of actual
  process-crash safety. Manual deletion of the database before any handle opens is
  outside detection.
- No automatic recovery, release, reconciliation or retry is provided for pending
  operations; main must investigate them manually before future work.

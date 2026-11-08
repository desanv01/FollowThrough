# Retry-safe account credits

The processor handles credit deliveries that may arrive again after a retry. A completed delivery records its identity and original units, and contributes its units once to the tenant's total. Processing is sequential and all durable effects live in one supplied SQLite-backed store.

## Processor interface

`processor.py` is a self-contained Python standard-library module with these entry points.

- `process_event(store, item)` returns one of the status strings below.
- `process_batch(store, items)` takes an ordered iterable of items and returns a list of statuses in input order. An empty iterable returns an empty list.

An item is a Python dictionary with `tenant`, `event_id`, and `units`. Extra keys are ignored. The tenant and event ID must be nonempty strings and are compared exactly. The units must be a positive Python integer, with booleans excluded. There is no fixed-width limit on units or accumulated totals. A missing or malformed field or an item of another type is invalid. Validate the whole item before looking up its identity.

| Status | Meaning and durable effect |
|---|---|
| `applied` | This `(tenant, event_id)` is new. Add its units to the tenant total and record the completion and original units in one transaction. |
| `duplicate` | This identity already completed with the same units. Leave the store unchanged. |
| `conflict` | This identity already completed with different valid units. Leave the store unchanged, including the original completion units. |
| `invalid` | The item is malformed. Leave the store unchanged. |

Each batch item is processed independently. Continue after an invalid item or a conflict. If a store operation raises, propagate the exception and stop the batch. Earlier committed entries remain durable. Entries after the failed call are unprocessed. Retrying the batch uses the same identity rules for earlier entries.

For example, a delivery with `{"tenant": "harbor", "event_id": "m1", "units": 2}` adds two units to a new tenant. A replay with two units returns `duplicate`, while a replay with three returns `conflict`. Another tenant can independently apply the same event ID. In a batch, a row with `units=True` returns `invalid` and processing continues to the next item.

## Supplied store

The caller supplies the store. These methods are the complete processor-facing API.

| API | Behavior |
|---|---|
| `store.lookup_units(tenant, event_id)` | Return the original completion units as an integer, or `None` if the identity is absent. |
| `store.add_units(tenant, units)` | Add units to the tenant's durable total. An absent total starts at zero. |
| `store.mark_completed(tenant, event_id, units)` | Record a new completion. Inserting an existing identity raises. |
| `with store.transaction():` | Start a transaction. A body exception rolls back its writes. A successful body commits its writes together. Nested transactions are unsupported. |

The store owns the transaction and failure injection. Writes outside a transaction commit individually. On a new delivery the store may raise a transient `RuntimeError` immediately after the credit write or immediately after the completion write. Neither effect may persist after one of these precommit failures. The store can also raise immediately after committing, representing an acknowledgement lost after the durable operation succeeded. In that case both effects persist and the next identical delivery returns `duplicate`. These rules also apply after reopening the database and reloading the processor.

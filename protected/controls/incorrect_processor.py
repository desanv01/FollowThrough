"""Known-incorrect authored control: credits before durable completion.

This file is never imported by the host. It exists only for the protected
candidate-container control when qualified Docker is available.
"""


def process_event(store, item):
    if not isinstance(item, dict):
        return "invalid"
    tenant = item.get("tenant")
    event_id = item.get("event_id")
    units = item.get("units")
    if not isinstance(tenant, str) or not tenant:
        return "invalid"
    if not isinstance(event_id, str) or not event_id:
        return "invalid"
    if not isinstance(units, int) or isinstance(units, bool) or units <= 0:
        return "invalid"
    if store.lookup_units(tenant, event_id) is not None:
        return "duplicate"
    store.add_units(tenant, units)
    store.mark_completed(tenant, event_id, units)
    return "applied"


def process_batch(store, items):
    return [process_event(store, item) for item in items]

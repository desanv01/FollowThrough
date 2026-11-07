"""Guarded credential and durable aggregate-budget composition around the adapter.

Future budgeted campaign calls must use this wrapper.  The unchanged low-level
:class:`adapters.deepseek.DeepSeekClient` alone is not an aggregate budget
boundary: it checks a single computed reservation and knows nothing about
campaign-wide committed spending.

Sanitized public errors are constructed inside the raw handler but raised only
at a clean boundary after leaving it, so neither provider usage nor an
unexpected local exception is retained through ``__context__`` or ``__cause__``.
"""

import os

from .budget import CampaignLedger
from .credentials import load_deepseek_key
from .deepseek import DeepSeekClient, DeepSeekError


class BudgetedDeepSeekError(Exception):
    """Sanitized wrapper failure with a fixed code and uncertainty flag."""

    def __init__(self, code, *, status=None, outcome_unknown=False, reservation_usd=None):
        super().__init__(code)
        self.code = code
        self.status = status
        self.outcome_unknown = outcome_unknown
        self.reservation_usd = reservation_usd


class BudgetedDeepSeekClient:
    """Load the project key, reserve durably, then dispatch at most once."""

    def __init__(
        self, project_root, *, campaign_id, cap_usd="20", environ=None, transport=None
    ):
        decoded = None
        invalid_root = False
        try:
            decoded = os.fsdecode(project_root)
        except (TypeError, ValueError):
            invalid_root = True
        if not invalid_root and (decoded == "" or "\x00" in decoded):
            invalid_root = True
        if invalid_root:
            raise BudgetedDeepSeekError("invalid_project_root")
        # Bind one canonical absolute root now; later chdir cannot redirect the
        # loader or ledger, and both components receive this same value.
        root = os.path.abspath(decoded)
        self._project_root = root
        self._campaign_id = campaign_id
        key = load_deepseek_key(root, environ=environ)
        self._ledger = CampaignLedger(
            root, campaign_id=campaign_id, cap_usd=cap_usd
        )
        self._client = DeepSeekClient(key, transport=transport)

    def complete(
        self,
        operation_id,
        messages,
        *,
        max_cost_usd,
        tools=None,
        max_tokens=64,
        timeout_seconds=15,
        max_response_bytes=131072,
        dispatch=False,
    ):
        """Reserve the full envelope, call the adapter once, record the outcome."""
        if dispatch is not True:
            raise BudgetedDeepSeekError("dispatch_not_authorized")

        self._ledger.reserve(operation_id, max_cost_usd)

        completion = None
        public_error = None
        failure_outcome = None
        try:
            completion = self._client.complete(
                messages,
                max_cost_usd=max_cost_usd,
                tools=tools,
                max_tokens=max_tokens,
                timeout_seconds=timeout_seconds,
                max_response_bytes=max_response_bytes,
            )
        except DeepSeekError as failure:
            failure_outcome = "uncertain" if failure.outcome_unknown else "provider_error"
            public_error = BudgetedDeepSeekError(
                failure.code,
                status=failure.status,
                outcome_unknown=failure.outcome_unknown,
                reservation_usd=failure.reservation_usd,
            )
        except Exception:
            failure_outcome = "internal_error"
            public_error = BudgetedDeepSeekError("internal_error", outcome_unknown=True)

        # Clean boundary: no raw exception is active here, so the public failures
        # below are raised without attaching provider usage or local causes.
        if public_error is not None:
            if not self._record_outcome_quietly(operation_id, failure_outcome):
                raise BudgetedDeepSeekError("result_not_recorded", outcome_unknown=True) from None
            raise public_error

        estimated_cost = completion.usage.estimated_cost_usd
        if not self._record_outcome_quietly(
            operation_id, "success", estimated_cost_usd=estimated_cost
        ):
            raise BudgetedDeepSeekError("result_not_recorded", outcome_unknown=True) from None
        return completion

    def _record_outcome_quietly(self, operation_id, outcome, *, estimated_cost_usd=None):
        """Try to persist one outcome; never re-raise or retain the raw cause."""
        try:
            self._ledger.finish(
                operation_id, outcome, estimated_cost_usd=estimated_cost_usd
            )
        except Exception:
            return False
        return True

    def snapshot(self):
        """Safe JSON-compatible ledger snapshot for the coordinating owner."""
        return self._ledger.snapshot()

    def __repr__(self):
        ledger = getattr(self, "_ledger", None)
        return (
            "BudgetedDeepSeekClient("
            f"campaign_id={self._campaign_id!r}, "
            f"ledger_path={(ledger.ledger_path if ledger is not None else None)!r})"
        )


__all__ = ["BudgetedDeepSeekClient", "BudgetedDeepSeekError"]

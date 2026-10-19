"""Retryable GET-only preparation failures, before an entry order intent.

Retries retain the original signal expiry. This classification must never be
used to retry an order POST or an uncertain leverage mutation.
"""


class RetryableRead(ValueError):
    def __init__(self, reason, code, **evidence):
        super().__init__(reason)
        self.code = code
        self.evidence = evidence


TRANSIENT_API_CODES = frozenset({"50004", "50011", "50013", "50026", "50040"})


def assert_no_entry_intent(trade):
    """A waiting record cannot own any submitted or ambiguous entry order."""
    if any(trade.get(key) is not None for key in
           ("entry_request", "entry_ack", "entry_order", "entry_ms", "algo_id", "exit_id")):
        raise ValueError("retry record contains an entry intent; reconciliation required")

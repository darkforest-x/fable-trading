"""Moved to yoyo.contracts.forward_log (2026-08-03 four-layer restructure).

The forward log is the L2 -> L4 interface, not the judgment layer's private file.
Kept as a forwarder while callers migrate.
"""
from yoyo.contracts.forward_log import *  # noqa: F401,F403
from yoyo.contracts.forward_log import (  # noqa: F401
    ForwardKey, LEGACY_SIDE, actionable_rows, forward_key, merge_forward_log,
    normalize_log, open_keys, read_forward_log, row_key, rows_for_protocol,
    write_forward_log,
)

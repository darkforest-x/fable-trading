"""Moved to fable.contracts.protocol (2026-08-03 restructure).

Kept as a forwarder so the 253 archived scripts and the existing tests keep
working during migration. Delete once every caller imports from fable.contracts.
"""
from fable.contracts.protocol import *  # noqa: F401,F403
from fable.contracts.protocol import (  # noqa: F401
    ACTIVE_BUNDLE, BundleError, FEATURE_SEMANTICS, PROJECT_DIR, REQUIRED_FIELDS,
    StrategyProtocol, file_sha256, load_active_bundle, load_bundle,
)

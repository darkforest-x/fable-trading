"""P2.5 Phase 2: hard-coded job whitelist + sqlite store + subprocess runner.

Never accept free shell / cmd / argv strings from the client.
"""
from __future__ import annotations

from src.webapp.jobs.runner import JobRunner, get_runner
from src.webapp.jobs.store import JobStore, get_store
from src.webapp.jobs.whitelist import (
    JOB_TYPES,
    build_argv,
    list_job_types,
    validate_params,
)

__all__ = [
    "JOB_TYPES",
    "JobRunner",
    "JobStore",
    "build_argv",
    "get_runner",
    "get_store",
    "list_job_types",
    "validate_params",
]

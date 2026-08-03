"""Moved to yoyo.contracts.protocol (2026-08-03 restructure, then split into
the yoyo-trading repo).

Kept as a forwarder so archived scripts and existing tests keep working. The star
import is deliberate: enumerating names here means every rename upstream breaks
this file, which is exactly what happened when ACTIVE_BUNDLE became a lazily
resolved path.
"""
from yoyo.contracts.protocol import *  # noqa: F401,F403

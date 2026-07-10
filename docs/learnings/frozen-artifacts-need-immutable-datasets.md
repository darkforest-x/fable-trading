# Frozen artifacts need immutable datasets

- **Problem**: An ACTIVE model's stored dataset hash stopped matching even though the model and threshold had not changed.
- **Dead end**: Updating metadata to the current CSV hash would silence the dashboard but falsely rewrite model provenance; checking only whether the path exists also misses in-place dataset rebuilds.
- **Effective path**: Compare frozen size/hash/time with the current file, identify byte-identical sibling outputs, and treat a post-freeze rewrite at the same path as provenance loss rather than model corruption.
- **General rule**: Freeze operations must copy the exact training CSV to a dated or content-addressed immutable path before writing model metadata; mutable pipeline output paths must never be artifact provenance.
- **Affected areas**: `src/judgment/frozen.py`, `models/frozen_*.json`, `data/swap_replication/`, pipeline fingerprint health checks.

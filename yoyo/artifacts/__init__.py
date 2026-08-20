"""Registry access for the two governance files at the repository root.

`yoyo.contracts.artifacts` decides what a valid record is; this package decides
where the files live and enforces the rules that only exist *between* records --
duplicate ids, and experiments citing artifacts nobody registered.
"""
from yoyo.artifacts.registry import (
    ARTIFACT_REGISTRY_PATH,
    EXPERIMENT_REGISTRY_PATH,
    Registries,
    load_artifact_registry,
    load_experiment_registry,
    load_registries,
)

__all__ = [
    "ARTIFACT_REGISTRY_PATH",
    "EXPERIMENT_REGISTRY_PATH",
    "Registries",
    "load_artifact_registry",
    "load_experiment_registry",
    "load_registries",
]

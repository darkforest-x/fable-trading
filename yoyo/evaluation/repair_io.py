"""Safe output-directory handling for reruns of frozen evaluation builders."""
from __future__ import annotations

import argparse
from pathlib import Path


def new_output_dir(output_dir, *, root: Path, historical_dir: Path,
                   artifacts: tuple[str, ...], description: str) -> Path:
    """Resolve an explicit new destination and fail closed on old artifacts."""
    if output_dir is None:
        parser = argparse.ArgumentParser(description=description)
        parser.add_argument('--output-dir', required=True,
                            help='New output directory; frozen v1 config and inputs remain the source')
        output_dir = parser.parse_args().output_dir
    output_dir = Path(output_dir)
    if not output_dir.is_absolute():
        output_dir = root / output_dir
    output_dir = output_dir.resolve()
    if output_dir == historical_dir.resolve():
        raise ValueError(f'Refusing to overwrite historical experiment directory: {historical_dir}')
    output_dir.mkdir(parents=True, exist_ok=True)
    existing = [name for name in artifacts if (output_dir / name).exists()]
    if existing:
        raise FileExistsError(f'Output artifacts already exist in {output_dir}: {existing}')
    return output_dir

"""Rerun the frozen study into a new directory without replacing old evidence."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import yoyo.evaluation.release_eth_multitf as study


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    output = args.out.resolve()
    if output.exists():
        raise SystemExit("choose a new output directory; existing evidence is immutable")
    # The committed engine and its fingerprint are unchanged. Only output moves.
    study.OUT = output
    for phase in ("development", "validation"):
        sys.argv = [sys.argv[0], phase]
        study.main()


if __name__ == "__main__":
    main()

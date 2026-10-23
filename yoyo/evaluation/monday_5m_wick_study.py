"""Reproduce both groups through the existing Nautilus adapters and caches.

The spot-quality adapter owns conflict censoring. Crypto keeps the original
unchanged builder, allowing completed native fills to be reused without replay.
This module only orchestrates committed builders; it has no execution logic.
"""
import argparse
import json
import subprocess
import sys

from yoyo.evaluation import monday_5m_wick_engulf as m


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workers', type=int, default=3)
    args = parser.parse_args()
    cfg = json.loads((m.EXP/'config.json').read_text())
    symbols = [s for s, _ in m.jobs(cfg)[0] if s != 'XAUUSD']
    subprocess.run([sys.executable, '-m', 'yoyo.evaluation.monday_5m_wick_engulf',
                    '--workers', str(args.workers), '--symbols', *symbols], check=True)
    subprocess.run([sys.executable, '-m', 'yoyo.evaluation.monday_5m_wick_gold'], check=True)
    subprocess.run([sys.executable, '-m', 'yoyo.evaluation.monday_5m_wick_engulf', '--summary'], check=True)


if __name__ == '__main__': main()

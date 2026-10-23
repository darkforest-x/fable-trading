"""Reproduce both groups through the existing Nautilus adapters and caches.

The current gold revision corrects the local archive clock and owns conflict
censoring. Crypto keeps the original unchanged builder and cached fills.
The registered v1 combined ledger is retained as the correction's precursor;
--historical-v1 explicitly reproduces that superseded gold clock only.
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
    parser.add_argument('--historical-v1', action='store_true')
    args = parser.parse_args()
    cfg = json.loads((m.EXP/'config.json').read_text())
    symbols = [s for s, _ in m.jobs(cfg)[0] if s != 'XAUUSD']
    subprocess.run([sys.executable, '-m', 'yoyo.evaluation.monday_5m_wick_engulf',
                    '--workers', str(args.workers), '--symbols', *symbols], check=True)
    if args.historical_v1:
        subprocess.run([sys.executable, '-m', 'yoyo.evaluation.monday_5m_wick_gold'], check=True)
        subprocess.run([sys.executable, '-m', 'yoyo.evaluation.monday_5m_wick_engulf', '--summary'], check=True)
    else:
        subprocess.run([sys.executable, '-m', 'yoyo.evaluation.monday_5m_wick_gold_clock'], check=True)


if __name__ == '__main__': main()

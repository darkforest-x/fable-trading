"""Replace the monitored Discord channels with a named list and route them.

    COPIER_HOME=... .venv-copier/bin/python -m yoyo.copier.tools.set_watchlist \
        --guild 1004707886657699901 --route paper \
        --name eth-advanced --name Woods --known 1226095564073205780=Woods

Channels whose id is already on record (``--known id=name``) switch on at once;
the rest switch on when the extension first reports a tab whose title matches.
Every other channel is switched off. Restart the copier service afterwards.
"""
from __future__ import annotations

import argparse
import json

from yoyo.copier.discord_channels import apply_watchlist
from yoyo.copier.store.sqlite import Database


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--guild", required=True)
    parser.add_argument("--route", default="paper")
    parser.add_argument("--name", action="append", required=True)
    parser.add_argument("--known", action="append", default=[], help="channel_id=name")
    args = parser.parse_args()
    known = dict(item.split("=", 1) for item in args.known)
    print(json.dumps(apply_watchlist(Database(), args.guild, args.name, known, args.route), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

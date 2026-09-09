"""Freeze Gate's public no-query contract catalog without changing live fetchers.

The initial bootstrap's explicit limit=1000 was rejected with HTTP 400; the
same documented endpoint with no query is observed to return all 973 entries
in the September 2026 probe. We retain exactly what the public response returns
and do not imply a complete historical or delisted universe. This separate
entry point avoids changing the source SHA of concurrently running collectors.
Source: https://www.gate.com/docs/developers/apiv4/en/futures/
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from yoyo.data import altseason_sources
from yoyo.data.altseason_sources import PublicClient, SCHEMA_VERSION, atomic_json, digest, normalize_catalog


def bootstrap(client):
    if client.venue != "gate":
        raise ValueError("Gate-only catalog bootstrap")
    target = client.root / "catalog" / "gate.json"
    if target.exists():
        existing = json.loads(target.read_text())
        if existing.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("Existing catalog schema mismatch")
        return existing
    raw, receipt = client.get("/futures/usdt/contracts", {})
    if not isinstance(raw, list) or not raw:
        raise ValueError("Gate no-query catalog response is empty or invalid")
    markets = normalize_catalog("gate", raw, receipt["fetched_at"])
    if len({row["symbol"] for row in markets}) != len(markets):
        raise ValueError("Duplicate market ids in Gate no-query catalog")
    result = {"schema_version": SCHEMA_VERSION, "venue": "gate", "asof": receipt["fetched_at"], "markets": markets, "receipts": [receipt],
              "bootstrap": {"module": "yoyo.data.altseason_gate_catalog", "source_sha256": digest(Path(__file__).read_bytes()),
                            "normalizer_sha256": digest(Path(altseason_sources.__file__).read_bytes()),
                            "reason": "Explicit limit1000/offset0 rejected HTTP400; public no-query endpoint validated and frozen.", "query": {}},
              "survivorship_warning": "As-of no-query catalog only. Not an exhaustive historical listing/delisting ledger; absent removed symbols cannot be inferred."}
    atomic_json(target, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    client = PublicClient("gate", args.output)
    client._session().trust_env = False
    result = bootstrap(client)
    print(json.dumps({"asof": result["asof"], "catalog": len(result["markets"]), "eligible": sum(x["eligible"] for x in result["markets"])}), flush=True)


if __name__ == "__main__":
    main()

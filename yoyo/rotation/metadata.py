"""Time-aware instrument/event metadata; missing coverage is never no-risk.

Records use published_at for information availability and effective_at for the
event date. An event announced after the decision is unavailable even if its
effective date is earlier. Review coverage has its own publication/expiry clock.
"""
from __future__ import annotations

from pathlib import Path
import json
from typing import Any, Mapping

from yoyo.contracts.rotation import RotationError, digest, iso, utc
from yoyo.rotation.providers import LEVERAGED_BASES, NON_CRYPTO_BASES

KNOWN_SECTORS = {"BTCUSDT": "基准", "ETHUSDT": "基准", "SOLUSDT": "公链",
                 "AVAXUSDT": "公链", "ADAUSDT": "公链", "INJUSDT": "金融基础设施",
                 "ETCUSDT": "PoW", "DOGEUSDT": "Meme", "LINKUSDT": "预言机"}


def load_catalog(path: Path) -> dict:
    if not Path(path).exists():
        return {"schema_version": 1, "assets": [], "events": [], "reviews": []}
    value = json.loads(Path(path).read_text())
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise RotationError("event catalog schema must be version 1")
    for field in ("assets", "events", "reviews"):
        if not isinstance(value.get(field, []), list):
            raise RotationError("event catalog " + field + " must be an array")
    return value


def asset_context(symbol: str, catalog: Mapping[str, Any], *, as_of: Any) -> dict:
    cutoff = utc(as_of)
    sector = KNOWN_SECTORS.get(symbol, "未分类")
    base = symbol[:-4]
    asset_type = "non_crypto" if base in NON_CRYPTO_BASES | LEVERAGED_BASES else "crypto" if symbol in KNOWN_SECTORS else "unknown"
    assets = [a for a in catalog.get("assets", []) if a.get("symbol") == symbol and utc(a["published_at"]) <= cutoff]
    if assets:
        latest_time = max(utc(a["published_at"]) for a in assets)
        newest = [a for a in assets if utc(a["published_at"]) == latest_time]
        if len({digest(a) for a in newest}) > 1:
            raise RotationError("conflicting asset metadata at the same publication time: " + symbol)
        asset = newest[0]
        asset_type = asset.get("asset_type", "unknown")
        sector = asset.get("sector", "未分类")
    visible = []
    for event in catalog.get("events", []):
        if event.get("symbol") != symbol or utc(event["published_at"]) > cutoff:
            continue
        if event.get("expires_at") and utc(event["expires_at"]) < cutoff:
            continue
        if not event.get("source_url") or event.get("severity") not in {"info", "warning", "block"}:
            raise RotationError("visible event requires source_url and valid severity")
        visible.append({k: event.get(k) for k in ("title", "severity", "source_url", "published_at", "effective_at", "expires_at")})
    covered = False
    coverage_reason = "no_current_manual_review"
    reviews = [r for r in catalog.get("reviews", []) if r.get("symbol") == symbol and utc(r["reviewed_at"]) <= cutoff]
    if reviews:
        latest_time = max(utc(r["reviewed_at"]) for r in reviews)
        newest = [r for r in reviews if utc(r["reviewed_at"]) == latest_time]
        if len({digest(r) for r in newest}) > 1:
            raise RotationError("conflicting risk reviews at the same time: " + symbol)
        review = newest[0]
        if not review.get("reviewer") or not review.get("sources"):
            raise RotationError("risk review must name reviewer and sources")
        if utc(review["reviewed_at"]) <= cutoff <= utc(review["valid_until"]):
            covered = review.get("status") == "reviewed"
            coverage_reason = "current_manual_review" if covered else "latest_review_not_cleared"
            if any(utc(e["published_at"]) > utc(review["reviewed_at"]) for e in visible):
                covered = False
                coverage_reason = "new_event_after_review_requires_new_review"
        else:
            coverage_reason = "latest_review_expired"
    blocked = asset_type != "crypto" or any(e["severity"] == "block" for e in visible)
    return {"asset_type": asset_type, "sector": sector, "events": sorted(visible, key=lambda e: (e["published_at"], e["title"])),
            "event_risk": "block" if blocked else "clear" if covered else "unknown",
            "risk_coverage": "reviewed" if covered else "unknown",
            "risk_coverage_reason": coverage_reason,
            "catalog_hash": digest(catalog), "metadata_as_of": iso(cutoff)}

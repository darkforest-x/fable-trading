#!/usr/bin/env python3
"""Serve the blind Local Signal V2 YES/NO/SKIP review with durable autosave."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "analysis/output/local_signal_v2_positive_semantic_review200_v2"
VALID = frozenset({"YES", "NO", "SKIP"})


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def load_manifest(out_dir: Path) -> dict[str, dict[str, Any]]:
    rows = read_jsonl(out_dir / "review_manifest.jsonl")
    return {str(row["review_id"]): row for row in rows}


def load_verdicts(out_dir: Path) -> dict[str, str]:
    latest: dict[str, str] = {}
    valid_ids = set(load_manifest(out_dir))
    for row in read_jsonl(out_dir / "owner_verdicts.jsonl"):
        review_id = str(row.get("review_id", ""))
        verdict = str(row.get("owner_verdict", "")).upper()
        if review_id in valid_ids and verdict in VALID:
            latest[review_id] = verdict
    return latest


def append_verdict(out_dir: Path, review_id: str, verdict: str) -> dict[str, Any]:
    manifest = load_manifest(out_dir)
    if review_id not in manifest:
        raise ValueError("unknown review_id")
    verdict = verdict.upper()
    if verdict not in VALID:
        raise ValueError("owner_verdict must be YES, NO, or SKIP")
    record = {
        "review_id": review_id,
        "owner_verdict": verdict,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }
    with (out_dir / "owner_verdicts.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record


class ReviewHandler(SimpleHTTPRequestHandler):
    out_dir: Path = DEFAULT_OUT

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(self.out_dir), **kwargs)

    def _json(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/api/state":
            self._json(200, {"ok": True, "verdicts": load_verdicts(self.out_dir)})
            return
        if path in {"/", "/index.html"}:
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] != "/api/verdict":
            self._json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            record = append_verdict(
                self.out_dir,
                str(payload.get("review_id", "")),
                str(payload.get("owner_verdict", "")),
            )
        except Exception as exc:
            self._json(400, {"error": str(exc)})
            return
        self._json(200, {"ok": True, "record": record})

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    out_dir = args.out.resolve()
    if not (out_dir / "index.html").is_file() or not (out_dir / "review_manifest.jsonl").is_file():
        raise SystemExit(f"review pack missing under {out_dir}; run the builder first")
    ReviewHandler.out_dir = out_dir
    server = ThreadingHTTPServer((args.host, args.port), ReviewHandler)
    print(f"Open http://{args.host}:{args.port}/")
    print("Keys: Y=YES N=NO S=SKIP Left/Right=previous/next")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

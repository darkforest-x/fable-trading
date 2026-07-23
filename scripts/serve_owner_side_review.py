#!/usr/bin/env python3
"""Tiny local server for owner long/short review — open gallery and label with L/S/K.

Serves analysis/output/owner_side_review/ and writes labels immediately to:
  - reviews.jsonl (append)
  - review_sheet.csv (owner_side / owner_note columns)

Also mounts dense_owner_v11 images at /ds/<split>/<stem>.png so full-mode
cards without previews still show the chart + YOLO overlay.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/serve_owner_side_review.py
  PYTHONPATH=. .venv/bin/python scripts/serve_owner_side_review.py --port 8765

Then open http://127.0.0.1:8765/gallery.html
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = PROJECT / "analysis" / "output" / "owner_side_review"
DEFAULT_SRC = PROJECT / "datasets" / "_deprecated_pretip" / "dense_owner_v11"
VALID = frozenset({"long", "short", "skip"})


class ReviewHandler(SimpleHTTPRequestHandler):
    out_dir: Path = DEFAULT_OUT
    src_dir: Path = DEFAULT_SRC

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(self.out_dir), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/api/state":
            self._json(200, {"reviews": load_reviews(self.out_dir), "ok": True})
            return
        if path == "/api/items":
            self._json(200, load_items_payload(self.out_dir))
            return
        if path.startswith("/ds/"):
            self._serve_dataset_image()
            return
        if self.path in ("/", "/index.html"):
            self.path = "/gallery.html"
        return super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] != "/api/label":
            self._json(404, {"error": "not found"})
            return
        try:
            data = self._read_json()
        except Exception as exc:
            self._json(400, {"error": f"bad json: {exc}"})
            return
        box_id = str(data.get("box_id", "")).strip()
        side = str(data.get("owner_side", "")).strip().lower()
        note = str(data.get("owner_note", "") or "")
        if not box_id or side not in VALID:
            self._json(400, {"error": "box_id + owner_side in {long,short,skip} required"})
            return
        rec = {
            "box_id": box_id,
            "owner_side": side,
            "owner_note": note,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        append_review(self.out_dir, rec)
        updated = patch_sheet(self.out_dir / "review_sheet.csv", box_id, side, note)
        self._json(200, {"ok": True, "updated_sheet": updated, "record": rec})

    def _serve_dataset_image(self) -> None:
        # /ds/<split>/<stem>.png
        parts = unquote(self.path.split("?", 1)[0]).strip("/").split("/")
        if len(parts) != 3 or parts[0] != "ds":
            self.send_error(404)
            return
        split, name = parts[1], parts[2]
        if split not in ("train", "val") or ".." in name:
            self.send_error(400)
            return
        if not name.endswith(".png"):
            name = name + ".png"
        path = self.src_dir / "images" / split / name
        if not path.is_file():
            self.send_error(404)
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        self.wfile.write(data)


def load_items_payload(out_dir: Path) -> dict:
    """Fresh items.json + preview file existence (for streaming gallery poll)."""
    items_path = out_dir / "items.json"
    items: list = []
    if items_path.exists():
        try:
            items = json.loads(items_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            items = []
    ready = 0
    pending = 0
    for it in items:
        prev = str(it.get("preview_path") or "").strip()
        exists = bool(prev) and (out_dir / prev).is_file()
        it["preview_ready"] = exists
        if exists:
            ready += 1
        else:
            pending += 1
            # keep path empty so UI shows 渲染中 instead of broken img
            if prev and not exists:
                it["preview_path"] = ""
    render_flag = out_dir / "render_status.json"
    status = {}
    if render_flag.exists():
        try:
            status = json.loads(render_flag.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            status = {}
    return {
        "ok": True,
        "items": items,
        "n_items": len(items),
        "n_preview_ready": ready,
        "n_preview_pending": pending,
        "render": status,
    }


def load_reviews(out_dir: Path) -> dict:
    """Latest side per box_id from jsonl + sheet."""
    out: dict = {}
    sheet = out_dir / "review_sheet.csv"
    if sheet.exists():
        with sheet.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                side = (row.get("owner_side") or "").strip().lower()
                if side in VALID:
                    out[row["box_id"]] = {
                        "owner_side": side,
                        "owner_note": row.get("owner_note") or "",
                        "ts": "",
                    }
    jl = out_dir / "reviews.jsonl"
    if jl.exists():
        for line in jl.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            side = str(rec.get("owner_side", "")).strip().lower()
            bid = str(rec.get("box_id", "")).strip()
            if bid and side in VALID:
                out[bid] = {
                    "owner_side": side,
                    "owner_note": rec.get("owner_note") or "",
                    "ts": rec.get("ts") or "",
                }
    return out


def append_review(out_dir: Path, rec: dict) -> None:
    path = out_dir / "reviews.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def patch_sheet(sheet: Path, box_id: str, side: str, note: str) -> bool:
    if not sheet.exists():
        return False
    with sheet.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
        fields = list(rows[0].keys()) if rows else []
    if "owner_side" not in fields:
        fields.append("owner_side")
    if "owner_note" not in fields:
        fields.append("owner_note")
    hit = False
    for row in rows:
        if row.get("box_id") == box_id:
            row["owner_side"] = side
            row["owner_note"] = note
            hit = True
            break
    if not hit:
        return False
    tmp = sheet.with_suffix(".csv.tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fields})
    tmp.replace(sheet)
    # Keep sample sheet in sync when present.
    sample = sheet.with_name("review_sheet_sample.csv")
    if sample.exists():
        with sample.open(newline="", encoding="utf-8") as fh:
            srows = list(csv.DictReader(fh))
            sfields = list(srows[0].keys()) if srows else fields
        for row in srows:
            if row.get("box_id") == box_id:
                row["owner_side"] = side
                row["owner_note"] = note
        tmp2 = sample.with_suffix(".csv.tmp")
        with tmp2.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=sfields, extrasaction="ignore")
            w.writeheader()
            for row in srows:
                w.writerow({k: row.get(k, "") for k in sfields})
        tmp2.replace(sample)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    if not (args.out / "gallery.html").exists():
        print(
            f"ERROR: {args.out / 'gallery.html'} missing.\n"
            "Run: PYTHONPATH=. .venv/bin/python scripts/build_owner_side_review_pack.py",
            file=sys.stderr,
        )
        return 1
    ReviewHandler.out_dir = args.out.resolve()
    ReviewHandler.src_dir = args.src.resolve()
    httpd = ThreadingHTTPServer((args.host, args.port), ReviewHandler)
    url = f"http://{args.host}:{args.port}/gallery.html"
    print(f"Serving {args.out}")
    print(f"Open → {url}")
    print("Keys: L=long  S=short  K/X=skip  N/P=next/prev  1=sample  2=full  U=unlabeled")
    print("Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

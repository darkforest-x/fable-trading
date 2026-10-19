"""Archive explicitly selected public RektProof threads and their original chart assets.

The owner's 2026-10-15 learning-workspace request requires attributable originals,
not reconstructed charts. Only the main author thread is parsed, not related posts.
Images stay in the gitignored local handbook directory. Each image keeps its exact
source URL, post ID, retrieval time, SHA256 and dimensions. No account or trading
endpoint is used. Run with --output <local handbook directory>.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import urllib.request

from bs4 import BeautifulSoup
from PIL import Image

THREADS = [
    ("standard", "1678662673402335232", "标准形态：区间、供需与三次触碰"),
    ("range", "1282920288485687296", "区间策略"),
    ("stops", "1258334168963928066", "提前退出与硬止损"),
    ("timeframes", "1247567599661019136", "多周期方向与交易计划"),
    ("short_case", "1234536350361088001", "供给区做空逐步拆解"),
    ("eth_case", "1237397285463457793", "ETH 大小周期区间套用"),
    ("atom_case", "1253589278614908928", "ATOM 计划、持仓与退出更新"),
    ("liquidity", "1448884052228988947", "供需区与流动性"),
    ("structure", "1291615193227640832", "有效结构与结构破坏"),
    ("review", "1260126112312471553", "历史行情拆解方法"),
    ("btc_exit", "1183959387896836096", "BTC：开仓、近保本退出与管理失误复盘"),
    ("eth_po3_2021", "1371626986313900032", "ETH：PO3 计划、成交更新与结束"),
]

SINGLE_POSTS = [
    ("profile_2026", "2008779823360151689", "2026 原图：市场剖面与区间"),
    ("reversal_2026", "2071624735272931387", "2026 原图：反转形态（含原图数字勘误）"),
    ("continuation_2026", "2074483941000851763", "2026 原图：趋势延续形态"),
]


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=45) as response:
        return response.read()


def collect_thread(spec: tuple[str, str, str], output: Path) -> dict:
    key, post_id, title = spec
    url = f"https://threadreaderapp.com/thread/{post_id}.html"
    raw_path = output / "sources" / f"{key}.html"
    if not raw_path.exists():
        raw_path.write_bytes(fetch(url))
    soup = BeautifulSoup(raw_path.read_bytes(), "html.parser")
    main = soup.select_one(".t-main") or soup.select_one(".container.narrow")
    if main is None:
        raise ValueError(f"Author thread missing: {url}")
    posts, pictures = [], []
    for node in main.select(".content-tweet[data-tweet]"):
        if node.get("data-screenname", "").lower() != "rektproof":
            raise ValueError(f"Unexpected author: {url}")
        tweet_id = node["data-tweet"]
        published = datetime.fromtimestamp(((int(tweet_id) >> 22) + 1288834974657) / 1000, timezone.utc).isoformat()
        assets = []
        for im in node.select("img[data-src]"):
            src = im["data-src"]
            if not re.fullmatch(r"https://pbs\.twimg\.com/media/[A-Za-z0-9_-]+\.(jpg|png|jpeg)", src):
                continue
            name = src.rsplit("/", 1)[1]
            path = output / "images" / name
            if not path.exists():
                path.write_bytes(fetch(src))
            with Image.open(path) as image:
                width, height = image.size
                image.verify()
            asset = {"path": f"images/{name}", "url": src, "post_id": tweet_id,
                     "published_at": published, "retrieved_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(), "width": width, "height": height,
                     "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            assets.append(asset)
            pictures.append(asset)
        for br in node.select("br"):
            br.replace_with("\n")
        for extra in node.select("sup"):
            extra.decompose()
        posts.append({"id": tweet_id, "published_at": published,
                      "text": node.get_text(" ", strip=True), "images": assets,
                      "url": f"https://x.com/RektProof/status/{tweet_id}"})
    if not posts or posts[0]["id"] != post_id:
        raise ValueError(f"Thread identity mismatch: {url}")
    return {"id": key, "title": title, "url": f"https://x.com/RektProof/status/{post_id}",
            "mirror": url, "source_captured_at": datetime.fromtimestamp(raw_path.stat().st_mtime, timezone.utc).isoformat(), "timestamp_basis": "Original X post ID snowflake, UTC", "posts": posts, "images": pictures,
            "provenance": "Author posts and original image URLs preserved by a third-party mirror; X not independently re-fetched."}


def collect_single(spec: tuple[str, str, str], output: Path) -> dict:
    """Archive a public status using FxEmbed's documented Status Fetch API.

    Source: https://github.com/FxEmbed/FxEmbed/wiki/Status-Fetch-API
    Only tweet.media.photos belongs to this post; quoted media must stay separate.
    Browser verification is retained only when exact post and media IDs match.
    """
    key, post_id, title = spec
    api = f"https://api.fxtwitter.com/RektProof/status/{post_id}"
    raw_path = output / "sources" / f"fx_{post_id}.json"
    if not raw_path.exists():
        raw_path.write_bytes(fetch(api))
    response = json.loads(raw_path.read_bytes())
    post = response.get("tweet") or {}
    if response.get("code") != 200 or str(post.get("id")) != post_id or post.get("author", {}).get("screen_name", "").lower() != "rektproof":
        raise ValueError(f"Post identity mismatch: {api}")
    published = datetime.strptime(post["created_at"], "%a %b %d %H:%M:%S %z %Y").isoformat()
    assets = []
    for media in post.get("media", {}).get("photos", []):
        src = media["url"]
        if not re.fullmatch(r"https://pbs\.twimg\.com/media/[A-Za-z0-9_-]+\.(jpg|png|jpeg)\?name=orig", src):
            raise ValueError(f"Unexpected original-media URL: {src}")
        name = src.rsplit("/", 1)[1].split("?")[0]
        path = output / "images" / name
        if not path.exists():
            path.write_bytes(fetch(src))
        with Image.open(path) as image:
            width, height = image.size
            image.verify()
        assets.append(dict(path=f"images/{name}", url=src, post_id=post_id, published_at=published,
                           retrieved_at=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                           width=width, height=height, sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    if not assets:
        raise ValueError(f"No original photos: {api}")
    url = f"https://x.com/RektProof/status/{post_id}"
    row = dict(id=key, title=title, url=url, mirror=api, mirror_label="公开嵌入数据",
               source_captured_at=datetime.fromtimestamp(raw_path.stat().st_mtime, timezone.utc).isoformat(),
               timestamp_basis="Public status created_at, UTC", images=assets,
               posts=[dict(id=post_id, published_at=published, text=post["text"], images=assets, url=url)],
               provenance="FxEmbed public status and original X media; quoted-post images excluded.")
    receipt = output / "sources" / "x_browser_20261015.json"
    if receipt.exists():
        verification = json.loads(receipt.read_text())
        match = next((p for p in verification["posts"] if f"/RektProof/status/{post_id}/photo/1" in p.get("links", [])), None)
        # A quote may contain another post's photo; require the post's own timestamp too.
        times = match.get("time", []) if match else []
        times = times if isinstance(times, list) else [times]
        exact_time = datetime.fromisoformat(published)
        if match and any(datetime.fromisoformat(t.replace("Z", "+00:00")) == exact_time for t in times):
            stems = [Path(a["path"]).stem for a in assets]
            if all(any(f"/media/{stem}?" in u for u in match["images"]) for stem in stems):
                row["verified_on_x_at"] = verification["captured_at"]
                row["provenance"] += " Exact post, UTC timestamp and media IDs also checked on X in browser."
    return row


def collect(output: Path) -> dict:
    for folder in ("sources", "images"):
        (output / folder).mkdir(parents=True, exist_ok=True)
    def one(spec):
        try:
            return collect_single(spec, output) if spec in SINGLE_POSTS else collect_thread(spec, output)
        except Exception as error:
            return {"id": spec[0], "title": spec[2], "error": str(error), "posts": [], "images": []}
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(one, SINGLE_POSTS + THREADS))
    case_receipt = output / "sources" / "x_cases_browser_20261015.json"
    if case_receipt.exists():
        checked = json.loads(case_receipt.read_text())
        for row in rows:
            verified = []
            for post in row["posts"]:
                matches = [p for p in checked["posts"] if f'/RektProof/status/{post["id"]}/photo/1' in p.get("links", [])]
                for match in matches:
                    same_second = int(datetime.fromisoformat(match["time"].replace("Z", "+00:00")).timestamp()) == int(datetime.fromisoformat(post["published_at"]).timestamp())
                    same_media = len(match["images"]) == len(post["images"]) and all(any(f'/media/{Path(im["path"]).stem}?' in u for u in match["images"]) for im in post["images"])
                    if same_second and same_media:
                        verified.append(post["id"])
            if verified:
                row["x_verified_post_ids"] = verified
                row["x_verification_captured_at"] = checked["captured_at"]
                row["provenance"] = f'Author thread archived from mirror; {len(verified)}/{len(row["posts"])} post timestamps and photo IDs also checked directly on X. See x_cases_browser_20261015.json.'
    manifest = {"retrieved_at": datetime.now(timezone.utc).isoformat(), "threads": rows,
                "source_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (output / "source_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    manifest = collect(parser.parse_args().output)
    print(json.dumps({"threads": len(manifest["threads"]),
                      "images": sum(len(x["images"]) for x in manifest["threads"]),
                      "failures": [{"id": x["id"], "error": x["error"]} for x in manifest["threads"] if "error" in x]}, ensure_ascii=False))

"""Collect only explicitly open official ChartPrime Pine publications.

Reads public publication SSR metadata and its advertised public Pine ID.
The public get route was observed to return scriptAccess=open_no_auth for
AtJtdaDe. It is NOT called for protected/invite-only publications. Retains
license headers, exact source hashes and unavailable states; never executes
Pine or loads market CSVs. Python stdlib networking/JSON only.
Sources: https://www.tradingview.com/pine-script-docs/writing/publishing/
https://www.tradingview.com/pine-script-docs/concepts/repainting/
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import urllib.parse
import urllib.request


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=35) as response:
        return response.read().decode("utf-8"), response.status, response.url


def extract_card(html):
    for text in re.findall(r'<script[^>]*type="application/prs.init-data\+json"[^>]*>(.*?)</script>', html, re.S):
        for value in json.loads(text).values():
            if isinstance(value, dict) and "ssrIdeaData" in value:
                return value["ssrIdeaData"]
    raise ValueError("Official publication metadata not found")


def flatten_ast(node):
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(flatten_ast(x) for x in node)
    if isinstance(node, dict):
        return flatten_ast(node.get("children", []))
    return ""


def collect(item, folder):
    script_id = item["id"]
    if not re.fullmatch(r"[A-Za-z0-9]{8}", script_id):
        raise ValueError("Unsafe/noncanonical script ID")
    target = folder/(script_id+".json")
    if target.exists():
        saved = json.loads(target.read_text())
        if saved["id"] != script_id or saved["url"] != item["url"]:
            raise ValueError("Existing identity changed")
        return saved
    record = {"id":script_id, "title":item["title"], "url":item["url"],
              "retrieved_at":datetime.now(timezone.utc).isoformat()}
    try:
        html, status, final_url = fetch(item["url"])
        card = extract_card(html)
        if card["uuid"] != script_id or card["user"]["username"] != "ChartPrime":
            raise ValueError("Publication owner or ID mismatch")
        script = card["script"]
        record.update(title=card["name"], http_status=status, final_url=final_url,
            created_at=card.get("created_at"), updated_at=card.get("updated_at"),
            description=flatten_ast(card.get("description_ast", {})),
            script=script, evidence_level="official_description_only")
        if script.get("access") == 1 and script.get("has_access") is True:
            source_url = "https://pine-facade.tradingview.com/pine-facade/get/{}/{}".format(
                urllib.parse.quote(script["script_id_part"], safe=""), script["version_maj"])
            payload, source_status, _ = fetch(source_url)
            source_data = json.loads(payload)
            if source_data.get("scriptAccess") != "open_no_auth":
                raise ValueError("Source access is not explicitly public open_no_auth")
            source = source_data["source"]
            if not isinstance(source, str) or not source.strip():
                raise ValueError("Open source empty")
            record.update(evidence_level="official_source_retrieved_not_yet_manually_reviewed",
                source_url=source_url, source_http_status=source_status,
                source_sha256=sha256(source.encode()).hexdigest(),
                source_lines=len(source.splitlines()),
                source_metadata={k:v for k,v in source_data.items() if k != "source"})
            pine_path = folder/(script_id+".pine")
            with pine_path.open("x") as stream:
                stream.write(source)
        else:
            record["source_unavailable_reason"] = "Not explicitly open; source endpoint not requested"
    except Exception as error:
        record.update(error_type=type(error).__name__, error=str(error),
                      evidence_level=record.get("evidence_level", "unavailable"))
    with target.open("x") as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2)
    return record


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--catalogue", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args=parser.parse_args()
    catalogue=json.loads(args.catalogue.read_text())
    items=catalogue["scripts"]
    if len({x["id"] for x in items}) != len(items):
        raise ValueError("Duplicate catalogue IDs")
    args.out.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures=[pool.submit(collect, item, args.out) for item in items]
        for future in as_completed(futures):
            r=future.result()
            print(json.dumps({"id":r["id"],"title":r["title"],
                "status":r["evidence_level"],"error":r.get("error")},ensure_ascii=False),flush=True)


if __name__ == "__main__":
    main()

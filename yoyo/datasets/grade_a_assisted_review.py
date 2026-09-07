"""Owner-assisted Grade-A review with physically separate future context.

Owner requested a simpler review and 40 additional 15m candles on 2026-09-07.
The frozen W18 candidate input is replayed byte-for-byte; W58 is review-only,
uses OHLC plus causal SMA/EMA columns computed by the frozen mining loader,
and is never written into a training image or label directory. Proposal boxes
are transported through bar/price coordinates, not re-estimated from outcomes.
Answers are schema 2 aided judgments: rejecting a proposal is not a background
label. Original blind-review evidence remains unchanged and incomparable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.parse import urlparse

from yoyo.datasets import grade_a_calibration as old

ROOT = old.ROOT
EXPERIMENT = old.EXPERIMENT
PROTOCOL = EXPERIMENT / "assisted_v2_protocol.json"
PACK = ROOT / "datasets/grade_a_owner_assisted_20260907_v2"
PROTOCOL_ID = "assisted_future40_v2"
RESULTS = EXPERIMENT / "results/assisted_v2"
FUTURE_BARS = 40
KEYS = {"review_id", "verdict", "reason", "note", "corrected_direction",
        "corrected_box", "future_bars_seen", "answered_at"}
VERDICTS = {"ACCEPT", "REJECT", "UNCERTAIN"}
REASONS = {"NO_TARGET", "WRONG_SIDE", "WRONG_BOX", "OTHER"}


def read_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def box(row: dict) -> list[float]:
    cx, cy, w, h = [float(row[f"prediction_{k}_norm"]) for k in ("cx", "cy", "w", "h")]
    result = [cx-w/2, cy-h/2, cx+w/2, cy+h/2]
    validate_box(result)
    return result


def validate_box(value: object) -> None:
    if not isinstance(value, list) or len(value) != 4 or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in value):
        raise ValueError("box must have four finite normalized edges")
    if not value[0] < value[2] or not value[1] < value[3]:
        raise ValueError("box edges must be ordered")


def transport_box(bounds: list[float], source, target) -> list[float]:
    """Preserve continuous bar and price coordinates between same-start windows."""
    validate_box(bounds)
    def x(v):
        bar = (v*source.width-source.left) / source.plot_w * (source.n_bars-1)
        return (target.left + bar/(target.n_bars-1)*target.plot_w) / target.width
    def y(v):
        price = source.price_max - (v*source.height-source.top)/source.plot_h*(source.price_max-source.price_min)
        return (target.top + (target.price_max-price)/(target.price_max-target.price_min)*target.plot_h) / target.height
    result = [x(bounds[0]), y(bounds[1]), x(bounds[2]), y(bounds[3])]
    validate_box(result)
    return result


def check_target(row: dict) -> datetime:
    """Reject a future review crossing the fixed development bound before raw reads."""
    start, end = old.instant(row["window_start_time"]), old.instant(row["window_end_time"])
    target = end + FUTURE_BARS*old.BAR
    old.assert_pre_holdout(target + old.BAR, what="human review window close")
    if start > end or end-start != 17*old.BAR or target > old.instant("2025-11-02T09:45:00Z"):
        raise ValueError("review context exceeds frozen source period")
    if not re.fullmatch(r"20\d\d-(0[1-9]|1[0-2])", row["source_month"]) or row["source_month"] > "2025-10":
        raise ValueError("invalid source month")
    return target


def preserved_inputs() -> dict[str, str]:
    """Snapshot the old pack, including any existing answers, and dataset contract."""
    paths = [p for p in old.PACK.rglob('*') if p.is_file()]
    paths += [old.DATASET / "manifest.jsonl", old.DATASET / "data.yaml"]
    return {p.relative_to(ROOT).as_posix():old.sha(p) for p in sorted(paths) if p.exists()}


def verify_preserved(before: dict[str, str]) -> None:
    for path, digest in before.items():
        old.assert_digest(ROOT/path, digest)


def build(*, images_only: bool = False) -> dict:
    import cv2
    from scripts import mine_15m_ma_launch_grade_a_daily_movers_5000 as mine

    if PACK.exists():
        raise ValueError("assisted pack exists; never overwrite review answers")
    source_paths=[Path(__file__).resolve(), PROTOCOL]
    if not images_only:
        source_paths.append(ROOT/"yoyo/datasets/templates/grade_a_assisted_review.html")
    source_commit = old.assert_source_first(source_paths)
    protocol = json.loads(PROTOCOL.read_text())
    if protocol["future_bars"] != FUTURE_BARS or protocol["protocol_id"] != PROTOCOL_ID:
        raise ValueError("protocol mismatch")
    for path, digest in protocol["inputs"].items():
        old.assert_digest(ROOT/path, digest)
    selected = read_lines(old.PACK/"admin/selected_source_rows.jsonl")
    truth = read_lines(old.PACK/"admin/truth.jsonl")
    for row in selected:
        check_target(row)
    before = preserved_inputs()
    PACK.mkdir(parents=True)
    (PACK/"public/input_images").mkdir(parents=True)
    (PACK/"review_future_only/images").mkdir(parents=True)
    old.dump(PACK/"admin/input_identity_before.json", before)
    old.dump(RESULTS/"started.json", {"source_commit":source_commit, "future_bars":FUTURE_BARS,
        "status":"building", "protocol_sha256":old.sha(PROTOCOL)})
    parent = json.loads((old.MINING/"preregistration.json").read_text())
    archive_root = ROOT/parent["data"]["archive_root"]
    grouped = defaultdict(list)
    for row in selected:
        grouped[row["source_month"]].append(row)
    images, source_receipts = {}, []
    for month, rows in sorted(grouped.items()):
        names = {r["exchange_symbol"] for r in rows}
        source_manifest = old.MINING/f"results/shards/{month}/source_manifest.json"
        records = [r for r in json.loads(source_manifest.read_text())["archives"] if r["symbol"] in names and "selected_symbol_context" in r["role"]]
        allowed = {r["path"]:r for r in records}
        # Every archive that the legacy loader can open is checked before any raw read.
        for r in records:
            old.assert_pre_holdout(r["last_bar_open"], what="archive receipt before opening")
            if old.instant(r["last_bar_open"]) > old.instant("2025-11-30T23:45:00Z"):
                raise ValueError("archive outside frozen context period")
        for name in names:
            for adjacent in mine.adjacent_months(month):
                path = mine.prior.month_archive_path(archive_root, name, adjacent)
                if path.exists() and path.relative_to(ROOT).as_posix() not in allowed:
                    raise ValueError("loader would open an unrecorded archive")
        for r in records:
            old.assert_digest(ROOT/r["path"], r["sha256"])
        frames, receipts = mine.load_selected_frames(parent, month=month, archive_root=archive_root, symbols=sorted(names))
        for r in receipts:
            if r["sha256"] != allowed[r["path"]]["sha256"]:
                raise ValueError("archive changed during loading")
        for row in rows:
            start, end = int(row["window_start_i"]), int(row["window_end_i"])
            frame = frames[row["exchange_symbol"]]
            window = frame.iloc[start:end+FUTURE_BARS+1]
            target = check_target(row)
            times = [mine.utc(v).to_pydatetime() for v in window["open_time"]]
            if len(times) != 58 or times[0] != old.instant(row["window_start_time"]) or times[17] != old.instant(row["window_end_time"]) or times[-1] != target or any(b-a != old.BAR for a,b in zip(times,times[1:])):
                raise ValueError("review must contain exact 18+40 continuous candles")
            raw, source_tf = mine.render_chart(window.iloc[:18], out_path=None)
            if mine.pixel_sha256(raw) != row["input_pixel_sha256"]:
                raise ValueError("original W18 pixel replay mismatch")
            future, future_tf = mine.render_chart(window, out_path=None)
            rid = old.rank(protocol["seed"], "blind", row["event_id"], 0)[:24]
            path = PACK/f"review_future_only/images/{rid}.png"
            if not cv2.imwrite(str(path), future, [cv2.IMWRITE_PNG_COMPRESSION,4]):
                raise ValueError("future PNG write failed")
            if mine.pixel_sha256(cv2.imread(str(path))) != mine.pixel_sha256(future):
                raise ValueError("future PNG roundtrip mismatch")
            original_box = box(row)
            images[row["event_id"]] = {"primary_id":rid,"proposal_direction":row["model_direction"],
                "proposal_core":[int(row["core_start_local"])+1,int(row["core_end_local"])+1],
                "proposal_box":original_box,"future_proposal_box":transport_box(original_box,source_tf,future_tf),
                "input_end_norm":(future_tf.left+17.5/57*future_tf.plot_w)/future_tf.width,
                "future_image_sha256":old.sha(path),"input_pixel_sha256":row["input_pixel_sha256"],
                "input_end_bar_open":row["window_end_time"],"future_end_bar_open":target.isoformat(),
                "input_available_at":(old.instant(row["window_end_time"])+old.BAR).isoformat(),
                "review_available_at":(target+old.BAR).isoformat()}
        for r in records:
            old.assert_digest(ROOT/r["path"], r["sha256"])
        source_receipts.extend(receipts)
        print(f"future40 {month}: {len(rows)} events", flush=True)
    items, future_manifest, lineage = [], [], []
    for t in truth:
        info = images[t["event_id"]]; rid=t["review_id"]
        source = old.PACK/f"public/images/{rid}.png"
        old.assert_digest(source,t["image_sha256"])
        shutil.copyfile(source,PACK/f"public/input_images/{rid}.png")
        if rid != info["primary_id"]:
            shutil.copyfile(PACK/f"review_future_only/images/{info['primary_id']}.png",PACK/f"review_future_only/images/{rid}.png")
        item = {k:info[k] for k in ("proposal_direction","proposal_core","proposal_box","future_proposal_box","input_end_norm")}
        items.append({**item,"review_id":rid,"image":f"input_images/{rid}.png",
            "future_image":f"review_future_only/images/{rid}.png","n_bars":18,"future_bars":40})
        future_manifest.append({"review_id":rid,"image":f"images/{rid}.png", "sha256":info["future_image_sha256"],
            "window_bars":58,"additional_future_bars":40,"review_only":True,
            "training_eligible":False,"future_data_in_training_image":False,"future_data_in_training_label":False,
            **{k:info[k] for k in ("input_end_bar_open","future_end_bar_open","input_available_at","review_available_at")}})
        lineage.append({**t,"protocol_id":PROTOCOL_ID,"proposal_exposed":True,"future_exposed":True,
            "input_copy_sha256":old.sha(PACK/f"public/input_images/{rid}.png"),"future_image_sha256":info["future_image_sha256"]})
    manifest={"schema_version":2,"pack_id":PACK.name,"protocol_id":PROTOCOL_ID,"default_future_bars":40,"items":items}
    old.dump(PACK/"public/manifest.json",manifest)
    old.dump(PACK/"review_future_only/manifest.json",{"protocol_id":PROTOCOL_ID,"training_eligible":False,"items":future_manifest})
    old.jsonl(PACK/"admin/lineage.jsonl",lineage)
    old.dump(PACK/"admin/source_receipts.json",source_receipts)
    # Keep original selection and record the expanded human dependency audit separately.
    intervals=old.membership_intervals(old.DATASET/"manifest.jsonl",150)
    expanded=[r["event_id"] for r in selected if old.overlaps(intervals.get(old.symbol(r["exchange_symbol"]),[]),old.instant(r["window_start_time"]),check_target(r))]
    old.dump(PACK/"admin/future_dependency_audit.json",{"expanded_overlap_events":expanded,"count":len(expanded),"selection_changed":False,"independence_status":"not_established"})
    payload={**manifest,"manifest_sha256":old.sha(PACK/"public/manifest.json")}
    safe=json.dumps(payload,ensure_ascii=False).replace('<','\\u003c').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
    if not images_only:
        template=(ROOT/"yoyo/datasets/templates/grade_a_assisted_review.html").read_text()
        if template.count('__PACK_JSON__') != 1:
            raise ValueError("invalid template payload placeholder")
        (PACK/"public/index.html").write_text(template.replace('__PACK_JSON__',safe))
    verify_preserved(before)
    if any('labels' in p.parts or p.suffix=='.txt' for p in (PACK/'review_future_only').rglob('*')):
        raise ValueError("review future directory contains labels")
    receipt={"schema_version":2,"source_commit":source_commit,"protocol_id":PROTOCOL_ID,"pack_id":PACK.name,
        "generated_at":datetime.now(timezone.utc).isoformat(),"unique_events":len(selected),"review_items":len(items),"repeats":len(items)-len(selected),
        "future_bars":40,"original_input_replay_passed":len(images),"preserved_old_file_hashes":len(before),"old_inputs_unchanged":True,
        "manifest_sha256":old.sha(PACK/'public/manifest.json'),"future_manifest_sha256":old.sha(PACK/'review_future_only/manifest.json'),
        "public_html_sha256":None if images_only else old.sha(PACK/'public/index.html'),"lineage_sha256":old.sha(PACK/'admin/lineage.jsonl'),
        "expanded_human_dependency_overlaps":len(expanded),"latest_future_bar_open":max(i['future_end_bar_open'] for i in future_manifest),
        "training_eligible":False,"production_eligible":False,"holdout_read":False,"new_inference":False,
        "review_mode":"assisted_with_future_context; not blind class/geometry annotation", "status":"awaiting_ui" if images_only else "ready_for_owner"}
    old.dump(RESULTS/"build_receipt.json",receipt)
    return receipt


def publish_ui() -> None:
    """Attach a committed UI to fixed images without changing IDs or Owner answers."""
    template=ROOT/'yoyo/datasets/templates/grade_a_assisted_review.html'
    commit=old.assert_source_first([Path(__file__).resolve(),template])
    receipt_path=RESULTS/'build_receipt.json';receipt=json.loads(receipt_path.read_text())
    old.assert_digest(PACK/'public/manifest.json',receipt['manifest_sha256'])
    old.assert_digest(PACK/'admin/lineage.jsonl',receipt['lineage_sha256'])
    page=PACK/'public/index.html'
    if page.exists():
        old.assert_digest(page,receipt['public_html_sha256'])
        history=RESULTS/'ui_history'/receipt['public_html_sha256'];history.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(page,history/'index.html');shutil.copyfile(receipt_path,history/'build_receipt.json')
    raw=template.read_text()
    if raw.count('__PACK_JSON__')!=1:raise ValueError('invalid template placeholder')
    payload={**json.loads((PACK/'public/manifest.json').read_text()),'manifest_sha256':receipt['manifest_sha256']}
    safe=json.dumps(payload,ensure_ascii=False).replace('<','\\u003c').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
    page.write_text(raw.replace('__PACK_JSON__',safe))
    receipt.update(public_html_sha256=old.sha(page),ui_source_commit=commit,status='ready_for_owner')
    old.dump(receipt_path,receipt)
    print('Committed UI attached; review IDs, images and previous answers unchanged')


def validate_snapshot(export: dict, pack: Path) -> tuple[int,int]:
    manifest_path=pack/'public/manifest.json';manifest=json.loads(manifest_path.read_text())
    if not isinstance(export,dict) or type(export.get('schema_version')) is not int or export['schema_version']!=2 or export.get('pack_id')!=manifest['pack_id'] or export.get('manifest_sha256')!=old.sha(manifest_path) or export.get('protocol_id')!=manifest['protocol_id']:
        raise ValueError('wrong assisted-review identity; blind answers are not interchangeable')
    old.review_timestamp(export['exported_at'])
    rows=export.get('answers');items={x['review_id']:x for x in manifest['items']}
    if not isinstance(rows,list) or len(rows)>len(items): raise ValueError('invalid answer list')
    seen=set();completed=0
    for a in rows:
        if not isinstance(a,dict) or not KEYS.issubset(a): raise ValueError('incomplete answer fields')
        rid=a['review_id']
        if not isinstance(rid,str) or rid not in items or rid in seen: raise ValueError('duplicate or foreign ID')
        seen.add(rid)
        if a['verdict'] is not None and a['verdict'] not in VERDICTS: raise ValueError('invalid verdict')
        if a['reason'] is not None and a['reason'] not in REASONS: raise ValueError('invalid reason')
        if not isinstance(a['note'],str) or len(a['note'])>4000: raise ValueError('invalid note')
        if a['corrected_direction'] is not None and a['corrected_direction'] not in {'LONG','SHORT'}: raise ValueError('invalid corrected direction')
        if a['corrected_box'] is not None: validate_box(a['corrected_box'])
        if type(a['future_bars_seen']) is not int or a['future_bars_seen']!=items[rid]['future_bars']: raise ValueError('future context mismatch')
        if a['verdict']!='REJECT' and any(a[k] is not None for k in ('reason','corrected_direction','corrected_box')): raise ValueError('non-reject retains corrections')
        if a['reason']=='NO_TARGET' and (a['corrected_direction'] is not None or a['corrected_box'] is not None): raise ValueError('no-target retains a correction')
        if a['answered_at'] is not None:
            old.review_timestamp(a['answered_at'])
            if a['verdict'] is None: raise ValueError('saved answer has no verdict')
            completed+=1
    return completed,len(items)


def save_snapshot(export: dict, pack: Path) -> dict:
    completed,total=validate_snapshot(export,pack)
    content=json.dumps(export,ensure_ascii=False,sort_keys=True,indent=2)+'\n';digest=hashlib.sha256(content.encode()).hexdigest()
    folder=pack/'answers';folder.mkdir(exist_ok=True)
    previous=sorted(folder.glob('answers_*.json'))
    if previous:
        latest=json.loads(previous[-1].read_text())
        previous_time=old.review_timestamp(latest['exported_at'])
        current_time=old.review_timestamp(export['exported_at'])
        if current_time<previous_time or (current_time==previous_time and latest!=export):
            raise ValueError('stale snapshot; retry with current progress')
    name='answers_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'_'+digest[:12]+'.json'
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=folder,delete=False,prefix='.pending_') as f:
        f.write(content);temporary=Path(f.name)
    temporary.rename(folder/name)
    return {'saved':True,'filename':name,'sha256':digest,'saved_answers':completed,'total':total}


def score(export: dict, pack: Path = PACK) -> dict:
    completed,total=validate_snapshot(export,pack)
    answers={a['review_id']:a for a in export['answers'] if a['answered_at'] is not None}
    rows=read_lines(pack/'admin/lineage.jsonl')
    manifest=json.loads((pack/'public/manifest.json').read_text())
    ids={i['review_id'] for i in manifest['items']}
    if len(rows)!=len(ids) or {r['review_id'] for r in rows}!=ids:
        raise ValueError('lineage must have exactly one row per public review ID')
    primary_ids={r['review_id'] for r in rows if r['is_primary'] is True}
    if any(type(r['is_primary']) is not bool or (r['is_primary'] and r['repeat_of_review_id'] is not None) or (not r['is_primary'] and r['repeat_of_review_id'] not in primary_ids) for r in rows):
        raise ValueError('invalid repeat-to-primary lineage')
    if pack.resolve()==PACK.resolve():
        receipt=json.loads((RESULTS/'build_receipt.json').read_text())
        old.assert_digest(pack/'admin/lineage.jsonl',receipt['lineage_sha256'])
        old.assert_digest(pack/'public/manifest.json',receipt['manifest_sha256'])
    primary=[r for r in rows if r['is_primary'] and r['review_id'] in answers]
    pairs=[(answers[r['review_id']]['verdict'],answers[r['repeat_of_review_id']]['verdict']) for r in rows if not r['is_primary'] and r['review_id'] in answers and r['repeat_of_review_id'] in answers]
    left,right=Counter(a for a,b in pairs),Counter(b for a,b in pairs);n=len(pairs)
    agreement=sum(a==b for a,b in pairs)/n if n else None
    expected=sum(left[k]*right[k] for k in VERDICTS)/n**2 if n else None
    return {'protocol_id':PROTOCOL_ID,'completed':completed,'total':total,'primary_completed':len(primary),
        'primary_verdicts':dict(Counter(answers[r['review_id']]['verdict'] for r in primary)),
        'explicit_no_target':sum(answers[r['review_id']]['verdict']=='REJECT' and answers[r['review_id']]['reason']=='NO_TARGET' for r in primary),
        'complete_repeat_pairs':n,'repeat_agreement':agreement,'assisted_repeat_kappa':(agreement-expected)/(1-expected) if expected is not None and expected<1 else None,
        'training_eligible':False,'production_eligible':False,'labels_mutated':False,
        'interpretation':'Assisted proposal review with 40 future candles; REJECT alone is not NO_SIGNAL. Not comparable to blind schema1 kappa.'}


def serve(pack: Path, port: int) -> None:
    """Serialize explicit local saves; expose only whitelisted review assets."""
    save_lock=Lock()
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)
        def send_json(self,status,body):
            raw=json.dumps(body,ensure_ascii=False).encode();self.send_response(status)
            self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store')
            self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        def do_GET(self):
            path=urlparse(self.path).path
            if path=='/api/latest':
                files=sorted((pack/'answers').glob('answers_*.json'))
                if not files: self.send_json(404,{'error':'no local answers'});return
                try:
                    value=json.loads(files[-1].read_text());validate_snapshot(value,pack)
                except (ValueError,TypeError,KeyError,AttributeError) as e:
                    self.send_json(409,{'error':'saved snapshot cannot be restored: '+str(e)});return
                self.send_json(200,value);return
            if path in {'/','/index.html'}: asset=pack/'public/index.html'
            elif path=='/manifest.json': asset=pack/'public/manifest.json'
            elif re.fullmatch(r'/input_images/[a-f0-9]{24}\.png',path): asset=pack/'public'/path.lstrip('/')
            elif re.fullmatch(r'/review_future_only/images/[a-f0-9]{24}\.png',path): asset=pack/path.lstrip('/')
            else: self.send_error(404);return
            if not asset.is_file(): self.send_error(404);return
            raw=asset.read_bytes();self.send_response(200)
            self.send_header('Content-Type','image/png' if asset.suffix=='.png' else 'text/html; charset=utf-8' if asset.suffix=='.html' else 'application/json')
            self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        def do_POST(self):
            if self.path!='/api/save': self.send_error(404);return
            if self.headers.get('Origin') not in {f'http://127.0.0.1:{port}',f'http://localhost:{port}'}:
                self.send_json(403,{'error':'local same-origin save required'});return
            try:
                with save_lock:
                    length=int(self.headers.get('Content-Length','0'))
                    if not 0<length<=2_000_000: raise ValueError('invalid snapshot length')
                    value=json.loads(self.rfile.read(length));result=save_snapshot(value,pack)
            except (ValueError,KeyError,TypeError,AttributeError,TimeoutError) as e:
                self.send_json(400,{'error':str(e)});return
            self.send_json(200,result)
    print(f'Assisted review http://127.0.0.1:{port}; snapshots: {pack / "answers"}',flush=True)
    ThreadingHTTPServer(('127.0.0.1',port),Handler).serve_forever()


def main() -> None:
    p=argparse.ArgumentParser(description=__doc__);subs=p.add_subparsers(dest='command',required=True)
    subs.add_parser('build');subs.add_parser('build-images');subs.add_parser('publish-ui')
    s=subs.add_parser('serve');s.add_argument('--pack',type=Path,default=PACK);s.add_argument('--port',type=int,default=8769)
    s=subs.add_parser('score');s.add_argument('--answers',type=Path,required=True);s.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if args.command=='build': print(json.dumps(build(),ensure_ascii=False,indent=2))
    elif args.command=='build-images': print(json.dumps(build(images_only=True),ensure_ascii=False,indent=2))
    elif args.command=='publish-ui': publish_ui()
    elif args.command=='serve': serve(args.pack,args.port)
    else:
        if args.output.exists(): raise ValueError('score output exists')
        result=score(json.loads(args.answers.read_text()));result['answer_sha256']=old.sha(args.answers);old.dump(args.output,result)
        print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__': main()

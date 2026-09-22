"""Fail-closed cross-machine orchestration for morphology-negative v6.

Modes are explicit: freeze, stage, start, snapshot and collect.  No mode
trains locally; start submits one WMI process and never retries an uncertain
submission.  The remote upload reuses only same-SHA files and rejects every
collision with different content.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import subprocess
import tarfile
import time
from pathlib import Path
from typing import Any, Mapping

from scripts.research.watch_ma_profit3r_owner1500_v4 import HOST, SSH
from yoyo.evaluation.ma_morphology_economics import CONTROL_PATHS
from yoyo.evaluation.ma_morphology_delivery import BASELINE, deliver

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-ma-morphology-negatives-20260922-v3"
DATA = ROOT / "datasets/ma_launch_owner1500_morph_v6"
REMOTE_ROOT = "C:/fable"
REMOTE_EXP = f"{REMOTE_ROOT}/{EXP.relative_to(ROOT).as_posix()}"
REMOTE_DATA = f"{REMOTE_ROOT}/{DATA.relative_to(ROOT).as_posix()}"
RUN = "C:/fable/runs/ma_launch_owner1500_morph_v6"
REMOTE_PYTHON = "C:/fable/.venv/Scripts/python.exe"
NEW_CODE = (
    "scripts/research/run_ma_morphology_redo.py",
    "scripts/windows/complete_ma_morphology_redo.py",
    "scripts/windows/run_ma_morphology_redo.py",
    "yoyo/datasets/ma_morphology_redo.py",
    "yoyo/datasets/ma_morphology_background.py",
    "yoyo/datasets/ma_morphology_review.py",
    "yoyo/evaluation/ma_morphology_economics.py",
    "yoyo/evaluation/ma_morphology_delivery.py",
    "yoyo/evaluation/ma_profit_control_metrics.py",
)


class OrchestrationError(RuntimeError):
    """Raised when a local or remote immutable launch contract is unsafe."""


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise OrchestrationError(f"JSON object required: {path}")
    return value


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


def _state(status: str, **extra: Any) -> None:
    write_json(EXP / "orchestration_status.json", {"status": status, "updated_unix": time.time(), **extra})


def _require_committed(paths: list[Path]) -> None:
    if subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip() != "main":
        raise OrchestrationError("freeze requires main")
    for path in paths:
        relative = _relative(path)
        if subprocess.run(["git", "cat-file", "-e", f"HEAD:{relative}"], cwd=ROOT).returncode:
            raise OrchestrationError(f"freeze requires committed file: {relative}")
        if subprocess.run(["git", "diff", "--quiet", "HEAD", "--", relative], cwd=ROOT).returncode:
            raise OrchestrationError(f"freeze refuses uncommitted file: {relative}")


def _remote(code: str, *, timeout: int = 120) -> dict[str, Any]:
    """Run remote Python with loss-tolerant text decoding, unlike legacy helper."""

    preamble = "import hashlib,json,os,subprocess,csv\nfrom pathlib import Path\ndef sha(p):\n h=hashlib.sha256()\n with Path(p).open('rb') as f:\n  for b in iter(lambda:f.read(1048576),b''):h.update(b)\n return h.hexdigest()\ndef emit(x):print(json.dumps(x))\n"
    encoded = base64.b64encode((preamble + code).encode("utf-8")).decode("ascii")
    command = f"{REMOTE_PYTHON} -c \"import base64;exec(base64.b64decode('{encoded}'))\""
    result = subprocess.run(SSH + [command], capture_output=True, timeout=timeout)
    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")
    if result.returncode:
        raise OrchestrationError(f"remote failed ({result.returncode}): {stderr[-1000:]}")
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise OrchestrationError(f"remote emitted invalid JSON: {stdout[-1000:]}") from exc


def _audit_and_review(plan: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    audit = read_json(DATA / "audit.json")
    review = read_json(EXP / "visual_review" / "review.json")
    selection = read_json(EXP / "visual_review" / "selection.json")
    if audit.get("status") != "passed" or audit.get("pilot") is not False or audit.get("per_sample_owner_gold") is not False:
        raise OrchestrationError("completed non-pilot morphology audit required")
    manifest = DATA / "manifest.jsonl"
    if (
        audit.get("manifest_sha256") != sha(manifest)
        or review.get("manifest_sha256") != sha(manifest)
        or selection.get("manifest_sha256") != sha(manifest)
    ):
        raise OrchestrationError("audit/review are not bound to current manifest")
    if review.get("status") != "passed_rendering_spot_check":
        raise OrchestrationError("passed rendering spot-check required")
    selection = EXP / "visual_review" / "selection.json"
    if review.get("selection_sha256") != sha(selection):
        raise OrchestrationError("visual review selection changed")
    for page in read_json(selection)["pages"]:
        if sha(selection.parent / page["path"]) != page["sha256"]:
            raise OrchestrationError("review sheet changed")
    if plan.get("training_eligible") is not False or plan.get("production_eligible") is not False:
        raise OrchestrationError("plan offline flags drift")
    return audit, review


def _frozen_paths(plan: Mapping[str, Any]) -> set[Path]:
    paths = {ROOT / relative for relative in NEW_CODE}
    probe = read_json(ROOT / "experiments/active/exp-ma-profit3r-negatives-20260922-v2/code_probe_files.json")
    for relative, digest in probe.items():
        path = ROOT / relative
        if sha(path) != digest:
            raise OrchestrationError(f"unchanged code-probe dependency drift: {relative}")
        paths.add(path)
    for value in plan.get("inputs", {}).values():
        if not isinstance(value, Mapping):
            raise OrchestrationError("plan input entry malformed")
        path = ROOT / str(value["path"])
        if sha(path) != value.get("sha256"):
            raise OrchestrationError(f"plan input SHA drift: {path}")
        paths.add(path)
    paths.update(CONTROL_PATHS)
    paths.add(BASELINE)
    paths.update({
        EXP / "plan.json", DATA / "audit.json", EXP / "visual_review" / "review.json",
        EXP / "visual_review" / "selection.json",
    })
    for path in DATA.iterdir():
        if path.is_file() and path.suffix in {".json", ".jsonl", ".txt", ".yaml"}:
            paths.add(path)
    if not paths or any(not path.is_file() for path in paths):
        raise OrchestrationError("missing frozen code/control/dataset metadata")
    return paths


def _launch_batch_bytes() -> bytes:
    """Return the fixed WMI child command; cmd owns redirection and exit evidence."""

    exp = REMOTE_EXP.replace("/", "\\")
    data = REMOTE_DATA.replace("/", "\\")
    run = RUN.replace("/", "\\")
    lines = (
        "@echo off",
        "setlocal",
        "set \"PYTHONPATH=C:\\fable\"",
        "cd /d C:\\fable",
        f">> \"{exp}\\complete.log\" echo [launcher] started %DATE% %TIME%",
        f"C:\\fable\\.venv\\Scripts\\python.exe -u -m scripts.windows.complete_ma_morphology_redo --experiment \"{exp}\" --dataset \"{data}\" --run-root \"{run}\" --launch-contract \"{exp}\\launch_contract.json\" >> \"{exp}\\complete.log\" 2>&1",
        "set FABLE_TRAIN_RC=%ERRORLEVEL%",
        f">> \"{exp}\\complete.log\" echo [launcher] exit_code=%FABLE_TRAIN_RC% %DATE% %TIME%",
        f"> \"{exp}\\wmi_exit_code.txt\" echo %FABLE_TRAIN_RC%",
        "exit /b %FABLE_TRAIN_RC%",
        "",
    )
    return "\r\n".join(lines).encode("ascii")


def freeze() -> None:
    launch = EXP / "launch_contract.json"
    if launch.exists():
        raise FileExistsError(launch)
    plan = read_json(EXP / "plan.json")
    audit, review = _audit_and_review(plan)
    paths = _frozen_paths(plan)
    _require_committed([ROOT / item for item in NEW_CODE])
    batch = EXP / "launch_wmi.cmd"
    if batch.exists():
        raise FileExistsError(batch)
    with batch.open("xb") as handle:
        handle.write(_launch_batch_bytes())
    paths.add(batch)
    files = {_relative(path): sha(path) for path in sorted(paths)}
    if not files:
        raise OrchestrationError("empty frozen file map")
    write_json(launch, {
        "status": "frozen_after_completed_morphology_audit", "experiment_id": plan["experiment_id"],
        "plan_sha256": sha(EXP / "plan.json"), "base_model_sha256": plan["inputs"]["base_model"]["sha256"],
        "dataset_audit": audit, "review_path": _relative(EXP / "visual_review" / "review.json"),
        "review_sha256": sha(EXP / "visual_review" / "review.json"), "review_manifest_sha256": audit["manifest_sha256"],
        "files": files, "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
    })
    _state("frozen", files=len(files), review_sha256=sha(EXP / "visual_review" / "review.json"))


def stage() -> None:
    launch = read_json(EXP / "launch_contract.json")
    files = _stage_paths(launch)
    if not files:
        raise OrchestrationError("launch contract files must be non-empty")
    for root in (DATA / "images", DATA / "labels"):
        files.update(path for path in root.rglob("*") if path.is_file())
    manifest = {_relative(path): sha(path) for path in sorted(files)}
    write_json(EXP / "upload_manifest.json", manifest)
    files.add(EXP / "upload_manifest.json")
    bundle = EXP / "upload.tar"
    with tarfile.open(bundle, "w") as archive:
        for path in sorted(files):
            archive.add(path, arcname=_relative(path), recursive=False)
    _state("uploading", files=len(files), bytes=bundle.stat().st_size)
    _remote(f"Path({REMOTE_EXP!r}).mkdir(parents=True,exist_ok=True);emit({{'ready':True}})")
    subprocess.run(["scp", "-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", str(bundle), f"{HOST}:{REMOTE_EXP}/upload.tar"], check=True, timeout=1800)
    result = _remote(f'''import tarfile
root=Path({REMOTE_ROOT!r}); bundle=Path({REMOTE_EXP!r})/'upload.tar'
if sha(bundle)!={sha(bundle)!r}: raise RuntimeError('bundle SHA drift')
with tarfile.open(bundle) as archive:
 members=archive.getmembers()
 if any(not m.isfile() or Path(m.name).is_absolute() or '..' in Path(m.name).parts for m in members): raise RuntimeError('unsafe archive')
 manifest=json.load(archive.extractfile({(EXP.relative_to(ROOT)/'upload_manifest.json').as_posix()!r}))
 for name,digest in manifest.items():
  target=root/name
  if target.exists() and sha(target)!=digest: raise RuntimeError('remote SHA collision: '+name)
 for member in members:
  target=root/member.name
  if not target.exists():
   target.parent.mkdir(parents=True,exist_ok=True)
   target.write_bytes(archive.extractfile(member).read())
 for name,digest in manifest.items():
  if sha(root/name)!=digest: raise RuntimeError('staged SHA mismatch: '+name)
 emit({{'status':'verified','files':len(manifest),'bundle_sha256':sha(bundle)}})
''', timeout=1800)
    write_json(EXP / "transfer_receipt.json", result)
    _state("staged", transfer=result)


def _stage_paths(launch: Mapping[str, Any]) -> set[Path]:
    """Return every immutable source uploaded before a remote preflight."""

    files = {ROOT / relative for relative in launch.get("files", {})}
    files.add(EXP / "launch_contract.json")
    return files


def _remote_json_subprocess_code(command: list[str], *, cwd: str | None = None) -> str:
    """Generate remote Python that always emits JSON when the child succeeds."""

    lines = []
    if cwd is not None:
        lines.append(f"os.chdir({cwd!r})")
    lines.extend((
        f"p=subprocess.run({command!r},capture_output=True)",
        "if p.returncode:",
        "    raise RuntimeError(p.stderr.decode('utf-8', errors='replace'))",
        "emit(json.loads(p.stdout.decode('utf-8', errors='replace')))",
    ))
    return "\n".join(lines)


def _powershell_literal(value: str) -> str:
    """Quote one literal for PowerShell single-quoted syntax."""

    return "'" + value.replace("'", "''") + "'"


def _powershell_wmi(command_line: str) -> str:
    """Encode the verified WMI launch without cmd/Python re-quoting layers."""

    cwd_literal = _powershell_literal("C:\\fable")
    script = (
        "$ErrorActionPreference='Stop'\n"
        "$r=Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{"
        f"CommandLine={_powershell_literal(command_line)};CurrentDirectory={cwd_literal}}}\n"
        "if ($r.ReturnValue -ne 0) { throw ('WMI Create failed '+$r.ReturnValue) }\n"
        "@{pid=$r.ProcessId;return_value=$r.ReturnValue;command_line="
        f"{_powershell_literal(command_line)}" + "}|ConvertTo-Json -Compress"
    )
    return base64.b64encode(script.encode("utf-16le")).decode("ascii")


def start() -> None:
    attempt = EXP / "remote_start_attempt.json"
    if attempt.exists():
        raise FileExistsError("start submission was already attempted; never retry an uncertain WMI launch")
    plan = read_json(EXP / "plan.json")
    preflight_command = [REMOTE_PYTHON, "-u", "-m", "scripts.windows.run_ma_morphology_redo", "--plan", f"{REMOTE_EXP}/plan.json", "--dataset", REMOTE_DATA, "--run-root", RUN, "--launch-contract", f"{REMOTE_EXP}/launch_contract.json"]
    preflight = _remote(_remote_json_subprocess_code(preflight_command, cwd=REMOTE_ROOT), timeout=1800)
    environment = _remote(f"os.chdir({REMOTE_ROOT!r});from scripts.windows.train_ma_profit3r import verify_environment;emit(verify_environment())")
    write_json(EXP / "remote_preflight.json", {"runner": preflight, "environment": environment})
    launch = read_json(EXP / "launch_contract.json")
    batch_relative = _relative(EXP / "launch_wmi.cmd")
    batch_sha = launch.get("files", {}).get(batch_relative)
    if not isinstance(batch_sha, str) or sha(EXP / "launch_wmi.cmd") != batch_sha:
        raise OrchestrationError("frozen WMI batch is absent or has drifted")
    _remote(
        f"p=Path({REMOTE_EXP!r})/'launch_wmi.cmd';"
        f"\nif not p.is_file() or sha(p)!={batch_sha!r}: raise RuntimeError('remote WMI batch SHA drift')"
        f"\nemit({{'path':str(p),'sha256':sha(p)}})"
    )
    remote_lock = _remote(
        f"p=Path({REMOTE_EXP!r})/'launch_lock.json';"
        "f=p.open('x',encoding='utf-8');json.dump({'requested':True},f);f.close();"
        "emit({'lock':str(p)})"
    )
    batch_remote = f"{REMOTE_EXP}/launch_wmi.cmd".replace("/", "\\")
    actual_command = f'cmd.exe /d /c "{batch_remote}"'
    encoded = _powershell_wmi(actual_command)
    write_json(attempt, {
        "status": "wmi_submission_attempted", "encoded_command_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
        "actual_command": actual_command, "launch_contract_sha256": sha(EXP / "launch_contract.json"),
        "launch_wmi_cmd_sha256": batch_sha, "preflight": preflight, "remote_lock": remote_lock,
    })
    result = _remote(_remote_json_subprocess_code(
        ["powershell.exe", "-NoProfile", "-EncodedCommand", encoded], cwd=REMOTE_ROOT,
    ))
    write_json(EXP / "remote_launch.json", result)
    _state("started", **result)


def snapshot() -> dict[str, Any]:
    return _remote(f'''out={{}}
for path,name in ((Path({REMOTE_EXP!r})/'job_receipt.json','job'),(Path({RUN!r})/'training_receipt.json','training')):
 if path.exists(): out[name]=json.loads(path.read_text(encoding='utf-8',errors='replace'))
progress={{}}
for arm in ('A','B'):
 path=Path({RUN!r})/('arm_'+arm)/'results.csv'
 rows=list(csv.DictReader(path.open(encoding='utf-8',errors='replace'))) if path.exists() else []
 progress[arm]={{'epoch':int(float(rows[-1]['epoch'])) if rows else 0,'mtime_ns':path.stat().st_mtime_ns if path.exists() else None}}
out['progress']=progress;emit(out)
''')


def _copy_remote(source: str, target: Path, digest: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if sha(target) != digest: raise OrchestrationError(f"local SHA collision: {target}")
        return
    partial = target.with_suffix(target.suffix + ".downloading")
    subprocess.run(["scp", "-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", f"{HOST}:{source}", str(partial)], check=True, timeout=600)
    if sha(partial) != digest: raise OrchestrationError(f"download SHA mismatch: {source}")
    partial.replace(target)


def _require_collected_sha(path: Path, expected: object, label: str) -> None:
    if not isinstance(expected, str) or sha(path) != expected:
        raise OrchestrationError(f"{label} SHA drift: {path}")


def _validate_collected_receipts(launch: Mapping[str, Any], job: Mapping[str, Any]) -> None:
    """Bind each downloaded model and evaluation artifact to the frozen receipts."""

    launch_sha = sha(EXP / "launch_contract.json")
    if job.get("status") != "completed" or job.get("plan_sha256") != launch.get("plan_sha256") or job.get("launch_sha256") != launch_sha:
        raise OrchestrationError("job plan/launch binding drift")
    training = read_json(EXP / "collected/training_receipt.json")
    preflight = training.get("preflight", {})
    if (
        training.get("status") != "completed"
        or preflight.get("plan_sha256") != launch.get("plan_sha256")
        or preflight.get("base_model_sha256") != launch.get("base_model_sha256")
    ):
        raise OrchestrationError("training plan/base-model binding drift")
    plan = read_json(EXP / "plan.json")
    expected_bank = plan.get("inputs", {}).get("old_manifest", {}).get("sha256")
    expected_code = launch.get("files", {}).get("yoyo/evaluation/ma_morphology_economics.py")
    for arm in ("A", "B"):
        item = training.get("arms", {}).get(arm, {})
        if item.get("status") != "trained":
            raise OrchestrationError(f"training arm incomplete: {arm}")
        arm_dir = EXP / f"collected/training/arm_{arm}"
        _require_collected_sha(arm_dir / "weights/best.pt", item.get("best_sha256"), f"best {arm}")
        _require_collected_sha(arm_dir / "weights/last.pt", item.get("last_sha256"), f"last {arm}")
        _require_collected_sha(arm_dir / "args.yaml", item.get("args_yaml_sha256"), f"args.yaml {arm}")
        _require_collected_sha(arm_dir / "results.csv", item.get("results_csv", {}).get("sha256"), f"results.csv {arm}")
        _require_collected_sha(
            EXP / f"collected/common_eval/arm_{arm}.jsonl",
            item.get("common_image_evaluation", {}).get("prediction_sha256"),
            f"common evaluation {arm}",
        )
        econ = read_json(EXP / f"collected/economics/arm_{arm}/receipt.json")
        if (
            econ.get("status") != "completed" or econ.get("arm") != arm
            or econ.get("model_sha256") != item.get("best_sha256")
            or econ.get("plan_sha256") != launch.get("plan_sha256")
            or econ.get("launch_sha256") != launch_sha
            or econ.get("code_sha256") != expected_code
            or econ.get("bank_manifest_sha256") != expected_bank
        ):
            raise OrchestrationError(f"economics binding drift: {arm}")
        for name, digest in econ.get("artifacts", {}).items():
            if Path(name).name != name:
                raise OrchestrationError("unsafe economics artifact")
            _require_collected_sha(EXP / f"collected/economics/arm_{arm}/{name}", digest, f"economics {arm}/{name}")


def collect() -> None:
    state = snapshot(); job = state.get("job", {})
    if job.get("status") != "completed": raise OrchestrationError("remote job is not completed")
    launch = read_json(EXP / "launch_contract.json")
    if job.get("plan_sha256") != launch.get("plan_sha256") or job.get("launch_sha256") != sha(EXP / "launch_contract.json"):
        raise OrchestrationError("remote job is not bound to the frozen plan/launch contract")
    remote_files = {"job_receipt.json": f"{REMOTE_EXP}/job_receipt.json", "complete.log": f"{REMOTE_EXP}/complete.log", "training_receipt.json": f"{RUN}/training_receipt.json"}
    for arm in ("A", "B"):
        for name in ("weights/best.pt", "weights/last.pt", "args.yaml", "results.csv"):
            remote_files[f"training/arm_{arm}/{name}"] = f"{RUN}/arm_{arm}/{name}"
        remote_files[f"common_eval/arm_{arm}.jsonl"] = f"{RUN}/arm_{arm}_common_eval_predictions.jsonl"
        remote_files[f"economics/arm_{arm}/receipt.json"] = f"{REMOTE_EXP}/economics/arm_{arm}/receipt.json"
    records = _remote(f"files={remote_files!r};emit({{k:{{'path':v,'sha256':sha(v),'bytes':Path(v).stat().st_size}} for k,v in files.items()}})")
    for arm in ("A", "B"):
        receipt = _remote(f"p=Path({REMOTE_EXP!r})/'economics'/'arm_{arm}'/'receipt.json';x=json.loads(p.read_text(encoding='utf-8',errors='replace'));emit(x)")
        if receipt.get("status") != "completed": raise OrchestrationError(f"economics {arm} incomplete")
        for name, digest in receipt.get("artifacts", {}).items():
            if Path(name).name != name: raise OrchestrationError("unsafe economics artifact")
            key = f"economics/arm_{arm}/{name}"; records[key] = {"path": f"{REMOTE_EXP}/economics/arm_{arm}/{name}", "sha256": digest}
    for name, row in records.items(): _copy_remote(row["path"], EXP / "collected" / name, row["sha256"])
    if any(sha(ROOT / path) != digest for path, digest in launch["files"].items()): raise OrchestrationError("local launch binding drift")
    local_job = read_json(EXP / "collected/job_receipt.json")
    _validate_collected_receipts(launch, local_job)
    deliver(EXP)
    write_json(EXP / "download_inventory.json", records); _state("collected", files=len(records))


def finish() -> None:
    """Bounded plain-Python completion transfer; never wakes an LLM or retrains.

    This is one job's completion continuation, not a repeating Codex automation.
    A disconnected SSH session can retry reads only; WMI submission is never
    repeated. Stop immediately on a failed remote receipt, or after 12 hours.
    """
    deadline=time.monotonic()+12*3600
    while time.monotonic()<deadline:
        try:
            current=snapshot()
        except (OrchestrationError,subprocess.TimeoutExpired) as exc:
            write_json(EXP/'completion_transfer_status.json',{'status':'waiting_for_connection','error':repr(exc),'updated_unix':time.time()})
            time.sleep(180)
            continue
        job=current.get('job',{});training=current.get('training',{})
        if job.get('status')=='failed' or training.get('status')=='failed':
            write_json(EXP/'completion_transfer_status.json',{'status':'remote_failed','job':job,'updated_unix':time.time()})
            raise OrchestrationError('Remote job failed; retained outputs, no automatic retry')
        if job.get('status')=='completed':
            collect()
            write_json(EXP/'completion_transfer_status.json',{'status':'completed','updated_unix':time.time()})
            return
        write_json(EXP/'completion_transfer_status.json',{'status':'waiting_for_job','progress':current.get('progress'),'updated_unix':time.time()})
        time.sleep(180)
    raise OrchestrationError('Completion transfer reached 12h limit; remote job was not killed or restarted')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("freeze", "stage", "start", "snapshot", "collect", "finish"))
    args = parser.parse_args()
    if args.mode == "snapshot": print(json.dumps(snapshot(), indent=2, sort_keys=True))
    else: globals()[args.mode]()


if __name__ == "__main__": main()

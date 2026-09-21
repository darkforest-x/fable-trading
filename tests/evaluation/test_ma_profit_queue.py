import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

import yoyo.datasets.ma_profit_queue as q


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _configure(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    exp = root / "experiments" / "active" / "exp-ma-profit3r-20260922-v1"
    scan = exp / "source_scans"
    plan = exp / "plan.json"
    plan.parent.mkdir(parents=True)
    plan.write_text('{"experiment_id":"x"}\n')
    (exp / "training_contract.json").write_text('{"minimum_train_winners":3000}\n')
    (exp / "reference_exclusion.json").write_text('[]\n')
    for name in ("ma_profit_pipeline.py", "ma_profit_cohort.py", "ma_profit_incremental_labels.py",
                 "fifteen_minute_launch_candidates.py"):
        path = root / "yoyo" / "datasets" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)
    resolver = root / "yoyo" / "contracts" / "ma_profit_filter.py"
    resolver.parent.mkdir(parents=True, exist_ok=True)
    resolver.write_text("resolver")
    rules = []
    for index, name in enumerate(("ma_profit_miner.py", "snapshot.py", "perfect.py", "autofill.py", "review.py", "candidates.py")):
        path = root / "yoyo" / "datasets" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(index))
        rules.append(path)
    monkeypatch.setattr(q, "ROOT", root)
    monkeypatch.setattr(q, "EXP", exp)
    monkeypatch.setattr(q, "PLAN", plan)
    monkeypatch.setattr(q, "SCAN", scan)
    monkeypatch.setattr(q, "RULES", rules)
    monkeypatch.setattr(q, "BASE_MANIFESTS", ())
    return root, exp, scan, plan, rules


def _manifest(exp, name="sources.json", paths=("input/a.csv", "input/b.csv")):
    path = exp / name
    path.write_text(json.dumps({"sources": [{"source_path": source, "sha256": f"sha-{i}"} for i, source in enumerate(paths)]}))
    return path


def _master(scan, manifest, plan, rules, *, root, paths=("input/a.csv", "input/b.csv"), failed=0):
    scan.mkdir(parents=True, exist_ok=True)
    path = scan / f"master_{manifest.stem}.json"
    path.write_text(json.dumps({
        "plan_sha256": _digest(plan), "sources_manifest_sha256": _digest(manifest),
        "miner_sha256": _digest(rules[0]),
        "rule_dependency_sha256": {str(rule.relative_to(root)): _digest(rule) for rule in rules[1:]},
        "source_coverage_complete": not failed, "failed_sources": failed, "errors": [] if not failed else [{"x": "bad"}],
        "completed_sources": len(paths) - failed, "attempted_sources": len(paths),
        "source_summaries": [{"source_path": source} for source in paths],
    }))
    return path


def _git_output(args, **_kwargs):
    if args[:3] == ["git", "branch", "--show-current"]:
        return "main\n"
    if args[:3] == ["git", "status", "--porcelain"]:
        return ""
    if args[:3] == ["git", "ls-files", "--error-unmatch"]:
        return args[-1] + "\n"
    if args[:4] == ["git", "log", "-1", "--format=%H"]:
        return "frozencommit\n"
    if args[:3] == ["git", "diff", "--cached"]:
        return ""
    if args[0] == "pgrep":
        raise subprocess.CalledProcessError(1, args)
    if args[:3] == ["git", "rev-parse", "HEAD"]:
        return "newcommit\n"
    raise AssertionError(args)


def _wait_now(predicate, description, **_kwargs):
    if not predicate():
        raise AssertionError(f"unexpected wait: {description}")



def _stage_paths(cohort):
    return (cohort / "frozen_events.jsonl", cohort / "frozen_sources.json", cohort / "collection_exclusions.jsonl", cohort / "collection_receipt.json")


def _event(index, *, retained=False):
    return {"event_id": f"event-{index}", "cluster_id": f"cluster-{index}", "symbol": "X",
            "canonical_asset": "X", "direction": "LONG", "bar_minutes": 1,
            "source_path": f"input/{index}.csv", "source_sha256": f"sha-{index}",
            "core_start_time": f"2025-01-01T00:{index % 60:02d}:00+00:00",
            "core_end_time": f"2025-01-01T01:{index % 60:02d}:00+00:00", "core_bars": 4,
            "split": "train" if retained else "val",
            "profit": {"retained": retained, "outcome": "TP" if retained else "SL",
                       "gross_r": 3.0 if retained else -1.0, "net_r": 2.99 if retained else -1.01}}


def _write_collection(root, exp, plan, manifests, cohort, *, count=1):
    cohort.mkdir(parents=True, exist_ok=True)
    events, sources, exclusions, receipt_path = _stage_paths(cohort)
    frozen = [{key: value for key, value in _event(index).items() if key not in {"split", "profit"}} for index in range(count)]
    events.write_text("".join(json.dumps(row) + "\n" for row in frozen))
    sources.write_text('{"sources":[]}\n')
    exclusions.write_text('')
    receipt_path.write_text(json.dumps({
        "plan_sha256": _digest(plan),
        "inputs": [{"path": str(plan.relative_to(root)), "sha256": _digest(plan)}] + [
            {"path": str(path.relative_to(root)), "sha256": _digest(path)} for path in manifests],
        "frozen_events_path": str(events.relative_to(root)), "frozen_events_sha256": _digest(events),
        "frozen_sources_path": str(sources.relative_to(root)), "frozen_sources_sha256": _digest(sources),
        "collection_exclusions_path": str(exclusions.relative_to(root)), "collection_exclusions_sha256": _digest(exclusions),
    }) + "\n")


def _write_label(exp, plan, cohort, outcomes, *, capacity, reuse=None):
    outcomes.mkdir(parents=True, exist_ok=True)
    events, sources = cohort / "frozen_events.jsonl", cohort / "frozen_sources.json"
    rows, errors, summary = outcomes / "outcomes.jsonl", outcomes / "lineage_errors.jsonl", outcomes / "summary.json"
    frozen = [json.loads(line) for line in events.read_text().splitlines() if line.strip()]
    labelled = [{**row, "split": "train" if capacity else "val",
                 "profit": _event(index, retained=capacity)["profit"]} for index, row in enumerate(frozen)]
    rows.write_text("".join(json.dumps(row) + "\n" for row in labelled))
    errors.write_text('')
    payload = {"plan_sha256": _digest(plan), "input_events_sha256": _digest(events),
               "source_manifest_sha256": _digest(sources), "outcomes_sha256": _digest(rows),
               "lineage_errors": 0}
    if reuse is not None:
        (outcomes / "reuse_receipt.json").write_text(json.dumps(reuse) + "\n")
        payload["reuse"] = {**reuse, "hits": 1, "misses": 0}
    summary.write_text(json.dumps(payload) + "\n")


def _write_selection(root, exp, outcomes, selection, *, capacity, forged=False):
    selection.mkdir(parents=True, exist_ok=True)
    ledger, dataset, receipt_path = selection / "selection_ledger.jsonl", selection / "dataset_ledger.jsonl", selection / "selection_receipt.json"
    outcome_rows = [json.loads(line) for line in (outcomes / "outcomes.jsonl").read_text().splitlines() if line.strip()]
    rows = [_event(index, retained=True) for index in range(3000)] if forged else [
        {**row, "dataset_kept": capacity, "dataset_reason": "selected_train_winner" if capacity else "capacity_gate_closed",
         "reference69_excluded": False} for row in outcome_rows]
    if forged:
        rows = [{**row, "dataset_kept": True, "dataset_reason": "selected_train_winner", "reference69_excluded": False} for row in rows]
    kept = [row for row in rows if row["dataset_kept"]]
    ledger.write_text("".join(json.dumps(row) + "\n" for row in rows))
    dataset.write_text("".join(json.dumps(row) + "\n" for row in kept))
    actual = sum(row["split"] == "train" and row["profit"]["retained"] for row in rows)
    receipt_path.write_text(json.dumps({
        "selection_ledger_sha256": _digest(ledger), "dataset_ledger_sha256": _digest(dataset),
        "selected_events_sha256": _digest(dataset), "training_contract_sha256": _digest(exp / "training_contract.json"),
        "minimum_train_winners": 3000, "maximum_train_winners": 5000, "capacity_gate": actual >= 3000,
        "train_retained_winners_actual": actual, "train_retained_winners_selected": len(kept),
        "inputs": [{"path": str((outcomes / "outcomes.jsonl").relative_to(root)), "sha256": _digest(outcomes / "outcomes.jsonl")}],
    }) + "\n")


def _valid_stage_runner(root, exp, plan, manifests, *, capacity):
    def runner(args, **_kwargs):
        module = args[args.index("-m") + 1]
        output = Path(args[args.index("--out") + 1])
        if module == "yoyo.datasets.ma_profit_cohort" and "collect" in args:
            _write_collection(root, exp, plan, manifests, output, count=3000 if capacity else 1)
        elif module in {"yoyo.datasets.ma_profit_pipeline", "yoyo.datasets.ma_profit_incremental_labels"}:
            cohort = Path(args[args.index("--events") + 1]).parent
            reuse = None
            if module == "yoyo.datasets.ma_profit_incremental_labels":
                reuse = {"status": "accepted", "cache_dir": args[args.index("--reuse-label-dir") + 1]}
            _write_label(exp, plan, cohort, output, capacity=capacity, reuse=reuse)
        elif module == "yoyo.datasets.ma_profit_cohort" and "select" in args:
            _write_selection(root, exp, Path(args[args.index("--events") + 1]).parent, output, capacity=capacity)
        else:
            raise AssertionError(args)
    return runner

def test_validate_master_rejects_failed_and_exact_source_set(tmp_path, monkeypatch):
    _root, exp, scan, plan, rules = _configure(monkeypatch, tmp_path)
    manifest = _manifest(exp)
    master = _master(scan, manifest, plan, rules, root=_root)
    assert q.validate_master(master, manifest, check_receipts=False)["attempted_sources"] == 2
    data = json.loads(master.read_text())
    data["source_summaries"] = [{"source_path": "input/a.csv"}, {"source_path": "wrong.csv"}]
    master.write_text(json.dumps(data))
    with pytest.raises(q.QueueError, match="source-set"):
        q.validate_master(master, manifest, check_receipts=False)
    _master(scan, manifest, plan, rules, root=_root, failed=1)
    with pytest.raises(q.QueueError, match="incomplete"):
        q.validate_master(master, manifest, check_receipts=False)


def test_uncommitted_manifest_is_rejected(tmp_path, monkeypatch):
    _root, exp, _scan, _plan, _rules = _configure(monkeypatch, tmp_path)
    manifest = _manifest(exp)

    def dirty(args, **_kwargs):
        if args[:3] == ["git", "status", "--porcelain"]:
            return " M " + args[-1]
        raise AssertionError(args)

    with pytest.raises(q.QueueError, match="not frozen"):
        q.ensure_committed(manifest, output=dirty)


def test_run_miner_uses_active_interpreter_and_rejects_parallel_pid(tmp_path, monkeypatch):
    _root, exp, _scan, _plan, _rules = _configure(monkeypatch, tmp_path)
    manifest = _manifest(exp)
    calls = []

    def runner(args, **kwargs):
        calls.append((args, kwargs))

    q.run_miner(manifest, runner=runner, output=_git_output)
    assert calls[0][0][0] == sys.executable

    def active(args, **_kwargs):
        if args[0] == "pgrep":
            return "222\n"
        return _git_output(args)

    with pytest.raises(q.QueueError, match="another miner"):
        q.run_miner(manifest, runner=runner, output=active)


def test_external_batch01_is_observed_without_a_second_miner(tmp_path, monkeypatch):
    _root, exp, scan, plan, rules = _configure(monkeypatch, tmp_path)
    manifest = _manifest(exp, "sources_archive_1m_batch01.json")
    master = _master(scan, manifest, plan, rules, root=_root)
    monkeypatch.setattr(q, "queue_items", lambda: [])
    monkeypatch.setattr(q, "validate_master", lambda master_path, manifest_path: json.loads(master_path.read_text()))
    runs, commits = [], []
    state = q.run_queue(once=False, runner=lambda *a, **k: runs.append(a), output=_git_output,
                        waiter=_wait_now, commit_fn=lambda paths: commits.append(list(paths)) or "commit")
    assert not runs
    assert state["batches"]["1m_batch01"]["master_sha256"] == _digest(master)
    assert commits == [[exp / "queue_summary_1m_batch01.json"]]


def test_resume_revalidates_saved_master_sha(tmp_path, monkeypatch):
    root, exp, scan, plan, rules = _configure(monkeypatch, tmp_path)
    manifest = _manifest(exp, "sources_archive_1m_batch01.json")
    master = _master(scan, manifest, plan, rules, root=root)
    monkeypatch.setattr(q, "queue_items", lambda: [])
    (exp / "queue_summary_1m_batch01.json").write_text("{}\n")
    state = {"schema_version": 2, "binding": q.binding(), "batches": {
        "1m_batch01": {"manifest_path": manifest.name, "manifest_sha256": _digest(manifest),
                        "master_path": str(master.relative_to(root)), "master_sha256": "wrong",
                        "summary_path": "queue_summary_1m_batch01.json", "summary_sha256": "x", "summary_commit": "c"}},
             "rounds": [], "status": "idle"}
    q.save_state(state)
    with pytest.raises(q.QueueError, match="saved batch master drift"):
        q.run_queue(once=True, output=_git_output, waiter=_wait_now, commit_fn=lambda paths: "unused")


def test_commit_exact_stages_only_requested_paths(tmp_path, monkeypatch):
    root, exp, _scan, _plan, _rules = _configure(monkeypatch, tmp_path)
    one, two = exp / "one.json", exp / "two.json"
    one.write_text("1")
    two.write_text("2")
    calls = []
    staged = set()

    def runner(args, **kwargs):
        calls.append(args)
        if args[:2] == ["git", "add"]:
            staged.update(args[3:])

    def output(args, **_kwargs):
        if args[:3] == ["git", "branch", "--show-current"]:
            return "main\n"
        if args[:3] == ["git", "diff", "--cached"]:
            return "\n".join(sorted(staged))
        if args[:3] == ["git", "rev-parse", "HEAD"]:
            return "commit123\n"
        raise AssertionError(args)

    assert q.commit_exact([one, two], runner=runner, output=output) == "commit123"
    assert calls[0] == ["git", "add", "--", str(one.relative_to(root)), str(two.relative_to(root))]
    assert calls[1][:2] == ["git", "commit"]


def test_downstream_commits_each_stage_and_capacity_stops(tmp_path, monkeypatch):
    root, exp, _scan, plan, _rules = _configure(monkeypatch, tmp_path)
    manifests = [_manifest(exp, "sources_archive_1m_batch01.json")]
    command_calls, commit_calls = [], []
    base_runner = _valid_stage_runner(root, exp, plan, manifests, capacity=True)

    def runner(args, **kwargs):
        command_calls.append(args)
        base_runner(args, **kwargs)

    def commit(paths):
        commit_calls.append([path.name for path in paths])
        return f"c{len(commit_calls)}"

    result = q.run_downstream(manifests, "queue_round_001", runner=runner, commit_fn=commit, input_validator=lambda paths: None)
    assert result["capacity_gate"] is True
    assert commit_calls == [
        ["frozen_events.jsonl", "frozen_sources.json", "collection_exclusions.jsonl", "collection_receipt.json"],
        ["outcomes.jsonl", "lineage_errors.jsonl", "summary.json"],
        ["selection_ledger.jsonl", "dataset_ledger.jsonl", "selection_receipt.json"],
        ["queue_ready.json"],
    ]
    assert "--compact-events" in command_calls[0]
    assert [call[call.index("-m") + 1] for call in command_calls] == [
        "yoyo.datasets.ma_profit_cohort", "yoyo.datasets.ma_profit_pipeline", "yoyo.datasets.ma_profit_cohort",
    ]
    assert all(call[0] == sys.executable for call in command_calls)
    assert (exp / "queue_ready.json").exists()


def test_downstream_rejects_receipt_sha_drift(tmp_path, monkeypatch):
    root, exp, _scan, plan, _rules = _configure(monkeypatch, tmp_path)
    manifests = [_manifest(exp, "sources_archive_1m_batch01.json")]
    cohort = exp / "cohort_queue_round_001"
    _write_collection(root, exp, plan, manifests, cohort)
    receipt = cohort / "collection_receipt.json"
    payload = json.loads(receipt.read_text())
    payload["frozen_events_sha256"] = "0" * 64
    receipt.write_text(json.dumps(payload))
    with pytest.raises(q.QueueError, match="collection receipt artifact"):
        q.run_downstream(manifests, "queue_round_001", output=_git_output,
                         input_validator=lambda paths: None, runner=lambda *args, **kwargs: None,
                         commit_fn=lambda paths: "unused")

def test_3m_receipt_requires_complete_coverage(tmp_path, monkeypatch):
    _root, exp, _scan, _plan, _rules = _configure(monkeypatch, tmp_path)
    manifest = _manifest(exp, "sources_archive_3m.json")
    receipt = exp / "archive3m_receipt.json"
    receipt.write_text(json.dumps({"sources_manifest_sha256": _digest(manifest), "source_coverage_complete": False, "failed_sources": 1, "errors": ["x"]}))
    with pytest.raises(q.QueueError, match="incomplete"):
        q.archive_3m_receipt_valid(manifest, receipt)


def test_run_queue_stops_after_capacity_decision(tmp_path, monkeypatch):
    _root, exp, scan, plan, rules = _configure(monkeypatch, tmp_path)
    first = _manifest(exp, "sources_archive_1m_batch01.json")
    three = _manifest(exp, "sources_archive_3m.json")
    _master(scan, first, plan, rules, root=_root)
    _master(scan, three, plan, rules, root=_root)
    receipt = exp / "archive3m_receipt.json"
    receipt.write_text("{}")
    monkeypatch.setattr(q, "queue_items", lambda: [("3m", three, True)])
    monkeypatch.setattr(q, "ensure_committed", lambda *args, **kwargs: "frozen")
    monkeypatch.setattr(q, "archive_3m_receipt_valid", lambda *args: None)
    monkeypatch.setattr(q, "validate_master", lambda master_path, manifest_path: json.loads(master_path.read_text()))
    downstream_inputs = []

    def downstream(manifests, tag, **_kwargs):
        downstream_inputs.append(([path.name for path in manifests], tag))
        return {"capacity_gate": True, "selection_receipt_sha256": "ready"}

    monkeypatch.setattr(q, "run_downstream", downstream)
    with pytest.raises(q.CapacityReached):
        q.run_queue(output=_git_output, waiter=_wait_now, commit_fn=lambda paths: "commit")
    state = q.load_state()
    assert state["status"] == "capacity_reached"
    assert downstream_inputs == [([three.name, first.name], "queue_round_001")]


def test_existing_queue_lock_refuses_second_controller(tmp_path, monkeypatch):
    _root, exp, _scan, _plan, _rules = _configure(monkeypatch, tmp_path)
    lock = exp / "queue.lock"
    lock.write_text('{"pid":123}\n')
    with pytest.raises(q.QueueError, match="lock exists"):
        q.acquire_lock()


def test_downstream_resume_uses_frozen_collect_without_overwrite(tmp_path, monkeypatch):
    root, exp, _scan, plan, _rules = _configure(monkeypatch, tmp_path)
    manifests = [_manifest(exp, "sources_archive_1m_batch01.json")]
    cohort = exp / "cohort_queue_round_001"
    _write_collection(root, exp, plan, manifests, cohort)
    commands, commits = [], []
    base_runner = _valid_stage_runner(root, exp, plan, manifests, capacity=False)

    def runner(args, **kwargs):
        commands.append(args)
        base_runner(args, **kwargs)

    result = q.run_downstream(manifests, "queue_round_001", runner=runner, output=_git_output,
                              commit_fn=lambda paths: commits.append([p.name for p in paths]) or "new",
                              input_validator=lambda paths: None)
    assert result["collection_commit"] == "frozencommit"
    assert len(commands) == 2
    assert all("collect" not in call for call in commands)
    assert commits == [["outcomes.jsonl", "lineage_errors.jsonl", "summary.json"],
                       ["selection_ledger.jsonl", "dataset_ledger.jsonl", "selection_receipt.json"]]


def test_resume_completed_batch_without_round_continues_downstream(tmp_path, monkeypatch):
    root, exp, scan, plan, rules = _configure(monkeypatch, tmp_path)
    first, three = _manifest(exp, "sources_archive_1m_batch01.json"), _manifest(exp, "sources_archive_3m.json")
    first_master, three_master = (_master(scan, first, plan, rules, root=root), _master(scan, three, plan, rules, root=root))
    monkeypatch.setattr(q, "queue_items", lambda: [("3m", three, True)])
    monkeypatch.setattr(q, "ensure_committed", lambda *args, **kwargs: "frozen")
    monkeypatch.setattr(q, "validate_master", lambda master, manifest: json.loads(master.read_text()))
    for name, manifest, master in (("1m_batch01", first, first_master), ("3m", three, three_master)):
        summary = exp / f"queue_summary_{name}.json"
        summary.write_text(json.dumps({"master_path": str(master.relative_to(root)), "master_sha256": _digest(master)}))
    state = {"schema_version": 2, "binding": q.binding(), "batches": {}, "rounds": [], "status": "idle"}
    for name, manifest, master in (("1m_batch01", first, first_master), ("3m", three, three_master)):
        summary = exp / f"queue_summary_{name}.json"
        state["batches"][name] = {"manifest_path": manifest.name, "manifest_sha256": _digest(manifest),
                                  "master_path": str(master.relative_to(root)), "master_sha256": _digest(master),
                                  "summary_path": summary.name, "summary_sha256": _digest(summary), "summary_commit": "frozen"}
    q.save_state(state)
    calls = []
    monkeypatch.setattr(q, "run_downstream", lambda manifests, tag, **kwargs: calls.append((manifests, tag)) or {
        "tag": tag, "capacity_gate": False, "selection_receipt_path": "unused", "selection_receipt_sha256": "x"})
    result = q.run_queue(output=_git_output, waiter=_wait_now, commit_fn=lambda paths: "unused")
    assert result["status"] == "complete"
    assert calls and calls[0][1] == "queue_round_001"


def test_commit_exact_real_git_keeps_unrelated_staged_file(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    for args in (("git", "init", "-b", "main"), ("git", "config", "user.email", "queue@example.test"),
                 ("git", "config", "user.name", "Queue Test")):
        subprocess.run(args, cwd=root, check=True, capture_output=True)
    (root / "tracked.txt").write_text("base\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=root, check=True, capture_output=True)
    unrelated, target = root / "unrelated.txt", root / "queue.json"
    unrelated.write_text("keep staged\n")
    subprocess.run(["git", "add", "unrelated.txt"], cwd=root, check=True)
    target.write_text("queue artifact\n")
    monkeypatch.setattr(q, "ROOT", root)
    commit = q.commit_exact([target])
    assert commit == subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    assert subprocess.check_output(["git", "show", "--format=", "--name-only", "HEAD"], cwd=root, text=True).splitlines() == ["queue.json"]
    assert subprocess.check_output(["git", "diff", "--cached", "--name-only"], cwd=root, text=True).splitlines() == ["unrelated.txt"]



def test_self_hashed_fabricated_selection_winners_are_rejected(tmp_path, monkeypatch):
    root, exp, _scan, plan, _rules = _configure(monkeypatch, tmp_path)
    manifests = [_manifest(exp, "sources_archive_1m_batch01.json")]
    cohort, outcomes, selection = (exp / "cohort_queue_round_001", exp / "outcomes_queue_round_001", exp / "selection_queue_round_001")
    _write_collection(root, exp, plan, manifests, cohort)
    _write_label(exp, plan, cohort, outcomes, capacity=False)
    _write_selection(root, exp, outcomes, selection, capacity=True, forged=True)
    with pytest.raises(q.QueueError, match="selection ledger event identities"):
        q.validate_selection_stage(outcomes, selection)


def test_label_rejects_self_hashed_immutable_frozen_field_drift(tmp_path, monkeypatch):
    root, exp, _scan, plan, _rules = _configure(monkeypatch, tmp_path)
    manifests = [_manifest(exp, "sources_archive_1m_batch01.json")]
    cohort, outcomes = exp / "cohort_queue_round_001", exp / "outcomes_queue_round_001"
    _write_collection(root, exp, plan, manifests, cohort)
    _write_label(exp, plan, cohort, outcomes, capacity=False)
    rows = [json.loads(line) for line in (outcomes / "outcomes.jsonl").read_text().splitlines() if line.strip()]
    rows[0]["source_path"] = "forged.csv"
    output = outcomes / "outcomes.jsonl"
    output.write_text("".join(json.dumps(row) + "\n" for row in rows))
    summary = outcomes / "summary.json"
    payload = json.loads(summary.read_text())
    payload["outcomes_sha256"] = _digest(output)
    summary.write_text(json.dumps(payload))
    with pytest.raises(q.QueueError, match="outcomes immutable"):
        q.validate_label_stage(cohort, outcomes)


@pytest.mark.parametrize("field,value", [("core_end_time", "2099-01-01T00:00:00+00:00"), ("split", "test")])
def test_dataset_rejects_self_hashed_immutable_selection_field_drift(tmp_path, monkeypatch, field, value):
    root, exp, _scan, plan, _rules = _configure(monkeypatch, tmp_path)
    manifests = [_manifest(exp, "sources_archive_1m_batch01.json")]
    cohort, outcomes, selection = (exp / "cohort_queue_round_001", exp / "outcomes_queue_round_001", exp / "selection_queue_round_001")
    _write_collection(root, exp, plan, manifests, cohort, count=3000)
    _write_label(exp, plan, cohort, outcomes, capacity=True)
    _write_selection(root, exp, outcomes, selection, capacity=True)
    dataset, receipt = selection / "dataset_ledger.jsonl", selection / "selection_receipt.json"
    rows = [json.loads(line) for line in dataset.read_text().splitlines() if line.strip()]
    rows[0][field] = value
    dataset.write_text("".join(json.dumps(row) + "\n" for row in rows))
    payload = json.loads(receipt.read_text())
    payload["dataset_ledger_sha256"] = _digest(dataset)
    payload["selected_events_sha256"] = _digest(dataset)
    receipt.write_text(json.dumps(payload))
    with pytest.raises(q.QueueError, match="dataset ledger immutable"):
        q.validate_selection_stage(outcomes, selection)


def test_selection_rejects_self_hashed_duplicate_event_id(tmp_path, monkeypatch):
    root, exp, _scan, plan, _rules = _configure(monkeypatch, tmp_path)
    manifests = [_manifest(exp, "sources_archive_1m_batch01.json")]
    cohort, outcomes, selection = (exp / "cohort_queue_round_001", exp / "outcomes_queue_round_001", exp / "selection_queue_round_001")
    _write_collection(root, exp, plan, manifests, cohort)
    _write_label(exp, plan, cohort, outcomes, capacity=False)
    _write_selection(root, exp, outcomes, selection, capacity=False)
    ledger, receipt = selection / "selection_ledger.jsonl", selection / "selection_receipt.json"
    ledger.write_text(ledger.read_text() * 2)
    payload = json.loads(receipt.read_text())
    payload["selection_ledger_sha256"] = _digest(ledger)
    receipt.write_text(json.dumps(payload))
    with pytest.raises(q.QueueError, match="selection ledger event identity"):
        q.validate_selection_stage(outcomes, selection)

def test_round_two_uses_explicit_incremental_cache_and_freezes_receipt(tmp_path, monkeypatch):
    root, exp, _scan, plan, _rules = _configure(monkeypatch, tmp_path)
    manifests = [_manifest(exp, "sources_archive_1m_batch01.json")]
    prior_cohort, prior_outcomes = exp / "cohort_queue_round_001", exp / "outcomes_queue_round_001"
    _write_collection(root, exp, plan, manifests, prior_cohort)
    _write_label(exp, plan, prior_cohort, prior_outcomes, capacity=False)
    calls, commits = [], []
    base_runner = _valid_stage_runner(root, exp, plan, manifests, capacity=False)

    def runner(args, **kwargs):
        calls.append(args)
        base_runner(args, **kwargs)

    result = q.run_downstream(manifests, "queue_round_002", runner=runner, output=_git_output,
                              commit_fn=lambda paths: commits.append([path.name for path in paths]) or "new",
                              input_validator=lambda paths: None)
    label = next(call for call in calls if "yoyo.datasets.ma_profit_incremental_labels" in call)
    assert result["capacity_gate"] is False
    assert label[label.index("--reuse-label-dir") + 1] == str(prior_outcomes)
    assert label[label.index("--reuse-events") + 1] == str(prior_cohort / "frozen_events.jsonl")
    assert label[label.index("--reuse-sources") + 1] == str(prior_cohort / "frozen_sources.json")
    assert ["outcomes.jsonl", "lineage_errors.jsonl", "summary.json", "reuse_receipt.json"] in commits


def test_round_two_fails_closed_without_complete_frozen_prior_label(tmp_path, monkeypatch):
    _root, exp, _scan, _plan, _rules = _configure(monkeypatch, tmp_path)
    manifests = [_manifest(exp, "sources_archive_1m_batch01.json")]
    calls = []
    with pytest.raises(q.QueueError, match="complete frozen prior cohort and outcomes"):
        q.run_downstream(manifests, "queue_round_002", runner=lambda *args, **kwargs: calls.append(args),
                         output=_git_output, commit_fn=lambda paths: "unused", input_validator=lambda paths: None)
    assert not calls


def test_incremental_wrapper_drift_invalidates_queue_binding(tmp_path, monkeypatch):
    root, _exp, _scan, _plan, _rules = _configure(monkeypatch, tmp_path)
    state = q.load_state()
    q.save_state(state)
    wrapper = root / "yoyo" / "datasets" / "ma_profit_incremental_labels.py"
    wrapper.write_text("changed wrapper")
    with pytest.raises(q.QueueError, match="queue binding drift"):
        q.load_state()


def test_round_three_requires_prior_incremental_receipt(tmp_path, monkeypatch):
    root, exp, _scan, plan, _rules = _configure(monkeypatch, tmp_path)
    manifests = [_manifest(exp, "sources_archive_1m_batch01.json")]
    prior_cohort, prior_outcomes = exp / "cohort_queue_round_002", exp / "outcomes_queue_round_002"
    _write_collection(root, exp, plan, manifests, prior_cohort)
    _write_label(exp, plan, prior_cohort, prior_outcomes, capacity=False)
    with pytest.raises(q.QueueError, match="partial downstream stage"):
        q.prior_incremental_inputs("queue_round_003", output=_git_output)

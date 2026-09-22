import base64
import contextlib
import hashlib
import io
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from yoyo.data.ma_profit_import_queue import QueueError, frozen_sources, require_ready, terminal_statuses


def audit(symbol, status="complete"):
    return {"symbol": symbol, "status": status, "gzip_path": rf"C:\\x\\binance_um_{symbol}_1m_2.csv.gz", "gzip_sha256": "g", "csv_sha256": "c", "rows": 2, "first_time": "a", "last_time": "b", "non_bar_gaps": 0, "source_audit_sha256": "a"}


def test_partial_and_terminal_failure_are_not_ready():
    assert terminal_statuses({"A": audit("A")}, ["A", "B"])["B"] == "missing"
    with pytest.raises(QueueError, match="failed"):
        require_ready({"A": audit("A", "failed")}, ["A"])


def test_frozen_sources_preserves_fixed_symbols_and_windows_basename(tmp_path):
    batch = {"batch_id": "batch_06", "symbols": ["A", "B"]}
    result = frozen_sources(experiment=tmp_path / "exp", batch=batch, audits={"A": audit("A"), "B": audit("B")}, binding={"config_sha256": "x"}, config_sha256="x")
    assert [x["symbol"] for x in result["sources"]] == ["A", "B"]
    assert result["sources"][0]["source_path"] == "compressed/binance_um_A_1m_2.csv.gz"


def test_wait_partial_then_ready_and_terminal_is_not_retried(monkeypatch):
    import yoyo.data.ma_profit_import_queue as q
    samples = iter([{}, {'A': audit('A')}])
    sleeps = []
    monkeypatch.setattr(q.time, 'sleep', sleeps.append)
    assert q.wait_ready(lambda: next(samples), ['A'])['A']['status'] == 'complete'
    assert sleeps == [50]
    with pytest.raises(QueueError, match='no_data'):
        q.wait_ready(lambda: {'A': audit('A', 'no_data')}, ['A'])
    assert sleeps == [50]


def test_remote_audits_reads_utf8_json_under_cp936_default(tmp_path, monkeypatch):
    """The remote Windows default encoding must not alter a non-ASCII audit symbol."""
    import yoyo.data.ma_profit_import_queue as q

    symbol = "币安人生USDT"
    remote_root = tmp_path / "remote"
    (remote_root / "compressed_audits").mkdir(parents=True)
    (remote_root / "audits").mkdir()
    binding = {"config_sha256": "frozen"}
    (remote_root / "run_binding.json").write_text(json.dumps(binding), encoding="utf-8")
    source = remote_root / "audits" / f"{symbol}.json"
    source.write_text(json.dumps({"symbol": symbol}, ensure_ascii=False), encoding="utf-8")
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    compressed = remote_root / "compressed_audits" / f"{symbol}.json"
    compressed.write_text(
        json.dumps(
            {"symbol": symbol, "status": "complete", "source_audit_sha256": source_sha},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    original_read_text = Path.read_text

    def cp936_default(self, encoding=None, errors=None):
        return original_read_text(self, encoding="cp936" if encoding is None else encoding, errors=errors)

    def remote_process(args, **kwargs):
        command = args[-1]
        encoded = re.search(r"b64decode\('([^']+)'\)", command).group(1)
        script = base64.b64decode(encoded).decode("utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exec(script, {"__name__": "__main__"})
        return SimpleNamespace(returncode=0, stdout=output.getvalue().encode("utf-8"), stderr=b"")

    monkeypatch.setattr(Path, "read_text", cp936_default)
    monkeypatch.setattr(q.subprocess, "run", remote_process)
    rows = q._remote_audits("host", str(remote_root), [symbol], binding)
    assert rows[symbol]["symbol"] == symbol
    assert rows[symbol]["source_audit_sha256"] == source_sha


def _completed_import(tmp_path, monkeypatch):
    import json
    import yoyo.data.ma_profit_import_queue as q
    root = tmp_path / 'repo'; root.mkdir()
    monkeypatch.setattr(q, 'ROOT', root)
    out = root / 'inputs/batch_06'; (out / 'series').mkdir(parents=True)
    csv = out / 'series/A.csv'; csv.write_text('real source bytes\n')
    digest = q.sha256_file(csv)
    manifest = {'sources': [{'symbol': 'A', 'csv_sha256': digest, 'sha256': 'gzip', 'rows': 2}]}
    source = {'symbol': 'A', 'source_path': str(csv.relative_to(root)), 'sha256': digest, 'gzip_sha256': 'gzip', 'rows': 2}
    published = out / 'sources_imported_1m.json'; published.write_text(json.dumps({'sources': [source]}))
    receipt = {'gate_open': True, 'sources_complete': 1, 'source_manifest_sha256': q.sha256_file(published)}
    return q, root, out, csv, manifest, receipt


def test_verify_exact_sources_and_changed_csv(tmp_path, monkeypatch):
    import json
    q, root, out, csv, manifest, receipt = _completed_import(tmp_path, monkeypatch)
    q.verify_import(manifest=manifest, out=out, receipt=receipt)
    csv.write_text('corrupted\n')
    with pytest.raises(QueueError, match='CSV SHA drift'):
        q.verify_import(manifest=manifest, out=out, receipt=receipt)
    csv.write_text('real source bytes\n')
    published = out / 'sources_imported_1m.json'
    payload = json.loads(published.read_text()); payload['sources'] *= 2
    published.write_text(json.dumps(payload)); receipt['source_manifest_sha256'] = q.sha256_file(published)
    with pytest.raises(QueueError, match='source set drift'):
        q.verify_import(manifest=manifest, out=out, receipt=receipt)


def test_completed_import_resumes_without_calling_importer(tmp_path, monkeypatch):
    import json
    q, root, out, csv, manifest, receipt = _completed_import(tmp_path, monkeypatch)
    manifest_path = root / 'frozen.json'; manifest_path.write_text(json.dumps(manifest))
    receipt['binding'] = {'archive_manifest_sha256': q.sha256_file(manifest_path)}
    receipt_path = root / 'receipt.json'; receipt_path.write_text(json.dumps(receipt))
    monkeypatch.setattr(q, 'import_archives', lambda **kw: pytest.fail('must not re-import'))
    got = q.import_frozen(manifest_path=manifest_path, staging=root / 'staging', out=out, receipt_path=receipt_path)
    assert got == receipt
    csv.write_text('changed')
    with pytest.raises(QueueError, match='CSV SHA drift'):
        q.import_frozen(manifest_path=manifest_path, staging=root / 'staging', out=out, receipt_path=receipt_path)


def test_transfer_uses_fixed_upstream_and_verifies_scp_bytes(tmp_path, monkeypatch):
    import hashlib
    from pathlib import Path
    from types import SimpleNamespace
    import yoyo.data.ma_profit_import_queue as q
    expected = b'actual gzip stream'
    manifest = {'sources': [{'source_path': 'compressed/A.csv.gz', 'sha256': hashlib.sha256(expected).hexdigest(), 'upstream_gzip_path': 'C:/fable/input/compressed/A.csv.gz'}]}
    calls = []
    monkeypatch.setattr(q, 'require_space', lambda path: None)
    def process(args, **kw):
        calls.append(args); Path(args[-1]).write_bytes(expected); return SimpleNamespace(returncode=0)
    monkeypatch.setattr(q.subprocess, 'run', process)
    staging = tmp_path / 'staging'
    q.transfer('host', 'C:/fable/input', manifest, staging)
    q.transfer('host', 'C:/fable/input', manifest, staging)
    assert len(calls) == 1
    (staging / 'compressed/A.csv.gz').write_bytes(b'corrupt')
    with pytest.raises(QueueError, match='existing gzip SHA drift'):
        q.transfer('host', 'C:/fable/input', manifest, staging)
    manifest['sources'][0]['upstream_gzip_path'] = 'C:/unrelated/A.csv.gz'
    with pytest.raises(QueueError, match='unexpected upstream'):
        q.transfer('host', 'C:/fable/input', manifest, staging)


def test_freeze_real_git_preserves_other_staged_file(tmp_path, monkeypatch):
    import subprocess
    import yoyo.data.ma_profit_import_queue as q
    root = tmp_path / 'repo'; root.mkdir(); monkeypatch.setattr(q, 'ROOT', root)
    for args in [('git','init','-b','main'), ('git','config','user.email','test@example.test'), ('git','config','user.name','test')]:
        subprocess.run(args, cwd=root, capture_output=True, check=True)
    other = root / 'other.txt'; other.write_text('keep staged')
    subprocess.run(['git','add','other.txt'], cwd=root, check=True)
    target = root / 'receipt.json'; target.write_text('{}')
    q.freeze([target])
    assert q.git('show','--format=','--name-only','HEAD') == 'receipt.json'
    assert q.git('diff','--cached','--name-only') == 'other.txt'
    assert q.freeze([target]) == q.git('rev-parse','HEAD')


def test_resume_freezes_existing_cleanup_receipt_before_completing(tmp_path, monkeypatch):
    import json
    q, root, out, csv, manifest, receipt = _completed_import(tmp_path, monkeypatch)
    exp = root / 'experiment'; exp.mkdir()
    manifest['sources'][0]['source_path'] = 'compressed/A.csv.gz'
    manifest_path = exp / 'frozen.json'; manifest_path.write_text(json.dumps(manifest))
    receipt['binding'] = {'archive_manifest_sha256': q.sha256_file(manifest_path)}
    monkeypatch.setattr(q, 'import_frozen', lambda **kw: receipt)
    freezes = []
    monkeypatch.setattr(q, 'freeze', lambda paths: freezes.append([p.name for p in paths]) or 'commit')
    batch = {'batch_id': 'batch_06'}
    q.finish_batch(exp, batch, manifest_path, exp / 'staging', out)
    freezes.clear()
    q.finish_batch(exp, batch, manifest_path, exp / 'staging', out)
    assert freezes[-1] == ['batch_06_cleanup_receipt.json']
    path = exp / 'imports_1m/batch_06_cleanup_receipt.json'
    payload = json.loads(path.read_text()); payload['source_manifest_sha256'] = 'drift'
    path.write_text(json.dumps(payload))
    with pytest.raises(QueueError, match='cleanup receipt binding drift'):
        q.finish_batch(exp, batch, manifest_path, exp / 'staging', out)

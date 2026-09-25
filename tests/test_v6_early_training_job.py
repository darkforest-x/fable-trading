"""Lifecycle evidence must not report a failed or duplicate GPU job as complete."""
import json
import sys

import pytest

from scripts.windows import run_v6_early_training as runner


def test_failure_is_recorded_and_same_job_cannot_restart(tmp_path,monkeypatch):
    ds=tmp_path/'dataset';pf=ds/'preflight';pf.mkdir(parents=True)
    receipt=pf/'preflight_receipt.json';receipt.write_text('{}')
    monkeypatch.setattr(sys,'argv',['runner','--dataset-root',str(ds),'--preflight-dir',str(pf),
        '--expected-preflight-sha256',runner.sha256_file(receipt)])
    called=[]
    def fail(*args):called.append(True);raise RuntimeError('simulated GPU failure')
    monkeypatch.setattr(runner,'train',fail)
    with pytest.raises(RuntimeError,match='simulated'):runner.main()
    state=json.loads((ds/'training_job.json').read_text())
    assert state['status']=='failed' and 'simulated' in state['error']
    with pytest.raises(FileExistsError):runner.main()
    assert len(called)==1


def test_preflight_change_never_calls_trainer(tmp_path,monkeypatch):
    ds=tmp_path/'dataset';pf=ds/'preflight';pf.mkdir(parents=True)
    (pf/'preflight_receipt.json').write_text('{}')
    monkeypatch.setattr(sys,'argv',['runner','--dataset-root',str(ds),'--preflight-dir',str(pf),
        '--expected-preflight-sha256','0'*64])
    monkeypatch.setattr(runner,'train',lambda *args:pytest.fail('trainer must not run'))
    with pytest.raises(ValueError,match='changed'):runner.main()
    assert not (ds/'training_job.json').exists()

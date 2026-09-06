"""Synthetic access/identity/immutability tests; no network or market data."""
import importlib.util
import json
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location("chartprime_collector", Path(__file__).with_name("collect_sources.py"))
c=importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


def publication(access=1, owner="ChartPrime"):
    card={"uuid":"abc123XY","user":{"username":owner},"name":"Fixture",
          "script":{"access":access,"has_access":True,"script_id_part":"PUB;fixture","version_maj":1},
          "description_ast":{"type":"root","children":["A",{"type":"b","children":["B"]}]}}
    return '<script type="application/prs.init-data+json">'+json.dumps({"x":{"ssrIdeaData":card}})+'</script>'


def item():
    return {"id":"abc123XY","title":"Fixture","url":"https://www.tradingview.com/script/abc123XY/"}


@pytest.mark.parametrize("access",[0,2,3,None])
def test_protected_publication_never_requests_source(monkeypatch,tmp_path,access):
    calls=[]
    def fetch(url):
        calls.append(url)
        return publication(access),200,url
    monkeypatch.setattr(c,"fetch",fetch)
    r=c.collect(item(),tmp_path)
    assert len(calls)==1
    assert r["evidence_level"]=="official_description_only"
    assert not list(tmp_path.glob("*.pine"))


def test_public_source_preserves_license_hash_and_does_not_claim_review(monkeypatch,tmp_path):
    source='// Copyright fixture\n//@version=6\nindicator("fixture")\n'
    def fetch(url):
        return (publication() if 'tradingview.com/script/' in url else json.dumps({"scriptAccess":"open_no_auth","source":source})),200,url
    monkeypatch.setattr(c,"fetch",fetch)
    r=c.collect(item(),tmp_path)
    assert r["description"]=="AB"
    assert r["evidence_level"].endswith("not_yet_manually_reviewed")
    assert (tmp_path/"abc123XY.pine").read_text()==source
    assert r["source_sha256"]==c.sha256(source.encode()).hexdigest()
    monkeypatch.setattr(c,"fetch",lambda _:pytest.fail("Existing record must not refetch"))
    assert c.collect(item(),tmp_path)==r


def test_wrong_author_and_unsafe_identity_fail_closed(monkeypatch,tmp_path):
    monkeypatch.setattr(c,"fetch",lambda u:(publication(owner="Other"),200,u))
    assert c.collect(item(),tmp_path)["error_type"]=="ValueError"
    with pytest.raises(ValueError): c.collect({**item(),"id":"../escape"},tmp_path)


def test_source_response_cannot_upgrade_access(monkeypatch,tmp_path):
    def fetch(url):
        return (publication() if 'tradingview.com/script/' in url else json.dumps({"scriptAccess":"protected","source":"not public"})),200,url
    monkeypatch.setattr(c,"fetch",fetch)
    r=c.collect(item(),tmp_path)
    assert r["error_type"]=="ValueError"
    assert not list(tmp_path.glob("*.pine"))

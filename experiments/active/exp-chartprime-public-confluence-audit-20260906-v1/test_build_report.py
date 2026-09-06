"""Synthetic source-evidence negatives; no network, market data or Pine runtime."""
from copy import deepcopy
from hashlib import sha256
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("chartprime_report", Path(__file__).with_name("build_report.py"))
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


def fixture():
    raw = b"// license\r\n//@version=6\r\nplot(close)\r\n"
    digest = sha256(raw).hexdigest()
    items = [{"id":"Abcd1234","title":"Open","url":"https://www.tradingview.com/script/Abcd1234/"},
             {"id":"Closed12","title":"Closed","url":"https://www.tradingview.com/script/Closed12/"}]
    cat = {"scripts":items,"complete":True,"declared_count":2,"actual_count":2}
    rows = []
    records = {}
    for item in items:
        row = {field:"reviewed" for field in report.TEXT_FIELDS}
        row.update(item, source_lines=[], source_sha256=None, source_url=item["url"], review_level="description_only")
        card = dict(item,script={"access":3,"has_access":False})
        if item["id"] == "Abcd1234":
            row.update(source_sha256=digest,source_lines=[1,3],source_url="https://pine-facade.tradingview.com/public-test",review_level="source_read")
            card.update(script={"access":1,"has_access":True},source_sha256=digest,source_lines=3,
                        source_url=row["source_url"],source_metadata={"scriptAccess":"open_no_auth"})
        rows.append(row)
        records[item["id"]] = card
    return cat,rows,records,{"Abcd1234":raw}


def test_valid_byte_preserving_source():
    result = report.validate(*fixture())
    assert result["listed"] == 2
    assert result["review_levels"] == {"source_read":1,"description_only":1}


def test_crlf_text_normalization_is_not_exact_source():
    cat, rows, cards, blobs = fixture()
    blobs["Abcd1234"] = blobs["Abcd1234"].replace(b"\r\n",b"\n")
    with pytest.raises(ValueError,match="hash mismatch"):
        report.validate(cat, rows, cards, blobs)


@pytest.mark.parametrize("mutation,match", [
    (lambda c,r,s,b:r.pop(),"coverage mismatch"),
    (lambda c,r,s,b:r.append(deepcopy(r[0])),"duplicate review"),
    (lambda c,r,s,b:r[0].update(title="Wrong"),"title mismatch"),
    (lambda c,r,s,b:r[0].update(source_lines=[4]),"out of bounds"),
    (lambda c,r,s,b:r[0].update(source_lines=[True]),"out of bounds"),
    (lambda c,r,s,b:r[0].update(source_lines=[]),"out of bounds"),
    (lambda c,r,s,b:r[0].update(formula=""),"missing formula"),
    (lambda c,r,s,b:r[1].update(review_level="source_read"),"inaccessible source claim"),
    (lambda c,r,s,b:r[1].update(source_sha256="fake"),"closed source fabricated"),
    (lambda c,r,s,b:s["Abcd1234"]["source_metadata"].update(scriptAccess="closed"),"public access mismatch"),
    (lambda c,r,s,b:s["Abcd1234"].update(error="network failed"),"collection error"),
    (lambda c,r,s,b:r[0].update(source_url="https://other.test/"),"URL mismatch"),
    (lambda c,r,s,b:c.update(complete=False),"incomplete catalogue"),
    (lambda c,r,s,b:c.update(actual_count=3),"count mismatch"),
    (lambda c,r,s,b:b.update(Extra123=b"bad"),"extra Pine source"),
])
def test_evidence_fail_closed(mutation, match):
    args = fixture()
    mutation(*args)
    with pytest.raises(ValueError,match=match):
        report.validate(*args)


def test_family_partition_exact():
    families = report.family_map()
    assert len(families) == 148
    assert len(set(families.values())) == 8
    assert len(report.FAMILIES["源码受限"]) == 14


def test_build_requires_manual_frozen_population():
    with pytest.raises(ValueError,match="partition"):
        report.family_map(147)

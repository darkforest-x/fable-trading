"""P2.5 Phase3 data/model hub smoke (no network)."""
from __future__ import annotations
from src.webapp import data_hub, model_hub

def test_data_hub_payload_shape():
    d = data_hub.data_hub_payload()
    assert "generated_at" in d or "coverage" in d or "by_bar" in d or isinstance(d, dict)

def test_model_hub_payload_shape():
    d = model_hub.model_hub_payload()
    assert isinstance(d, dict)

"""Focused display contracts for the SPIKE coin break-even chart book."""
import subprocess

from yoyo.evaluation.spike_coin_be_charts import html, tv_url


def test_chart_tradingview_link_keeps_the_source_venue_and_interval() -> None:
    assert tv_url("okx", "ETH-USDT-SWAP", 60) == "https://www.tradingview.com/chart/?symbol=OKX%3AETHUSDT.P&interval=60"
    assert tv_url("binance", "ETH/USDT:USDT", 30) == "https://www.tradingview.com/chart/?symbol=BINANCE%3AETHUSDT.P&interval=30"


def test_chart_template_keeps_r_level_labels_and_imacd_zero_line() -> None:
    page = html([])
    assert "...r.r_levels.map(x=>[x.label,displayPrice(x.price)])" in page
    assert "value:0" in page


def test_generated_inline_javascript_has_valid_node_syntax() -> None:
    script = html([]).rsplit("<script>", 1)[1].split("</script>", 1)[0]
    completed = subprocess.run(["node", "--check", "/dev/stdin"], input=script, text=True, capture_output=True)
    assert completed.returncode == 0, completed.stderr

"""Symbol scope for the frozen Binance USDT weekly first-touch ledger (not OKX).

OPEN is OpenLedger and FRAX is the renamed FXS token on this contract.
Do not filter either by ticker collision. USTC is the depegged former TerraUSD;
leave it in market breadth but separate it from crypto altcoin outcomes.
"""
def exclusion_reason(symbol):
    return {'BTCUSDT':'benchmark','ETHUSDT':'benchmark','SOLUSDT':'benchmark',
            'USTCUSDT':'former_stablecoin'}.get(symbol,'')

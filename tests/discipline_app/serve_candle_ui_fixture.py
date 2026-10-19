"""Loopback chart QA with synthetic public candles; never connects to an account.

Build with VITE_SPIKE_SERVICE_URL and VITE_SPIKE_CANDLES_HTTP set to
http://127.0.0.1:18921, VITE_SPIKE_CANDLES_WS=ws://127.0.0.1:18921/ws/v5/business,
VITE_SPIKE_LIVE_QUOTES=off. Pass --web-directory to this server.
"""
import argparse
import asyncio
import math
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--web-directory', required=True)
    options = parser.parse_args()
    app = FastAPI()
    metrics = dict(reads=0, streams=0, active=0, ticks=0, writes=0)
    control = dict(offline=False)
    steps = {'1m': 60, '5m': 300, '15m': 900, '1H': 3600, '4H': 14400, '1D': 86400}

    def candle(stamp, step, symbol, live=False):
        base = 2400 if symbol.startswith('ETH') else 80000
        index = stamp // step
        opening = base + math.sin(index / 11) * base * .007
        closing = opening + math.cos(index / 3) * base * .002 + (math.sin(time.time()) * 20 if live else 0)
        return [str(stamp * 1000), str(opening), str(max(opening, closing) + base * .001), str(min(opening, closing) - base * .001), str(closing), '100', str(5 + index % 20), '10000', '0' if live else '1']

    @app.get('/api/v5/market/candles')
    @app.get('/api/v5/market/history-candles')
    def candles(instId: str = 'BTC-USDT-SWAP', bar: str = '15m', after: int = 0):
        metrics['reads'] += 1
        if control['offline']:
            return JSONResponse({'code': '500'}, status_code=503)
        step = steps[bar]
        newest = after // 1000 - step if after else int(time.time()) // step * step
        return {'code': '0', 'data': [candle(newest - i * step, step, instId, i == 0 and not after) for i in range(300)]}

    @app.websocket('/ws/v5/business')
    async def stream(ws: WebSocket):
        await ws.accept()
        metrics['streams'] += 1
        metrics['active'] += 1
        try:
            sub = (await ws.receive_json())['args'][0]
            step = steps[sub['channel'].removeprefix('candle')]
            while True:
                if control['offline']:
                    await ws.close()
                    break
                stamp = int(time.time()) // step * step
                await ws.send_json({'arg': sub, 'data': [candle(stamp, step, sub['instId'], True)]})
                metrics['ticks'] += 1
                try:
                    message = await asyncio.wait_for(ws.receive_text(), timeout=1)
                    if message == 'ping':
                        await ws.send_text('pong')
                except asyncio.TimeoutError:
                    pass
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            metrics['active'] -= 1

    @app.get('/qa/metrics')
    def stats():
        return metrics

    @app.post('/qa/offline/{offline}')
    def offline(offline: bool):
        control['offline'] = offline
        return control

    @app.api_route('/v1/{path:path}', methods=['GET', 'POST', 'PUT', 'DELETE'])
    def no_account(path: str):
        return JSONResponse({'detail': 'Synthetic chart fixture has no account'}, status_code=401)

    @app.get('/qa/mobile', response_class=HTMLResponse)
    def mobile(width: int = 390, height: int = 844):
        width, height = max(320, min(width, 640)), max(480, min(height, 1000))
        return f'<html><body style="margin:0;background:#e9eeea;display:flex;justify-content:center"><iframe title="Synthetic candles {width}px" src="/" style="width:{width}px;height:{height}px;border:0"></iframe></body></html>'

    app.mount('/', StaticFiles(directory=Path(options.web_directory), html=True), name='web')
    uvicorn.run(app, host='127.0.0.1', port=18921, access_log=False)


if __name__ == '__main__':
    main()

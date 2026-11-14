"""Synthetic OKX ``books5`` WebSocket for native iPhone QA; loopback only, no exchange access."""
import argparse
import asyncio
import re
import time

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import uvicorn


app = FastAPI()
metrics = {"subscriptions": 0, "messages": 0, "symbols": []}
SWAP = re.compile(r"^[A-Z0-9]{1,40}-USDT-SWAP$")


@app.get("/qa/metrics")
def qa_metrics():
    return dict(metrics)


@app.websocket("/ws/v5/public")
async def public_depth(socket: WebSocket):
    await socket.accept()
    try:
        request = await socket.receive_json()
        args = request.get("args", []) if isinstance(request, dict) else []
        arg = args[0] if args else {}
        symbol = arg.get("instId", "") if isinstance(arg, dict) else ""
        if request.get("op") != "subscribe" or arg.get("channel") != "books5" or not SWAP.fullmatch(symbol):
            await socket.send_json({"event": "error", "code": "60012", "msg": "Invalid synthetic books5 subscription"})
            return

        metrics["subscriptions"] += 1
        metrics["symbols"] = list(dict.fromkeys([*metrics["symbols"], symbol]))
        await socket.send_json({"event": "subscribe", "arg": arg, "connId": "iphone-depth-qa"})

        tick = 0
        while True:
            try:
                text = await asyncio.wait_for(socket.receive_text(), timeout=0.4)
                if text == "ping":
                    await socket.send_text("pong")
            except asyncio.TimeoutError:
                pass
            base = 100.00
            asks = [[f"{base + 0.01 * i:.2f}", str(7 + i * 5 + tick % 3), "0", str(1 + i % 2)] for i in range(1, 6)]
            bids = [[f"{base - 0.01 * i:.2f}", str(9 + i * 4 + tick % 4), "0", str(1 + i % 3)] for i in range(1, 6)]
            await socket.send_json({
                "arg": arg,
                "data": [{"instId": symbol, "asks": asks, "bids": bids,
                          "ts": str(int(time.time() * 1000)), "seqId": tick}],
            })
            metrics["messages"] += 1
            tick += 1
    except WebSocketDisconnect:
        return


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18924)
    port = parser.parse_args().port
    uvicorn.run(app, host="127.0.0.1", port=port, access_log=False)


if __name__ == "__main__":
    main()

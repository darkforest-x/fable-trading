from __future__ import annotations

import asyncio
import base64
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from yoyo.copier.config import ROOT, app_config, env

DEFAULT_CAPTURE_PATH = "/capture/orders-card"
DEFAULT_CHROME_PATHS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/opt/homebrew/bin/chromium",
    "/usr/bin/google-chrome",
]


def capture_orders_card(output_path: Path | None = None) -> Path:
    """Capture the admin orders card with local Chrome headless."""
    output = output_path or ROOT / "data" / "orders-card-snapshot.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        output.unlink()
    except FileNotFoundError:
        pass

    url = _capture_url()
    chrome = _chrome_path()
    return asyncio.run(_capture_orders_card_cdp(chrome, url, output))


async def _capture_orders_card_cdp(chrome: str, url: str, output: Path) -> Path:
    user_data_dir = Path(tempfile.mkdtemp(prefix="copier-card-capture-"))
    process: subprocess.Popen[str] | None = None
    try:
        cmd = [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--hide-scrollbars",
            "--remote-debugging-port=0",
            f"--user-data-dir={user_data_dir}",
            "about:blank",
        ]
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        ws_url = _wait_for_devtools_url(user_data_dir, process)
        png = await _capture_element_png(ws_url, url)
        output.write_bytes(png)
        if not output.exists() or output.stat().st_size < 2048:
            raise RuntimeError("Chrome 截图文件为空或过小")
        return output
    finally:
        if process is not None:
            _stop_process(process)
        shutil.rmtree(user_data_dir, ignore_errors=True)


def _wait_for_devtools_url(user_data_dir: Path, process: subprocess.Popen[str]) -> str:
    port_file = user_data_dir / "DevToolsActivePort"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=1)
            detail = (stderr or stdout or "").strip()
            raise RuntimeError(f"Chrome 启动失败: {detail[:500]}")
        if port_file.exists():
            parts = port_file.read_text(encoding="utf-8").splitlines()
            if parts:
                port = parts[0].strip()
                with urlopen(f"http://127.0.0.1:{port}/json/list", timeout=3) as response:
                    pages = json.loads(response.read().decode("utf-8"))
                for page in pages:
                    if page.get("type") != "page":
                        continue
                    if str(page.get("url") or "").startswith("chrome-extension://"):
                        continue
                    ws_url = page.get("webSocketDebuggerUrl")
                    if ws_url:
                        return str(ws_url)
        time.sleep(0.1)
    raise RuntimeError("Chrome DevTools 启动超时")


async def _capture_element_png(ws_url: str, url: str) -> bytes:
    import websockets

    next_id = 0

    async with websockets.connect(ws_url, max_size=16 * 1024 * 1024) as ws:
        async def command(method: str, params: dict | None = None) -> dict:
            nonlocal next_id
            next_id += 1
            msg_id = next_id
            await ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
            while True:
                message = json.loads(await ws.recv())
                if message.get("id") == msg_id:
                    if "error" in message:
                        raise RuntimeError(f"Chrome DevTools {method}: {message['error']}")
                    return message.get("result") or {}

        await command("Page.enable")
        await command("Runtime.enable")
        await command(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": 1320,
                "height": 760,
                "deviceScaleFactor": 1,
                "mobile": False,
            },
        )
        await command("Page.navigate", {"url": url})
        rect = await _wait_for_orders_card(command)
        result = await command(
            "Page.captureScreenshot",
            {
                "format": "png",
                "captureBeyondViewport": True,
                "clip": {
                    "x": max(0, float(rect["x"]) - 1),
                    "y": max(0, float(rect["y"]) - 1),
                    "width": float(rect["width"]) + 2,
                    "height": float(rect["height"]) + 2,
                    "scale": 1,
                },
            },
        )
        return base64.b64decode(result["data"])


async def _wait_for_orders_card(command) -> dict[str, float]:
    script = """
(() => {
  const panel = document.querySelector('.capture-orders-panel');
  if (!panel || document.body.innerText.includes('加载中')) return null;
  if (!document.body.innerText.includes('当前持仓与订单')) return null;
  const rowCount = panel.querySelectorAll('tbody tr').length;
  if (!rowCount) return null;
  const rect = panel.getBoundingClientRect();
  return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
})()
"""
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        result = await command(
            "Runtime.evaluate",
            {"expression": script, "returnByValue": True},
        )
        value = ((result.get("result") or {}).get("value") or None)
        if value:
            return value
        await asyncio.sleep(0.25)
    raise RuntimeError("等待订单卡片渲染超时")


def _capture_url() -> str:
    if env.admin_snapshot_url:
        return _with_capture_path(env.admin_snapshot_url)
    for base in _candidate_bases():
        url = _with_capture_path(base)
        if _url_ready(url):
            return url
    return _with_capture_path(f"http://{app_config.admin.host}:{app_config.admin.port}")


def _candidate_bases() -> list[str]:
    return [
        "http://127.0.0.1:5173",
        f"http://{app_config.admin.host}:{app_config.admin.port}",
    ]


def _with_capture_path(base: str) -> str:
    base = str(base or "").rstrip("/")
    if DEFAULT_CAPTURE_PATH in base:
        return base
    return f"{base}{DEFAULT_CAPTURE_PATH}"


def _url_ready(url: str) -> bool:
    for _ in range(2):
        try:
            with urlopen(url, timeout=2.5) as response:
                return 200 <= int(response.status) < 500
        except (OSError, URLError):
            time.sleep(0.15)
    return False


def _chrome_path() -> str:
    if env.chrome_path and Path(env.chrome_path).exists():
        return env.chrome_path
    for path in DEFAULT_CHROME_PATHS:
        if Path(path).exists():
            return path
    for name in ("google-chrome", "chrome", "chromium"):
        found = shutil.which(name)
        if found:
            return found
    raise RuntimeError("未找到 Chrome/Chromium，无法生成管理台截图")


def _stop_process(process: subprocess.Popen[str], *, kill: bool = False) -> None:
    if process.poll() is not None:
        return
    try:
        if kill:
            process.kill()
        else:
            process.terminate()
        process.wait(timeout=2)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def _crop_whitespace(path: Path) -> None:
    try:
        from PIL import Image, ImageChops

        image = Image.open(path).convert("RGB")
        background = Image.new("RGB", image.size, (255, 255, 255))
        bbox = ImageChops.difference(image, background).getbbox()
        if not bbox:
            return
        left, top, right, bottom = bbox
        pad = 12
        cropped = image.crop(
            (
                max(0, left - pad),
                max(0, top - pad),
                min(image.width, right + pad),
                min(image.height, bottom + pad),
            )
        )
        cropped.save(path)
    except Exception:
        return

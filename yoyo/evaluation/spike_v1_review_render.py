"""Render every fixed SPIKE V1 review record through the local web viewer.

This renderer deliberately drives the published Lightweight Charts page in an
isolated Playwright CLI session.  It never redraws candles itself: each PNG is
a browser screenshot after the viewer has loaded the record's local chart JSON
and reports its interactive-chart-ready status.  The output receipt retains the
selected DOM id, canvas dimensions/non-empty pixels, visible guide semantics,
browser errors and PNG digest for each record.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = ROOT / "experiments" / "active" / "exp-spike-v1-okx-133-review-20260911"
DATA = EXPERIMENT / "data"
DEFAULT_OUTPUT = EXPERIMENT / "rendered"
DEFAULT_SITE_URL = "http://localhost:8767/"
VIEWPORT = (1600, 1400)
SAMPLE_SEQUENCES = (1, 46, 133)
PWCLI = Path.home() / ".codex" / "skills" / "playwright" / "scripts" / "playwright_cli.sh"


class RenderError(RuntimeError):
    """The browser viewer failed to render an exact frozen review record."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def slug(value: str) -> str:
    return "".join(char.lower() if char.isalnum() else "-" for char in value).strip("-")


class PlaywrightCli:
    """Small JSON wrapper around the user-scoped Playwright CLI skill wrapper."""

    def __init__(self, session: str):
        if not shutil.which("npx"):
            raise RenderError("npx_not_available")
        if not PWCLI.is_file():
            raise RenderError(f"playwright_cli_wrapper_missing:{PWCLI}")
        self.environment = {**os.environ, "PLAYWRIGHT_CLI_SESSION": session}

    def call(self, *arguments: str, parse_result: bool = False) -> Any:
        completed = subprocess.run([str(PWCLI), "--json", *arguments], cwd=ROOT, env=self.environment,
                                   text=True, capture_output=True, timeout=120)
        if completed.returncode:
            raise RenderError(f"playwright_cli_failed:{' '.join(arguments[:2])}:{completed.stderr.strip() or completed.stdout.strip()}")
        try:
            response = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RenderError("playwright_cli_non_json_response") from exc
        if not parse_result:
            return response
        result = response.get("result")
        if isinstance(result, str):
            try:
                return json.loads(result)
            except json.JSONDecodeError:
                return result
        return result


def _code(source: str) -> str:
    """Wrap a browser async function without interpolating any record data."""
    return "async (page) => {" + source + "}"


def prepare_browser(browser: PlaywrightCli, site_url: str) -> None:
    browser.call("open", site_url)
    browser.call("resize", str(VIEWPORT[0]), str(VIEWPORT[1]))
    # A fresh accessibility snapshot makes the selector contract observable
    # before the script uses explicit, stable data-record selectors.
    browser.call("snapshot")
    browser.call("run-code", _code("""
      await page.waitForSelector('button[data-record]');
      await page.waitForFunction(() => document.querySelectorAll('button[data-record]').length === 133);
      await page.evaluate(() => {
        window.__spikeReviewRenderErrors = [];
        const push = (kind, value) => window.__spikeReviewRenderErrors.push({kind, value: String(value)});
        window.addEventListener('error', event => push('window_error', event.message));
        window.addEventListener('unhandledrejection', event => push('unhandled_rejection', event.reason));
        const original = console.error.bind(console);
        console.error = (...args) => { push('console_error', args.join(' ')); original(...args); };
      });
    """))


def inspect_record(browser: PlaywrightCli, record_id: str, view: str = "signal") -> dict[str, Any]:
    """Select one known id and wait for all three real charts, not just DOM creation."""
    payload = json.dumps(record_id)
    return browser.call("run-code", _code(f"""
      const id = {payload};
      await page.evaluate(() => {{ window.__spikeReviewRenderErrors = []; }});
      const button = page.locator(`button[data-record="${{id}}"]`);
      await button.click();
      await page.waitForFunction((wanted) => {{
        const node = document.querySelector(`button[data-record="${{wanted}}"]`);
        const status = document.querySelector('#chart-status');
        const charts = document.querySelector('#charts');
        const canvases = [...document.querySelectorAll('#price-chart canvas, #momentum-chart canvas, #volume-chart canvas')];
        return Boolean(node?.classList.contains('active') && charts && !charts.hidden
          && charts.dataset.renderedRecordId === wanted
          && status?.textContent?.includes('十字光标') && canvases.length >= 3
          && canvases.every(canvas => canvas.width > 0 && canvas.height > 0));
      }}, id, {{timeout: 30000}});
      if ({json.dumps(view)} === 'global') {{
        await page.locator('#fit-global').click();
        await page.waitForTimeout(150);
      }}
      await page.locator('.review').scrollIntoViewIfNeeded();
      await page.waitForTimeout(150);
      return await page.evaluate((wanted) => {{
        const recordButton = document.querySelector(`button[data-record="${{wanted}}"]`);
        const inspectCanvas = (node) => {{
          let nonzero = false, error = null;
          try {{
            const pixels = node.getContext('2d').getImageData(0, 0, node.width, node.height).data;
            for (let index = 0; index < pixels.length; index += Math.max(4, Math.floor(pixels.length / 4096 / 4) * 4)) {{
              if (pixels[index] || pixels[index + 1] || pixels[index + 2] || pixels[index + 3]) {{ nonzero = true; break; }}
            }}
          }} catch (exception) {{ error = String(exception); }}
          return {{width: node.width, height: node.height, nonzero, error}};
        }};
        const panels = ['price-chart', 'momentum-chart', 'volume-chart'].map((id) => {{
          const canvas = [...document.querySelectorAll(`#${{id}} canvas`)].map(inspectCanvas);
          // Lightweight Charts intentionally keeps transparent crosshair/axis overlay canvases.
          // A panel is drawn when at least one substantive canvas has real pixels.
          return {{id, canvas, primary_nonzero: canvas.some((item) => item.width * item.height >= 4096 && item.nonzero)}};
        }});
        const read = (selector) => document.querySelector(selector)?.textContent?.trim() || null;
        return {{
          selected_id: recordButton?.dataset.record || null,
          selected_active: Boolean(recordButton?.classList.contains('active')),
          rendered_record_id: document.querySelector('#charts')?.dataset.renderedRecordId || null,
          chart_status: read('#chart-status'), charts_visible: !document.querySelector('#charts')?.hidden,
          title: read('#record-title'), sequence: read('#record-sequence'), facts: read('#facts'),
          panels, signal_guide_count: document.querySelectorAll('#price-chart .signal-guide:not([hidden])').length,
          initial_stop_guide_count: document.querySelectorAll('#price-chart .initial-stop-guide:not([hidden])').length,
          errors: window.__spikeReviewRenderErrors || []
        }};
      }}, id);
    """), parse_result=True)


def screenshot(browser: PlaywrightCli, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    browser.call("screenshot", "--filename", str(destination))
    if not destination.is_file():
        raise RenderError("screenshot_not_written")


def chart_payload(record: dict[str, Any], data_root: Path) -> dict[str, Any]:
    return json.loads((data_root / record["chart_path"]).read_text(encoding="utf-8"))


def render_records(
    browser: PlaywrightCli,
    records: list[dict[str, Any]],
    output: Path,
    data_root: Path,
    view: str = "signal",
    on_progress: Callable[[list[dict[str, Any]]], None] | None = None,
) -> list[dict[str, Any]]:
    rendered: list[dict[str, Any]] = []
    images = output / "png"
    for record in records:
        chart = chart_payload(record, data_root)
        filename = f"{int(record['sequence']):03d}_{slug(record['symbol'])}_{record['timeframe_min']}m_{record['signal_close_ms']}.png"
        destination = images / filename
        started = time.monotonic()
        try:
            browser_state = inspect_record(browser, str(record["id"]), view=view)
            screenshot(browser, destination)
            with Image.open(destination) as image:
                size = list(image.size)
            ready = (browser_state["selected_id"] == record["id"] and browser_state["selected_active"]
                     and browser_state["rendered_record_id"] == record["id"]
                     and browser_state["charts_visible"] and "十字光标" in (browser_state["chart_status"] or "")
                     and len(browser_state["panels"]) == 3 and all(item["primary_nonzero"] for item in browser_state["panels"])
                     and not browser_state["errors"] and size[0] >= VIEWPORT[0] and size[1] >= VIEWPORT[1])
            state = "rendered" if ready else "failed_validation"
            error = None if ready else "browser_render_validation_failed"
        except (RenderError, subprocess.SubprocessError, OSError) as exc:
            browser_state, size, state, error = None, None, "failed", str(exc)
        rendered.append({
            "sequence": record["sequence"], "id": record["id"], "symbol": record["symbol"], "timeframe": record["timeframe"],
            "timeframe_min": record["timeframe_min"], "signal_close_ms": record["signal_close_ms"], "chart_path": record["chart_path"],
            "png": str(destination.relative_to(output)), "png_sha256": sha256_file(destination) if destination.is_file() else None,
            "png_size": size, "state": state, "error": error, "elapsed_seconds": round(time.monotonic() - started, 3),
            "view": view,
            "selected_id": browser_state.get("selected_id") if browser_state else None,
            "browser": browser_state,
            "marker_contract": {"signal_marker_bar_open_ms": chart.get("signal_bar_open_ms") or chart.get("signal", {}).get("bar_open_ms"),
                                "initial_stop_starts_at_signal_close_ms": chart.get("signal_close_ms") or chart.get("signal", {}).get("time_ms"),
                                "initial_stop": chart.get("initial_stop")},
        })
        if on_progress and len(rendered) % 25 == 0:
            on_progress(rendered)
    return rendered


def contact_sheets(rows: list[dict[str, Any]], output: Path) -> list[str]:
    """Create thumbnail indexes from browser PNGs; never synthesize chart imagery."""
    result: list[str] = []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["state"] == "rendered":
            grouped[row["timeframe"]].append(row)
    for timeframe, group in sorted(grouped.items()):
        columns, thumb_width, thumb_height, label_height = 3, 360, 225, 30
        sheet = Image.new("RGB", (columns * thumb_width, ((len(group) + columns - 1) // columns) * (thumb_height + label_height)), "#101820")
        painter = ImageDraw.Draw(sheet)
        for number, row in enumerate(group):
            x, y = (number % columns) * thumb_width, (number // columns) * (thumb_height + label_height)
            with Image.open(output / row["png"]) as original:
                thumbnail = original.convert("RGB"); thumbnail.thumbnail((thumb_width, thumb_height))
                sheet.paste(thumbnail, (x, y))
            painter.text((x + 4, y + thumb_height + 6), f"#{row['sequence']} {row['symbol']} {row['timeframe']}", fill="white")
        path = output / "contact_sheets" / f"{slug(timeframe)}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True); sheet.save(path, quality=85)
        result.append(str(path.relative_to(output)))
    return result


def write_readme(output: Path, manifest_hash: str) -> Path:
    """Explain the review-only archive without implying trading performance."""
    path = output / "README.md"
    path.write_text(
        "# SPIKE V1 OKX 133-signal browser render archive\n\n"
        "This archive contains browser PNG captures created by the local official "
        "Lightweight Charts review viewer, one fixed historical signal per image. "
        "Open the local viewer at http://localhost:8767/ while its static server is running.\n\n"
        f"Data manifest SHA-256: `{manifest_hash}`.\n\n"
        "The 2026-08-27 through 2026-09-11 BJT signal roster is a historical review "
        "artifact. Ledger entry/exit fields are backtest-ledger evidence only; the six "
        "live-source records have no trade ledger. A censored label means the frozen OHLC "
        "data ended without an exit conclusion. These images make no trading-return or "
        "execution claim.\n",
        encoding="utf-8",
    )
    return path


def archive(output: Path, receipt_path: Path, readme_path: Path) -> Path:
    path = output / "spike-v1-okx-133-rendered.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for item in sorted((output / "png").glob("*.png")):
            bundle.write(item, item.relative_to(output))
        for item in sorted((output / "contact_sheets").glob("*.jpg")):
            bundle.write(item, item.relative_to(output))
        bundle.write(receipt_path, receipt_path.relative_to(output))
        bundle.write(readme_path, readme_path.relative_to(output))
        bundle.write(DATA / "manifest.json", "data-manifest.json")
    return path


def clear_output(output: Path) -> None:
    """Remove only renderer-owned mutable outputs before a deliberate fresh run."""
    for directory in (output / "png", output / "contact_sheets"):
        shutil.rmtree(directory, ignore_errors=True)
    for filename in ("sample_render_receipt.json", "render_receipt.json", "spike-v1-okx-133-rendered.zip", "README.md"):
        (output / filename).unlink(missing_ok=True)


def global_crop_records(records: list[dict[str, Any]], data_root: Path) -> list[dict[str, Any]]:
    """Return only charts whose signal near-view cannot show every frozen candle."""
    return [record for record in records if len(chart_payload(record, data_root).get("candles", [])) > 245]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-url", default=DEFAULT_SITE_URL)
    parser.add_argument("--data-root", type=Path, default=DATA)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--session", default="spike-v1-133-render")
    parser.add_argument("--samples", action="store_true", help="Render only sequences 1, 46 and 133 for visual approval.")
    parser.add_argument("--clean-output", action="store_true", help="Remove only renderer-owned prior PNGs, receipts and archive before this run.")
    parser.add_argument("--repair-global-crops", action="store_true", help="Re-render only over-245-candle PNGs in global view and merge them into the completed receipt.")
    arguments = parser.parse_args()
    manifest = json.loads((arguments.data_root / "manifest.json").read_text(encoding="utf-8"))
    records = list(manifest.get("records", []))
    if len(records) != 133 or manifest.get("counts", {}).get("available") != 133:
        raise RenderError("manifest_not_133_available_records")
    if arguments.samples and arguments.repair_global_crops:
        raise RenderError("samples_and_repair_global_crops_are_exclusive")
    if arguments.clean_output and arguments.repair_global_crops:
        raise RenderError("clean_output_would_destroy_repair_receipt")
    if arguments.samples:
        records = [record for record in records if int(record["sequence"]) in SAMPLE_SEQUENCES]
    if arguments.clean_output:
        clear_output(arguments.output)
    receipt_path = arguments.output / ("sample_render_receipt.json" if arguments.samples else "render_receipt.json")
    manifest_hash = sha256_file(arguments.data_root / "manifest.json")
    existing_rows: list[dict[str, Any]] | None = None
    if arguments.repair_global_crops:
        previous = json.loads(receipt_path.read_text(encoding="utf-8"))
        if not previous.get("complete") or previous.get("expected_records") != 133 or previous.get("manifest_sha256") != manifest_hash:
            raise RenderError("repair_requires_completed_matching_133_receipt")
        existing_rows = list(previous.get("records", []))
        if len(existing_rows) != 133 or any(row.get("state") != "rendered" for row in existing_rows):
            raise RenderError("repair_requires_133_rendered_rows")
        records = global_crop_records(records, arguments.data_root)
        if len(records) != 34:
            raise RenderError(f"unexpected_global_crop_count:{len(records)}")

    def make_receipt(rows: list[dict[str, Any]], complete: bool, sheets: list[str] | None = None) -> dict[str, Any]:
        return {
            "schema_version": 1, "site_url": arguments.site_url, "viewport": list(VIEWPORT), "manifest_sha256": manifest_hash,
            "expected_records": 133 if arguments.repair_global_crops else len(records), "rendered": sum(row["state"] == "rendered" for row in rows),
            "failed": sum(row["state"] != "rendered" for row in rows), "complete": complete,
            "records": rows, "contact_sheets": sheets or [],
        }

    def flush_progress(rows: list[dict[str, Any]]) -> None:
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(json.dumps(make_receipt(rows, complete=False), ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"progress": len(rows), "rendered": sum(row["state"] == "rendered" for row in rows)}, ensure_ascii=False), flush=True)

    browser = PlaywrightCli(arguments.session)
    try:
        prepare_browser(browser, arguments.site_url)
        rows = render_records(
            browser, records, arguments.output, arguments.data_root,
            view="global" if arguments.repair_global_crops else "signal",
            on_progress=None if arguments.repair_global_crops else flush_progress,
        )
    finally:
        try:
            browser.call("close")
        except RenderError:
            pass
    if existing_rows is not None:
        replacements = {row["sequence"]: row for row in rows}
        rows = [{**row, "view": row.get("view", "signal")} for row in existing_rows]
        rows = [replacements.get(row["sequence"], row) for row in rows]
    sheets = contact_sheets(rows, arguments.output) if not arguments.samples else []
    receipt = make_receipt(rows, complete=True, sheets=sheets)
    if arguments.repair_global_crops:
        receipt["global_crop_repair"] = {
            "rule": "candles > 245 (default signal view is signal -100 through +144)",
            "repaired_records": len(records),
            "sequences": [record["sequence"] for record in records],
        }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    if not arguments.samples and receipt["failed"] == 0:
        readme_path = write_readme(arguments.output, manifest_hash)
        bundle = archive(arguments.output, receipt_path, readme_path)
        receipt["archive"] = str(bundle.relative_to(arguments.output))
        receipt["archive_sha256"] = sha256_file(bundle)
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: receipt[key] for key in ("expected_records", "rendered", "failed")}, ensure_ascii=False))
    if receipt["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

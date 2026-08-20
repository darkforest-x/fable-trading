"""Render the per-tip frames a Causal Review Pack shows.

One frame per tip: the last 200 bars ending at that tip, nothing after it. The
strip is what makes progressive reveal honest -- there is no later data in the
image to fade or crop, because it was never drawn.

Ported from yolo-xx src/yolo_xx/pis/events/render_frames.py at 9296cfa8. One
thing changed, and it is the thing consolidation exists to remove: the original
took `fable_root` and `yoyo_root` arguments and pushed them onto sys.path at
call time, because the chart renderer and the OHLCV loader lived in a different
repository. They do not any more, so the imports are ordinary module-level
imports of the canonical implementations, and two arguments that only existed to
bridge repositories are gone.

`ohlcv_root` stays an explicit argument. Reaching for Path.home() from library
code is what the original spec section 17 forbade, and consolidation does not
change that: where the bars live is the caller's decision, and it is recorded in
the returned provenance.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional, Tuple

WINDOW_DEFAULT = 200


def make_frame_renderer(
    ohlcv_root: Path,
    out_dir: Path,
    *,
    bar: str = "15m",
) -> Tuple[Callable[[str, int, int], Optional[str]], dict]:
    """Return (render_frame, provenance).

    render_frame(symbol, tip_i, window_bars) writes frames/<symbol>_<tip>.png and
    returns its path relative to out_dir, or None when the window runs off the
    series.
    """
    import cv2

    from yoyo.data.loader import list_series, load_series
    from yoyo.layers.l1_detection.data import add_mas
    from yoyo.layers.l1_detection.render import render_chart

    groups = list_series(ohlcv_root, bar=bar)
    sym_paths = {s: p for (src, s), p in groups.items() if src == "okx"}
    frames_dir = out_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    cache: dict = {}

    def render_frame(symbol: str, tip_i: int, window_bars: int = WINDOW_DEFAULT):
        if symbol not in sym_paths:
            return None
        if symbol not in cache:
            cache[symbol] = add_mas(load_series(sym_paths[symbol]))
        fr = cache[symbol]
        start_i = tip_i - window_bars + 1
        if start_i < 0 or tip_i >= len(fr):
            return None
        name = f"{symbol}_{tip_i}.png"
        target = frames_dir / name
        if not target.exists():
            img, _ = render_chart(fr.iloc[start_i : tip_i + 1], out_path=None)
            cv2.imwrite(str(target), img)
        return f"frames/{name}"

    provenance = {
        "ohlcv_root": str(ohlcv_root),
        "bar": bar,
        "window_bars": WINDOW_DEFAULT,
        "render_fn": "yoyo/layers/l1_detection/render.py::render_chart",
        "ma_fn": "yoyo/layers/l1_detection/data.py::add_mas",
        "n_symbols_available": len(sym_paths),
    }
    return render_frame, provenance

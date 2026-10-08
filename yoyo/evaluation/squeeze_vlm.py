"""Owner squeeze breakout v2 pipeline: a VLM judges the owner's pattern on loose rule candidates.

Owner 2026-10-08 (Notion #40): "要不用vlm吧 我感觉你手写的代码和我想要的不一致", then "你先搭流水线，
图我一会发你". exp-owner-squeeze-breakout-20261008-v1 wrote the five conditions as rules; the owner
judged that the rules do not match what they see. Here rules only cast a wide net and a VLM, shown
the owner's own examples, decides which candidates are the pattern.

Stages (each one a CLI subcommand):

  scan      loose rule net per symbol: the v1 always-on breakout candle (direction, body / range
            >= 0.55, close beyond all six MAs and the 12-bar extreme) plus BB compression and volume
            ratio >= 1.5. Dense MAs, engulfing and big body are left to the VLM. Each candidate also
            gets the v1 exits (stop below the MAs, 3R / 5R, 0.2% cost) and 20 matched controls, so a
            later VLM split can be read against random entries without re-simulating.
  render    one PNG per item: the 120 closed bars ending at the signal bar, six MAs (SMA solid, EMA
            dashed), BB200 +/- 2 sigma, volume. Indicators are computed from bars up to the signal
            only and nothing after it is drawn. Candidates and owner examples use the same renderer
            so the VLM never compares two drawing styles.
  judge     Zhipu glm-5.3-flash via yoyo.vision_research.zhipu.ZhipuClient.analyze (right-edge
            launch prompt) with CRITERIA and the owner's reference renders. One ledger row per
            (image, references, criteria, model, prompt); an existing row is never re-billed;
            errors stay unknown and are not retried.
  report    agreement with owner labels on examples not used as references, and VLM verdicts
            against the candidates' exits and controls.

Owner screenshots are transcribed into examples.json (symbol, timeframe, breakout bar time in
Beijing time, side, owner label, screenshot sha256) and re-rendered from Binance USD-M data: the
frozen 5m series up to 2026-09-23, public klines after it. The owner's charts are OKX, so prices
can differ slightly from what the owner saw.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import threading
import time
import warnings
import zlib
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from yoyo.evaluation import market_sync_shock as v1  # noqa: E402
from yoyo.evaluation import squeeze_breakout_v1 as sb  # noqa: E402

plt.rcParams["font.sans-serif"] = ["Hiragino Sans GB", "STHeiti", "Arial Unicode MS", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
EXP = Path("experiments/active/exp-owner-squeeze-vlm-20261008-v1")
CONFIG = EXP / "config.json"
EXAMPLES = EXP / "examples.json"
LEDGER = EXP / "vlm_ledger.jsonl"
IMAGES = EXP / "images"
VISION_RUNTIME = Path("experiments/active/exp-spike-gemini-vision-20260923-v1/runtime")
WINDOW = 120
LOOSE = ("bb", "volume")
FROZEN_END_MS = pd.Timestamp("2026-09-23T00:00:00Z").value // 10**6
INTERVAL = {5: "5m", 15: "15m", 30: "30m", 60: "1h", 240: "4h"}
TF_NAME = {5: "5分钟", 15: "15分钟", 30: "30分钟", 60: "1小时", 240: "4小时"}
MA_COLOR = {20: "#2962ff", 60: "#ff9800", 120: "#9c27b0"}
UP, DOWN = "#089981", "#f23645"
CRITERIA_VERSION = "owner-squeeze-v1"
CRITERIA = """Owner 的「挤压后放量突破」形态（做多与做空镜像）。只看最右端最后一根已收盘K线是不是这种启动K：
1. BB 密集：最右端之前的一段时间里，布林带（灰色上下轨，SMA200±2σ）明显收窄，价格在窄带里横着走。
2. 六条均线密集：SMA20/EMA20/SMA60/EMA60/SMA120/EMA120（实线 SMA、虚线 EMA，蓝=20、橙=60、紫=120）在这段横盘里靠拢、交织成一束；只有 20 组两条线交叉、60/120 仍明显分开，不算密集。
3. 突破K：最右端最后一根是大阳线（做多）或大阴线（做空），实体明显大于前面横盘里的K线，吞没前一根（或前几根）K线，并从均线束和横盘区的一侧收盘突破出去。
4. 放量：最后一根的成交量柱（下方面板）明显高于前面横盘时的成交量。
四条都满足、且最后一根就是刚发生的突破，才算符合（current_state=launching, verdict=match，side 写方向）。只满足一部分、突破K不够大、没有放量、横盘区并不窄、均线没有收拢、或者最后一根之前已经走出很远，都不算符合。
止损放在均线束另一侧、盈亏比 1:3 或 1:5 属于交易执行，不作为判断条件。"""


# --------------------------------------------------------------------------- data

def binance_bars(symbol: str, minutes: int, start_ms: int, end_ms: int, client=None) -> pd.DataFrame:
    """Closed Binance USD-M klines [start_ms, end_ms) on a gap-free grid of bar opens."""
    from yoyo.monitor.market_sync import Binance  # network only when fresh bars are needed

    client = client or Binance()
    step = minutes * 60_000
    now = int(time.time() * 1000)
    rows, cursor = [], start_ms
    while cursor < end_ms:
        page = client.klines(symbol, INTERVAL[minutes], cursor, end_ms - 1, 1000)
        if not page:
            break
        rows += [r for r in page if int(r[6]) < now]
        last = int(page[-1][0])
        if last + step <= cursor:
            break
        cursor = last + step
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame([[int(r[0]), *map(float, r[1:6])] for r in rows],
                      columns=["ts", "open", "high", "low", "close", "volume"]).drop_duplicates("ts").set_index("ts")
    df = df.loc[(df.index >= start_ms) & (df.index < end_ms)]
    return df.reindex(np.arange(df.index.min(), df.index.max() + step, step))


def bars_until(symbol: str, minutes: int, signal_ms: int, history: int = 800) -> tuple[pd.DataFrame, int]:
    """Bars ending at the signal bar (inclusive) with enough history for MAs, BB and the window."""
    step = minutes * 60_000
    start_ms = signal_ms - history * step
    if signal_ms + step <= FROZEN_END_MS:
        raw = v1.read_5m(symbol, pd.Timestamp(start_ms, unit="ms", tz="UTC"),
                         pd.Timestamp(signal_ms + step, unit="ms", tz="UTC"))
        bars = sb.full_bars(raw, minutes)
    else:
        bars = binance_bars(symbol, minutes, start_ms, signal_ms + step)
    bars = bars.loc[bars.index <= signal_ms]
    if not len(bars) or bars.index[-1] != signal_ms or not np.isfinite(bars.iloc[-1][["open", "close"]].to_numpy(float)).all():
        raise ValueError(f"{symbol} {minutes}m has no complete bar at {pd.Timestamp(signal_ms, unit='ms', tz='UTC')}")
    return bars, len(bars) - 1


# --------------------------------------------------------------------------- render

def render(bars: pd.DataFrame, i: int, symbol: str, minutes: int, window: int = WINDOW) -> bytes:
    """PNG of the ``window`` bars ending at bar ``i``; indicators use bars <= i only."""
    past = bars.iloc[: i + 1]
    c = past.close
    lo = max(0, len(past) - window)
    w = past.iloc[lo:]
    x = np.arange(len(w))
    o, h, l, cl, v = (w[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))
    fig = plt.figure(figsize=(14.4, 8.0), dpi=100)
    grid = fig.add_gridspec(4, 1, hspace=0.05, left=0.05, right=0.985, top=0.94, bottom=0.05)
    ax, axv = fig.add_subplot(grid[:3, 0]), fig.add_subplot(grid[3, 0])
    col = np.where(cl >= o, UP, DOWN)
    ax.vlines(x, l, h, color=col, linewidth=0.8)
    ax.bar(x, np.maximum(np.abs(cl - o), 1e-12), bottom=np.minimum(o, cl), color=col, width=0.7)
    axv.bar(x, np.nan_to_num(v), color=col, width=0.7)
    levels = [l, h]
    for n in sb.MA_PERIODS:
        sma, ema = c.rolling(n).mean().iloc[lo:], c.ewm(span=n, adjust=False).mean().iloc[lo:]
        ax.plot(x, sma, color=MA_COLOR[n], linewidth=1.1, label=f"SMA{n}")
        ax.plot(x, ema, color=MA_COLOR[n], linewidth=1.1, linestyle="--", label=f"EMA{n}")
        levels += [sma.to_numpy(float), ema.to_numpy(float)]
    basis, sd = c.rolling(sb.BB_LEN).mean().iloc[lo:], c.rolling(sb.BB_LEN).std(ddof=0).iloc[lo:]
    upper, lower = basis + sb.BB_MULT * sd, basis - sb.BB_MULT * sd
    ax.plot(x, upper, color="#787b86", linewidth=1.0, label="BB200 ±2σ")
    ax.plot(x, lower, color="#787b86", linewidth=1.0)
    ax.fill_between(x, lower, upper, color="#787b86", alpha=0.06)
    ymin, ymax = np.nanmin(np.concatenate(levels)), np.nanmax(np.concatenate(levels))
    pad = 0.05 * (ymax - ymin)
    ax.set_ylim(ymin - pad, ymax + pad)
    stamps = pd.to_datetime(w.index.to_numpy(), unit="ms", utc=True).tz_convert("Asia/Shanghai")
    ticks = list(range(len(w) - 1, -1, -20))[::-1]
    fmt = "%m-%d %H:%M" if minutes < 240 else "%m-%d"
    axv.set_xticks(ticks, [stamps[t].strftime(fmt) for t in ticks], fontsize=8)
    ax.set_xticks(ticks, [])
    for a in (ax, axv):
        a.set_xlim(-1, len(w) + 1)
        a.grid(alpha=0.15)
        a.tick_params(axis="y", labelsize=8)
    axv.set_yticks([])
    axv.set_ylabel("成交量", fontsize=9)
    ax.legend(loc="upper left", fontsize=8, ncol=7, frameon=False)
    ax.set_title(f"{symbol}  {TF_NAME[minutes]}  币安永续  最右一根收盘于 {stamps[-1] + pd.Timedelta(minutes=minutes):%Y-%m-%d %H:%M}（北京时间）",
                 fontsize=11, loc="left")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, metadata={"Software": None})
    plt.close(fig)
    return buf.getvalue()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- loose net

def loose_events(f: pd.DataFrame, allowed: np.ndarray) -> list[tuple[int, int]]:
    """Cooldown-filtered (bar, side) hits of the always-on candle plus BB compression and volume."""
    hits = []
    for s in (1, -1):
        base, conds = sb.conditions(f, s)
        m = base.to_numpy() & allowed
        for name in LOOSE:
            m &= conds[name].to_numpy()
        hits += [(int(i), s) for i in np.flatnonzero(m)]
    return sb.with_cooldown(hits)


def scan_symbol(args: tuple) -> pd.DataFrame:
    symbol, months, cfg = args
    raw = v1.read_5m(symbol, pd.Timestamp(cfg["warmup_start"]), pd.Timestamp(cfg["end"]))
    if raw.empty:
        return pd.DataFrame()
    start_ms = pd.Timestamp(cfg["start"]).value // 10**6
    out = []
    for minutes in cfg["timeframes"]:
        bars = sb.full_bars(raw, minutes)
        if len(bars) < sb.READY + 50:
            continue
        f = sb.features(bars)
        index = bars.index.to_numpy()
        month = pd.to_datetime(index, unit="ms").to_period("M").astype(str).to_numpy()
        n = len(f)
        allowed = np.isin(month, list(months)) & (index >= start_ms)
        allowed[n - 1:] = False
        events = loose_events(f, allowed)
        if not events:
            continue
        ei, sd = np.array([e for e, _ in events]), np.array([s for _, s in events])
        o, h, l, c = (f[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        atr = f.atr.to_numpy(float)
        atr_pct = atr / c
        stop = np.where(sd > 0, f.rope_lo.to_numpy(float)[ei] - sb.STOP_BUFFER * atr[ei],
                        f.rope_hi.to_numpy(float)[ei] + sb.STOP_BUFFER * atr[ei])
        risk_atr = sd * (o[ei + 1] - stop) / atr[ei]
        ready = ((f.seg >= sb.READY) & (f.atr > 0) & (f.frozen_share <= sb.MAX_FROZEN)).to_numpy() & (index >= start_ms)
        ready[n - 1:] = False
        pools = {mo: np.flatnonzero(ready & (month == mo)) for mo in set(months)}
        rng = np.random.default_rng(cfg["sample_seed"] + zlib.crc32(f"{symbol}|{minutes}".encode()))
        ctrl = np.full((len(ei), 20), -1)
        for k, e in enumerate(ei):
            cand = sb.control_candidates(pools.get(month[e], np.empty(0, int)), atr_pct, e)
            if len(cand):
                ctrl[k] = rng.choice(cand, size=20)
        flat, ok = ctrl.ravel(), ctrl.ravel() >= 0
        cu, cside = flat[ok], np.repeat(sd, 20)[ok]
        cstop = o[cu + 1] - cside * np.repeat(risk_atr, 20)[ok] * atr[cu]
        conds = {s: sb.conditions(f, s)[1] for s in (1, -1)}
        row = {"symbol": symbol, "minutes": minutes, "bar_open_ms": index[ei],
               "time": pd.to_datetime(index[ei], unit="ms", utc=True).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "month": month[ei], "side": sd, "stop": stop, "risk_atr": risk_atr,
               "past_width": f.past_width.to_numpy()[ei], "past_flips": f.past_flips.to_numpy()[ei], "rv": f.rv.to_numpy()[ei],
               "body_atr": np.abs(c[ei] - o[ei]) / f.prev_atr.to_numpy()[ei]}
        for name in ("dense", "big_body", "engulf"):
            row[f"rule_{name}"] = np.array([bool(conds[s][name].iat[e]) for e, s in zip(ei, sd)])
        for target in (3, 5):
            got = sb.simulate_many(o, h, l, c, ei + 1, sd, stop, target, sb.MAX_HOLD, cfg["round_trip_cost"])
            cg = sb.simulate_many(o, h, l, c, cu + 1, cside, cstop, target, sb.MAX_HOLD, cfg["round_trip_cost"])
            cr = np.full(len(flat), np.nan)
            cr[ok] = cg["net_r"]
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                row[f"control_net_r_{target}r"] = np.nanmean(cr.reshape(len(ei), 20), axis=1)
            row[f"net_r_{target}r"] = got["net_r"]
            row[f"exit_{target}r"] = got["kind"]
        row["risk_frac"] = got["risk_frac"]
        out.append(pd.DataFrame(row))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def scan(cfg: dict, workers: int = 8) -> pd.DataFrame:
    symbols = sorted(p.name.removesuffix(".csv.gz") for p in v1.SERIES.glob("*.csv.gz"))
    universe = v1.monthly_universe(symbols, pd.Timestamp(cfg["start"]), pd.Timestamp(cfg["end"]), 100)
    by_symbol: dict[str, list[str]] = {}
    for month, names in universe.items():
        for s in list(names) + list(sb.LEADERS):
            by_symbol.setdefault(s, []).append(month)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        parts = list(pool.map(scan_symbol, [(s, m, cfg) for s, m in sorted(by_symbol.items())], chunksize=1))
    frame = pd.concat([p for p in parts if len(p)], ignore_index=True)
    frame.insert(0, "item_id", frame.symbol + "|" + frame.minutes.astype(str) + "|" + frame.time + "|" + frame.side.astype(str))
    return frame


# --------------------------------------------------------------------------- judge

def ledger_key(image_sha: str, ref_shas: list[str], model: str, prompt_version: str, effort: str = "max") -> str:
    payload = json.dumps([image_sha, ref_shas, CRITERIA_VERSION, sha256(CRITERIA.encode()), model, prompt_version]
                         + ([] if effort == "max" else [effort]))  # max keeps the pilot rows' keys
    return sha256(payload.encode())


def read_ledger(path: Path = LEDGER) -> dict[str, dict]:
    if not path.exists():
        return {}
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return {r["key"]: r for r in rows}


def judge(items: list[dict], refs: list[dict], *, model: str, client_factory, workers: int = 3,
          ledger: Path = LEDGER, effort: str = "max") -> list[dict]:
    """Judge items ({item_id, set, path}) with reference renders ({item_id, path}); never re-bills a key."""
    from yoyo.vision_research.images import image_from_bytes
    from yoyo.vision_research.zhipu import PROMPT_VERSION, ZhipuError

    done = read_ledger(ledger)
    ref_images = [image_from_bytes(Path(r["path"]).read_bytes(), f"owner-reference-{k + 1}.png") for k, r in enumerate(refs)]
    ref_shas = [img.sha256 for img in ref_images]
    lock = threading.Lock()
    todo = []
    for item in items:
        data = Path(item["path"]).read_bytes()
        key = ledger_key(sha256(data), ref_shas, model, PROMPT_VERSION, effort)
        if key not in done and not any(t[0] == key for t in todo):
            todo.append((key, item, data))
    local = threading.local()

    def one(task):
        key, item, data = task
        if not hasattr(local, "client"):
            local.client = client_factory()
        row = {"key": key, "item_id": item["item_id"], "set": item["set"], "image_sha256": sha256(data),
               "reference_ids": [r["item_id"] for r in refs], "reference_sha256": ref_shas, "model": model,
               "prompt_version": PROMPT_VERSION, "criteria_version": CRITERIA_VERSION, "reasoning_effort": effort,
               "requested_at": pd.Timestamp.now(tz="UTC").isoformat()}
        try:
            got = local.client.analyze(image_from_bytes(data, "candidate.png"), ref_images, CRITERIA,
                                       {"time_boundary": "closed_bar_snapshot", "last_bar_closed": True,
                                        "note": "图中最右一根是已收盘的信号K线；图里没有其后的任何K线"})
            decision = got["decision"]
            decision = decision.model_dump() if hasattr(decision, "model_dump") else dict(decision)
            row.update(status="ok", verdict=decision.get("verdict"), side=decision.get("side"),
                       current_state=decision.get("current_state"), summary=decision.get("summary"),
                       evidence=decision.get("evidence"), risks=decision.get("risks"), usage=got.get("usage"),
                       latency_ms=got.get("latency_ms"), response_id=got.get("response_id"), response_model=got.get("model"))
        except ZhipuError as exc:
            row.update(status="error", error_code=exc.code, error=str(exc))
        with lock:
            with ledger.open("a") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, todo))


def zhipu_factory(model: str, effort: str = "max"):
    """Workbench Zhipu client; ``effort`` other than max overrides only reasoning_effort."""
    from yoyo.vision_research.settings import LocalSettings
    from yoyo.vision_research.zhipu import ZhipuClient

    class EffortClient(ZhipuClient):
        def _thinking_options(self):
            options = super()._thinking_options()
            return {**options, "reasoning_effort": effort} if options and effort != "max" else options

    settings = LocalSettings(VISION_RUNTIME).load()
    key = settings.get("api_key") if settings.get("provider") == "zhipu" else None
    if not key:
        for profile in (settings.get("profiles") or {}).values():
            if profile.get("provider") == "zhipu" and profile.get("api_key"):
                key = profile["api_key"]
    if not key:
        raise ValueError("no Zhipu API key in the vision workbench settings")
    return lambda: EffortClient(key, model)


# --------------------------------------------------------------------------- CLI

def load_examples() -> list[dict]:
    return json.loads(EXAMPLES.read_text()) if EXAMPLES.exists() else []


def example_signal_ms(ex: dict) -> int:
    """Owner times are Beijing-time bar opens of the breakout candle."""
    return pd.Timestamp(ex["signal_open_bj"], tz="Asia/Shanghai").tz_convert("UTC").value // 10**6


def render_set(which: str, cfg: dict, n: int, query: str | None = None, name: str | None = None) -> pd.DataFrame:
    """Render owner examples, or a seeded random sample of candidates (optionally a ``query`` subset)."""
    rows = []
    name = name or which
    if which == "examples":
        for ex in load_examples():
            bars, i = bars_until(ex["symbol"], int(ex["minutes"]), example_signal_ms(ex))
            png = render(bars, i, ex["symbol"], int(ex["minutes"]))
            path = IMAGES / "examples" / f"{ex['id']}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(png)
            rows.append({"item_id": ex["id"], "set": "examples", "path": str(path), "sha256": sha256(png),
                         "owner_label": ex["owner_label"], "role": ex.get("role", "test")})
    else:
        cands = pd.read_csv(EXP / "candidates.csv.gz")
        if query:
            cands = cands.query(query).reset_index(drop=True)
        rng = np.random.default_rng(cfg["sample_seed"] + zlib.crc32(name.encode()) % 1000 * (name != "candidates"))
        pick = cands.iloc[np.sort(rng.choice(len(cands), size=min(n, len(cands)), replace=False))]
        for _, ev in pick.iterrows():
            bars, i = bars_until(ev.symbol, int(ev.minutes), int(ev.bar_open_ms))
            png = render(bars, i, ev.symbol, int(ev.minutes))
            path = IMAGES / "candidates" / f"{sha256(ev.item_id.encode())[:16]}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(png)
            rows.append({"item_id": ev.item_id, "set": name, "path": str(path), "sha256": sha256(png)})
    manifest = pd.DataFrame(rows)
    if query:
        manifest["query"] = query
    manifest.to_csv(EXP / f"manifest_{name}.csv", index=False)
    return manifest


def review_sheets(name: str, picks: dict[str, int], effort: str, seed: int, per_sheet: int = 6) -> pd.DataFrame:
    """Blind numbered contact sheets for the owner from a judged manifest.

    ``picks`` maps a VLM verdict group ("match" / "other") to how many to draw; the groups are
    shuffled together so the sheet order says nothing about the verdict. The key with verdicts
    and outcomes is written next to the sheets and is not shown to the owner before labelling.
    """
    from PIL import Image, ImageDraw, ImageFont

    manifest = pd.read_csv(EXP / f"manifest_{name}.csv")
    rows = [r for r in read_ledger().values()
            if r["set"] == name and r["status"] == "ok" and r.get("reasoning_effort", "max") == effort]
    judged = pd.DataFrame(rows).drop_duplicates("item_id").merge(manifest[["item_id", "path"]], on="item_id")
    judged["group"] = np.where(judged.verdict.eq("match"), "match", "other")
    rng = np.random.default_rng(seed)
    chosen = pd.concat([g.iloc[rng.permutation(len(g))[: picks.get(k, 0)]] for k, g in judged.groupby("group")])
    chosen = chosen.iloc[rng.permutation(len(chosen))].reset_index(drop=True)
    chosen.insert(0, "number", np.arange(1, len(chosen) + 1))
    cands = pd.read_csv(EXP / "candidates.csv.gz")
    key = chosen.merge(cands, on="item_id", how="left", suffixes=("", "_cand"))
    out = EXP / "review" / name
    out.mkdir(parents=True, exist_ok=True)
    font = ImageFont.truetype("/System/Library/Fonts/Hiragino Sans GB.ttc", 44)
    for start in range(0, len(chosen), per_sheet):
        part = chosen.iloc[start: start + per_sheet]
        sheet = Image.new("RGB", (1440, 400 * ((len(part) + 1) // 2)), "white")
        draw = ImageDraw.Draw(sheet)
        for k, (_, row) in enumerate(part.iterrows()):
            img = Image.open(row.path).convert("RGB").resize((720, 400))
            x, y = 720 * (k % 2), 400 * (k // 2)
            sheet.paste(img, (x, y))
            draw.rectangle([x + 6, y + 332, x + 106, y + 392], fill="#111111")  # bottom-left keeps the title readable
            draw.text((x + 14, y + 334), f"#{row.number}", fill="white", font=font)
            draw.rectangle([x, y, x + 719, y + 399], outline="#999999", width=2)
        sheet.save(out / f"sheet_{start // per_sheet + 1}.png")
    key.to_csv(out / "key_hidden.csv", index=False)
    return key


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan").add_argument("--workers", type=int, default=8)
    r = sub.add_parser("render")
    r.add_argument("which", choices=["candidates", "examples"])
    r.add_argument("--n", type=int, default=40)
    r.add_argument("--query", help="pandas query on candidates.csv.gz before sampling")
    r.add_argument("--name", help="manifest / set name (default: which)")
    j = sub.add_parser("judge")
    j.add_argument("which", help="manifest name: candidates, examples or a render --name")
    rs = sub.add_parser("sheets")
    rs.add_argument("name")
    rs.add_argument("--match", type=int, default=15)
    rs.add_argument("--other", type=int, default=9)
    rs.add_argument("--effort", default="low")
    rs.add_argument("--seed", type=int, default=1008702)
    j.add_argument("--limit", type=int, required=True)
    j.add_argument("--no-references", action="store_true")
    j.add_argument("--effort", default="max")
    j.add_argument("--ids", nargs="*", help="judge only these item ids")
    args = parser.parse_args()
    cfg = json.loads(CONFIG.read_text())
    if args.cmd == "scan":
        sources = (Path(__file__), CONFIG, Path("tests/evaluation/test_squeeze_vlm.py"), Path(sb.__file__), Path(v1.__file__))
        if not v1._committed(sources):
            raise ValueError("commit builder, tests and config before generating results")
        frame = scan(cfg, args.workers)
        frame.to_csv(EXP / "candidates.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
        head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        (EXP / "candidates_receipt.json").write_text(json.dumps({
            "source_commit": head, "candidates": len(frame), "symbols": int(frame.symbol.nunique()),
            "by_minutes": frame.minutes.value_counts().to_dict(), "generated_at": pd.Timestamp.now(tz="UTC").isoformat()}, indent=1))
        print(len(frame))
    elif args.cmd == "render":
        print(render_set(args.which, cfg, args.n, args.query, args.name).to_string(index=False))
    elif args.cmd == "sheets":
        key = review_sheets(args.name, {"match": args.match, "other": args.other}, args.effort, args.seed)
        print(key[["number", "item_id", "verdict"]].to_string(index=False))
    else:
        manifest = pd.read_csv(EXP / f"manifest_{args.which}.csv")
        refs = []
        if not args.no_references:
            ex = pd.read_csv(EXP / "manifest_examples.csv")
            refs = ex.loc[ex.role.eq("reference")].to_dict("records")
        pool = manifest.loc[~manifest.item_id.isin([r["item_id"] for r in refs])]
        if args.ids:
            pool = pool.loc[pool.item_id.isin(args.ids)]
        items = pool.head(args.limit).to_dict("records")
        rows = judge(items, refs, model=cfg["vlm"]["model"], client_factory=zhipu_factory(cfg["vlm"]["model"], args.effort),
                     workers=cfg["vlm"]["max_workers"], effort=args.effort)
        for row in rows:
            print(row["item_id"], row["status"], row.get("verdict"), row.get("current_state"), (row.get("summary") or row.get("error", ""))[:120])


if __name__ == "__main__":
    main()

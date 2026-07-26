"""Generate analysis/INDEX.md -- a one-line-per-report index of analysis/*.md.

Why (2026-07-26): docs/DOC_MAP.md maps document CATEGORIES but never indexes the
individual reports, so `analysis/` has grown to 130+ files with no way to see
what has already been answered. That gap has real cost: in one session an agent
nearly re-ran the owner's 2525-box long/short labelling (already done and
written up in p_owner_side_feature_verdict.md), and two parallel sessions each
built their own IT-14 visual-direction pre-check.

Extraction is deliberately DUMB and quotable, never a summary in my own words:
  title   = the file's first `# ` heading, verbatim
  date    = first YYYY-MM-DD found in the first 5 lines (usually in the H1)
  verdict = the first line under a 结论/裁决/verdict heading, or the first line
            starting with 结论/裁决/判决, truncated -- QUOTED, not paraphrased
A blank verdict means "not machine-extractable", not "no conclusion". Read the
file. Auto-summarising 130 reports would risk mislabelling a failed experiment
as a success, which is exactly the kind of contamination CLAUDE.md forbids.

Re-run after adding reports so the index does not go stale:
  PYTHONPATH=. .venv/bin/python scripts/gen_analysis_index.py
"""
from __future__ import annotations

import re
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
ANALYSIS = PROJECT / "analysis"
OUT = ANALYSIS / "INDEX.md"

DATE_RE = re.compile(r"(20\d{2}[-/]\d{2}[-/]\d{2})")
VERDICT_HEAD = re.compile(r"^#+\s*.*(结论|裁决|判决|verdict|conclusion)", re.I)
VERDICT_INLINE = re.compile(r"^\**\s*(结论|裁决|判决|回答)\W", re.I)


def extract(path: Path) -> dict:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    title = ""
    for ln in lines[:15]:
        if ln.startswith("# "):
            title = ln[2:].strip()
            break
    date = ""
    for ln in lines[:5]:
        m = DATE_RE.search(ln)
        if m:
            date = m.group(1).replace("/", "-")
            break
    verdict = ""
    for i, ln in enumerate(lines):
        if VERDICT_HEAD.match(ln):
            for nxt in lines[i + 1: i + 8]:
                s = nxt.strip().lstrip("*_->| ").strip()
                if s and not s.startswith(("#", "|--", "---", "|")):
                    verdict = s
                    break
            if verdict:
                break
        if not verdict and VERDICT_INLINE.match(ln.strip()):
            verdict = ln.strip()
            break
    verdict = re.sub(r"\s+", " ", verdict).strip("*_ ")
    if len(verdict) > 150:
        verdict = verdict[:147] + "..."
    return {"title": title or path.stem, "date": date, "verdict": verdict}


def main() -> int:
    files = sorted(
        (p for p in ANALYSIS.glob("*.md") if p.name != "INDEX.md"),
        key=lambda p: p.name,
    )
    rows = []
    for p in files:
        info = extract(p)
        if info:
            rows.append((p.name, info))

    dated = sorted(rows, key=lambda r: (r[1]["date"] or "0000-00-00"), reverse=True)

    out = [
        "# analysis/ 报告索引（自动生成,勿手改）",
        "",
        f"共 **{len(rows)}** 篇。重跑刷新:`PYTHONPATH=. .venv/bin/python scripts/gen_analysis_index.py`",
        "",
        "> **动手前先在这里搜一遍**——这个索引存在的原因是:曾经差点重跑 owner 已标完的 2525 个",
        "> 多空框(`p_owner_side_feature_verdict.md` 早有结论),也曾两个会话各自做了一遍同样的",
        "> 视觉方向预检。**结论列是原文摘录,不是我的转述;空 = 机器提不出,不是没结论——去读原文。**",
        "",
        "## 按日期倒序",
        "",
        "| 日期 | 报告 | 标题 | 结论(原文摘录) |",
        "|---|---|---|---|",
    ]
    for name, info in dated:
        t = info["title"].replace("|", "\\|")
        v = info["verdict"].replace("|", "\\|")
        out.append(f"| {info['date'] or '—'} | [`{name}`]({name}) | {t} | {v} |")

    out += ["", "## 按文件名(便于 grep)", ""]
    for name, info in rows:
        out.append(f"- [`{name}`]({name}) — {info['title']}")
    out.append("")

    OUT.write_text("\n".join(out), encoding="utf-8")
    n_v = sum(1 for _, i in rows if i["verdict"])
    print(f"wrote {OUT.relative_to(PROJECT)}: {len(rows)} reports, "
          f"{n_v} with machine-extractable verdict ({n_v*100//max(len(rows),1)}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

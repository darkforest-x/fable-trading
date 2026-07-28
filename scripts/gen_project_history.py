"""Extract the project's history from its artefacts, not from anyone's memory.

The owner asked for a full account of 21 days, 411 commits, 131 analysis reports
and 197 result files. Writing that from recall would get it wrong -- this session
alone produced two confident misdiagnoses of the detection lag and a "8.5x" that
was really 23bp -- so the appendices are generated from what the repo actually
records, and only the narrative is written by hand on top of them.

Four appendices, each from a different source of truth:

  A  TIMELINE      every commit, grouped by day and by conventional-commit type,
                   so the sequence of what was tried is the git log's account
                   rather than a reconstruction.
  B  RESULTS       every analysis/output/*.json that carries a verdict or a
                   metric, flattened into one table. These are the numbers the
                   scripts printed at the time, not numbers re-derived later.
  C  LEARNINGS     the docs/learnings notes, with their problem statement and
                   general rule pulled out of each.
  D  PROVENANCE    which script produced which result file, so any number in the
                   narrative can be traced to the command that made it.

Read-only. Writes markdown under analysis/history/.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/gen_project_history.py
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
OUT_DIR = PROJECT / "analysis" / "history"

TYPE_ZH = {
    "feat": "新功能", "fix": "修复", "measure": "测量", "diag": "诊断",
    "build": "构建", "charts": "图表", "holdout": "holdout 验收",
    "prereg": "预注册", "docs": "文档", "refactor": "重构", "test": "测试",
    "chore": "杂项", "data": "数据", "exp": "实验", "report": "报告",
    "live": "实盘", "deploy": "部署", "train": "训练", "perf": "性能",
}


def sh(*args: str) -> str:
    return subprocess.run(args, cwd=PROJECT, capture_output=True,
                          text=True).stdout


def appendix_a() -> str:
    log = sh("git", "log", "--reverse", "--date=short",
             "--format=%H\t%ad\t%s")
    by_day: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    by_type: dict[str, int] = defaultdict(int)
    for line in log.strip().splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        h, day, subj = parts
        m = re.match(r"^(\w+)(\([^)]*\))?:\s*(.*)$", subj)
        typ = m.group(1) if m else "other"
        by_type[typ] += 1
        by_day[day].append((h[:7], typ, m.group(3) if m else subj))

    out = ["# 附录 A:提交时间线", "",
           f"共 {sum(len(v) for v in by_day.values())} 次提交,"
           f"{len(by_day)} 个活动日。由 `git log` 生成,非回忆。", "",
           "## 按类型", "", "| 类型 | 次数 |", "|---|---|"]
    for t, n in sorted(by_type.items(), key=lambda kv: -kv[1]):
        out.append(f"| {t} ({TYPE_ZH.get(t, '')}) | {n} |")
    out += ["", "## 按日", ""]
    for day in sorted(by_day):
        rows = by_day[day]
        out.append(f"### {day} — {len(rows)} 次提交")
        out.append("")
        for h, typ, subj in rows:
            out.append(f"- `{h}` **{typ}** {subj}")
        out.append("")
    return "\n".join(out)


def _verdict_of(obj) -> str | None:
    if isinstance(obj, dict):
        for k in ("verdict", "判读", "conclusion", "summary"):
            v = obj.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def _metrics_of(obj, prefix="", depth=0) -> list[tuple[str, str]]:
    """Scalar leaves worth showing, shallow so tables stay readable."""
    out = []
    if depth > 2 or not isinstance(obj, dict):
        return out
    for k, v in obj.items():
        key = f"{prefix}{k}"
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out.append((key, f"{v:.6g}"))
        elif isinstance(v, str) and len(v) < 40 and k != "verdict":
            out.append((key, v))
        elif isinstance(v, dict):
            out.extend(_metrics_of(v, key + ".", depth + 1))
    return out


def appendix_b() -> str:
    files = sorted((PROJECT / "analysis" / "output").glob("*.json"))
    out = ["# 附录 B:实验结果汇总", "",
           f"从 `analysis/output/` 的 {len(files)} 个 JSON 抽取。"
           "每条都是脚本当时打印的结果,不是事后重算。", ""]
    n_verdict = 0
    for f in files:
        try:
            obj = json.loads(f.read_text())
        except Exception:  # noqa: BLE001
            continue
        v = _verdict_of(obj)
        mets = _metrics_of(obj)[:14]
        if not v and not mets:
            continue
        out.append(f"## `{f.name}`")
        out.append("")
        if v:
            n_verdict += 1
            out.append(f"> **判读**:{v}")
            out.append("")
        if mets:
            out.append("| 指标 | 值 |")
            out.append("|---|---|")
            for k, val in mets:
                out.append(f"| {k} | {val} |")
            out.append("")
    out.insert(3, f"其中 {n_verdict} 个带明确判读。\n")
    return "\n".join(out)


def appendix_c() -> str:
    notes = sorted((PROJECT / "docs" / "learnings").glob("*.md"))
    out = ["# 附录 C:经验笔记", "",
           f"`docs/learnings/` 共 {len(notes)} 篇。每篇提取标题、问题、通用规则。", ""]
    for f in notes:
        text = f.read_text()
        title = next((l.lstrip("# ").strip() for l in text.splitlines()
                      if l.startswith("#")), f.stem)
        def grab(label: str) -> str:
            m = re.search(rf"\*\*{label}\*\*[::]\s*(.+?)(?=\n- \*\*|\Z)",
                          text, re.S)
            return " ".join(m.group(1).split())[:300] if m else ""
        prob = grab("问题") or grab("Problem")
        rule = grab("通用规则") or grab("General rule")
        out.append(f"### {title}")
        out.append("")
        out.append(f"`{f.name}`")
        out.append("")
        if prob:
            out.append(f"- **问题**:{prob}")
        if rule:
            out.append(f"- **规则**:{rule}")
        out.append("")
    return "\n".join(out)


def appendix_d() -> str:
    """Which script writes which result file -- the trace for every number."""
    pairs = []
    for s in sorted((PROJECT / "scripts").glob("*.py")):
        try:
            text = s.read_text()
        except Exception:  # noqa: BLE001
            continue
        for m in re.finditer(r'["\']([\w./-]+\.(?:json|csv))["\']', text):
            name = m.group(1)
            if "output" in text[max(0, m.start() - 120):m.start()] or \
               (PROJECT / "analysis" / "output" / Path(name).name).exists():
                pairs.append((Path(name).name, s.name))
    seen = set()
    rows = []
    for out_name, script in pairs:
        if (out_name, script) in seen:
            continue
        seen.add((out_name, script))
        rows.append((out_name, script))
    rows.sort()
    out = ["# 附录 D:数字出处索引", "",
           "报告里每个数字都能查到是哪个脚本产的。", "",
           "| 结果文件 | 产生它的脚本 |", "|---|---|"]
    for o, s in rows:
        out.append(f"| `{o}` | `scripts/{s}` |")
    return "\n".join(out)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, fn in (("APPENDIX_A_timeline.md", appendix_a),
                     ("APPENDIX_B_results.md", appendix_b),
                     ("APPENDIX_C_learnings.md", appendix_c),
                     ("APPENDIX_D_provenance.md", appendix_d)):
        text = fn()
        (OUT_DIR / name).write_text(text + "\n")
        print(f"{name:<32} {len(text.splitlines()):>6} 行  "
              f"{len(text)/1024:>7.1f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

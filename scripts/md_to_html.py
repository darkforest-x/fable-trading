"""Render the project's markdown reports as self-contained HTML.

The reports went to Telegram as .md and came back garbled. Two causes, both here:
mimetypes has no entry for .md, so the file was sent as application/octet-stream
with no charset, and Telegram's preview then decoded UTF-8 Chinese with whatever
default it picked. HTML fixes it structurally -- <meta charset="utf-8"> is part
of the document, so no viewer has to guess -- and tables render as tables instead
of pipe characters.

The converter is deliberately small and covers exactly what these reports use:
ATX headings, pipe tables, fenced code, ordered/unordered lists, blockquotes,
horizontal rules, and inline bold/code/links. Wrapped paragraph and list-item
lines are joined before rendering so source line wrapping does not create fake
HTML paragraphs. No new dependency (CLAUDE.md: no heavy deps),
and nothing here needs to be a general markdown implementation -- it needs to be
correct on the documents in analysis/.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/md_to_html.py analysis/*.md
  PYTHONPATH=. .venv/bin/python scripts/md_to_html.py --out-dir analysis/html FILE...
"""
from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
BULLET = re.compile(r"^\s*[-*+]\s+")
ORDERED = re.compile(r"^\s*\d+\.\s+")

CSS = """
:root { color-scheme: light dark; }
body { max-width: 62rem; margin: 0 auto; padding: 2rem 1.2rem 5rem;
       font: 16px/1.75 -apple-system, "PingFang SC", "Hiragino Sans GB",
             "Microsoft YaHei", system-ui, sans-serif;
       color: #1a1a1a; background: #fff; word-wrap: break-word; }
h1 { font-size: 1.9rem; border-bottom: 3px solid #1976d2; padding-bottom: .4rem;
     margin: 2.4rem 0 1rem; }
h2 { font-size: 1.45rem; border-bottom: 1px solid #d0d7de; padding-bottom: .3rem;
     margin: 2rem 0 .8rem; }
h3 { font-size: 1.15rem; margin: 1.5rem 0 .6rem; }
h4 { font-size: 1rem; margin: 1.2rem 0 .5rem; color: #444; }
table { border-collapse: collapse; margin: 1rem 0; font-size: .92rem;
        display: block; overflow-x: auto; max-width: 100%; }
th, td { border: 1px solid #d0d7de; padding: .45rem .7rem; text-align: left;
         white-space: nowrap; }
th { background: #f2f5f8; font-weight: 600; }
tr:nth-child(even) td { background: #fafbfc; }
code { background: #f2f5f8; padding: .12em .35em; border-radius: 4px;
       font: .88em/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; }
pre { background: #f6f8fa; padding: .9rem 1.1rem; border-radius: 8px;
      overflow-x: auto; border: 1px solid #e3e8ee; }
pre code { background: none; padding: 0; font-size: .85rem; }
blockquote { margin: 1rem 0; padding: .6rem 1rem; border-left: 4px solid #1976d2;
             background: #f5f9ff; color: #2c3e50; }
hr { border: 0; border-top: 1px solid #d0d7de; margin: 2.5rem 0; }
a { color: #1976d2; }
ul, ol { padding-left: 1.5rem; }
li { margin: .25rem 0; }
@media (prefers-color-scheme: dark) {
  body { color: #e6e6e6; background: #14171a; }
  h2, th, td, hr, pre { border-color: #30363d; }
  th { background: #1c2128; } tr:nth-child(even) td { background: #191d21; }
  code, pre { background: #1c2128; } h4 { color: #b8c0c8; }
  blockquote { background: #172029; color: #cbd5e0; }
}
"""


def inline(text: str) -> str:
    """Escape, then re-introduce the inline markup we actually use."""
    out = html.escape(text)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<![*\w])\*([^*\n]+)\*(?![*\w])", r"<em>\1</em>", out)
    out = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', out)
    return out


def convert(md: str) -> str:
    lines = md.splitlines()
    out: list[str] = []
    i, n = 0, len(lines)
    in_code = False
    list_tag: str | None = None

    def close_list() -> None:
        nonlocal list_tag
        if list_tag:
            out.append(f"</{list_tag}>")
            list_tag = None

    def block_start(index: int) -> bool:
        """Return whether a line starts a new Markdown block."""
        candidate = lines[index]
        stripped = candidate.strip()
        if not stripped:
            return True
        if candidate.startswith("```") or candidate.startswith(">"):
            return True
        if re.match(r"^#{1,6}\s+", candidate):
            return True
        if BULLET.match(candidate) or ORDERED.match(candidate):
            return True
        if re.match(r"^\s*(---+|___+|\*\*\*+)\s*$", candidate):
            return True
        return stripped.startswith("|")

    while i < n:
        line = lines[i]

        if line.startswith("```"):
            if in_code:
                out.append("</code></pre>")
                in_code = False
            else:
                close_list()
                out.append("<pre><code>")
                in_code = True
            i += 1
            continue
        if in_code:
            out.append(html.escape(line))
            i += 1
            continue

        # pipe table: header row, separator row, then body until a non-pipe line
        if (line.strip().startswith("|") and i + 1 < n
                and re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1])):
            close_list()
            def cells(row: str) -> list[str]:
                return [c.strip() for c in row.strip().strip("|").split("|")]
            out.append("<table><thead><tr>")
            out += [f"<th>{inline(c)}</th>" for c in cells(line)]
            out.append("</tr></thead><tbody>")
            i += 2
            while i < n and lines[i].strip().startswith("|"):
                out.append("<tr>" + "".join(f"<td>{inline(c)}</td>"
                                            for c in cells(lines[i])) + "</tr>")
                i += 1
            out.append("</tbody></table>")
            continue

        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            close_list()
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{inline(m.group(2))}</h{lvl}>")
            i += 1
            continue

        list_match = BULLET.match(line) or ORDERED.match(line)
        if list_match:
            wanted_tag = "ul" if BULLET.match(line) else "ol"
            if list_tag != wanted_tag:
                close_list()
                out.append(f"<{wanted_tag}>")
                list_tag = wanted_tag
            pattern = BULLET if wanted_tag == "ul" else ORDERED
            item_lines = [pattern.sub("", line).strip()]
            i += 1
            while (i < n and lines[i].strip()
                   and not block_start(i)
                   and lines[i][:1].isspace()):
                item_lines.append(lines[i].strip())
                i += 1
            out.append(f"<li>{inline(' '.join(item_lines))}</li>")
            continue

        if line.startswith(">"):
            close_list()
            buf = []
            while i < n and lines[i].startswith(">"):
                buf.append(lines[i].lstrip("> ").rstrip())
                i += 1
            out.append(f"<blockquote>{inline(' '.join(buf))}</blockquote>")
            continue

        if re.match(r"^\s*(---+|___+|\*\*\*+)\s*$", line):
            close_list()
            out.append("<hr>")
            i += 1
            continue

        if not line.strip():
            close_list()
            i += 1
            continue

        close_list()
        paragraph = [line.strip()]
        i += 1
        while i < n and not block_start(i):
            paragraph.append(lines[i].strip())
            i += 1
        out.append(f"<p>{inline(' '.join(paragraph))}</p>")

    if in_code:
        out.append("</code></pre>")
    close_list()
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--out-dir", default="analysis/html")
    args = ap.parse_args()

    out_dir = PROJECT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    made = []
    for f in args.files:
        p = Path(f)
        if not p.is_absolute():
            p = PROJECT / p
        if not p.exists():
            print(f"跳过(不存在): {p}")
            continue
        md = p.read_text(encoding="utf-8")
        title = next((l.lstrip("# ").strip() for l in md.splitlines()
                      if l.startswith("# ")), p.stem)
        doc = (f'<!doctype html>\n<html lang="zh-CN">\n<head>\n'
               f'<meta charset="utf-8">\n'
               f'<meta name="viewport" content="width=device-width,initial-scale=1">\n'
               f'<link rel="icon" href="data:,">\n'
               f'<title>{html.escape(title)}</title>\n<style>{CSS}</style>\n'
               f'</head>\n<body>\n{convert(md)}\n</body>\n</html>\n')
        dst = out_dir / (p.stem + ".html")
        dst.write_text(doc, encoding="utf-8")
        made.append(dst)
        print(f"{p.name:<40} -> {dst.relative_to(PROJECT)}  {len(doc)/1024:.0f} KB")
    return 0 if made else 1


if __name__ == "__main__":
    raise SystemExit(main())

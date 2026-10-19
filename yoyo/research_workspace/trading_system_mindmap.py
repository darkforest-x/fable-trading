"""Render the owner's Notion #57 trading-system map as a portable document.

Source: the versioned JSON records links to tasks 49/50/53/54 and Manual-v4.
This is a documentation renderer, not a market-data builder or trading policy.
It preserves source status, requires explicit versions, and never accesses an
account, changes eligibility, or supplies missing financial risk parameters.
The output uses native HTML details and inline accessible SVG; no JavaScript,
remote image assets, or rendering dependency is required.
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path


def esc(value):
    return html.escape(str(value), quote=True)


def lines(value, width=13):
    count = max(1, (len(value) + width - 1) // width)
    span = (len(value) + count - 1) // count
    return [value[i:i + span] for i in range(0, len(value), span)]


def label(value, x, y, size=16, width=13):
    rows = lines(value, width)
    top = y - (len(rows) - 1) * 12
    return ''.join(f'<text x="{x}" y="{top + i * 24}" text-anchor="middle" font-size="{size}" font-weight="600">{esc(row)}</text>' for i, row in enumerate(rows))


def svg_start(slug, title, description, width, height):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-labelledby="{slug}-title {slug}-desc">'
            f'<title id="{slug}-title">{esc(title)}</title><desc id="{slug}-desc">{esc(description)}</desc>'
            '<defs>' + ''.join(f'<marker id="{slug}-{name}" markerWidth="8" markerHeight="8" refX="8" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8 Z" fill="{color}"/></marker>' for name, color in [('arrow', '#4f5d75'), ('arrow-accent', '#eb6c36'), ('arrow-link', '#2e5aa8')]) + '</defs>')


def overview(data):
    out = svg_start('overview', data['title'], '五个阶段覆盖交易前准备、模型选择、风险执行、持仓管理与复盘迭代；下方目录连接十八个细分支。', 1040, 336)
    paths = ['M440 88 V104 Q440 112 432 112 H108 Q100 112 100 120 V192',
             'M480 88 V136 Q480 144 472 144 H308 Q300 144 300 152 V192',
             'M520 88 V192',
             'M560 88 V136 Q560 144 568 144 H732 Q740 144 740 152 V192',
             'M600 88 V104 Q600 112 608 112 H932 Q940 112 940 120 V192']
    out += '<g fill="none" stroke="#7a8399" stroke-width="1.2">' + ''.join(f'<path d="{p}"/>' for p in paths) + '</g>'
    out += '<rect x="400" y="16" width="240" height="72" rx="8" fill="#f6e9e2" stroke="#eb6c36"/>' + label(data['title'], 520, 56, 24)
    for domain, x in zip(data['domains'], [20, 220, 440, 660, 860]):
        out += f'<a href="#domain-{domain["id"]}"><rect x="{x}" y="192" width="160" height="64" rx="8" fill="#fff" stroke="#bfc0c0"/>' + label(domain['title'], x + 80, 228, 20) + '</a>'
    out += '<line x1="20" y1="288" x2="1020" y2="288" stroke="#bfc0c0"/><text x="20" y="316" font-size="16" fill="#4f5d75">连线表示包含关系；点击阶段进入目录。每条规则保留来源与状态。</text></svg>'
    return out


def mobile_overview(data):
    out = svg_start('overview-mobile', data['title'], '手机总览：准备、选打法、开单、管理、复盘五个阶段；点击阶段进入目录。', 360, 488)
    paths = ['M112 176 H120 Q128 176 128 168 V56 Q128 48 136 48 H208',
             'M112 208 H144 Q152 208 152 200 V152 Q152 144 160 144 H208',
             'M112 240 H208',
             'M112 272 H144 Q152 272 152 280 V328 Q152 336 160 336 H208',
             'M112 304 H120 Q128 304 128 312 V424 Q128 432 136 432 H208']
    out += '<g fill="none" stroke="#7a8399" stroke-width="1.2">' + ''.join(f'<path d="{p}"/>' for p in paths) + '</g>'
    out += '<rect x="8" y="144" width="104" height="192" rx="8" fill="#f6e9e2" stroke="#eb6c36"/>' + label(data['title'], 60, 240, 16, 4)
    for domain, y in zip(data['domains'], [16, 112, 208, 304, 400]):
        out += f'<a href="#domain-{domain["id"]}"><rect x="208" y="{y}" width="144" height="64" rx="8" fill="#fff" stroke="#bfc0c0"/>' + label(domain['title'], 280, y + 36, 16, 9) + '</a>'
    return out + '</svg>'


def branch(section):
    slug = section['id']
    out = svg_start(slug, section['title'], section['brief'] + '分为：' + '、'.join(g['title'] for g in section['groups']) + '。', 800, 448)
    paths = ['M256 156 H296 Q304 156 304 148 V56 Q304 48 312 48 H400',
             'M256 188 H320 Q328 188 328 180 V152 Q328 144 336 144 H400',
             'M256 220 H344 Q352 220 352 228 V232 Q352 240 360 240 H400',
             'M256 252 H368 Q376 252 376 260 V328 Q376 336 384 336 H400']
    out += '<g fill="none" stroke="#7a8399" stroke-width="1.2">' + ''.join(f'<path d="{p}"/>' for p in paths) + '</g>'
    out += '<rect x="16" y="128" width="240" height="152" rx="8" fill="#f6e9e2" stroke="#eb6c36"/>' + label(section['title'], 136, 208)
    for i, (group, y) in enumerate(zip(section['groups'], [16, 112, 208, 304])):
        out += f'<a href="#{slug}-g{i + 1}"><rect x="400" y="{y}" width="320" height="64" rx="8" fill="#fff" stroke="#bfc0c0"/>' + label(group['title'], 560, y + 36, width=18) + '</a>'
    out += '<line x1="16" y1="400" x2="784" y2="400" stroke="#bfc0c0"/><text x="16" y="428" font-size="16" fill="#4f5d75">点击节点看执行细节；每个分支下方均保留规则来源。</text></svg>'
    return out


CSS = """
:root{--paper:#f5f5f5;--ink:#2d3142;--muted:#4f5d75;--soft:#7a8399;--line:#bfc0c0;--accent:#eb6c36;--tint:#f6e9e2}
*{box-sizing:border-box}html{scroll-behavior:auto;scroll-padding-top:24px}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.8 'Geist','PingFang SC','Microsoft YaHei',sans-serif}a{color:#2e5aa8;text-underline-offset:4px}a:hover{color:#a3451b}a:focus-visible,summary:focus-visible{outline:3px solid var(--accent);outline-offset:4px}
main{max-width:1160px;margin:auto;padding:56px 32px}header{border-top:4px solid var(--accent);padding-top:24px}h1{font:400 clamp(32px,5vw,48px)/1.3 'Instrument Serif','Songti SC',serif;margin:12px 0 16px}h2{font-size:28px;line-height:1.4;margin:0 0 12px}h3{font-size:20px;line-height:1.5;margin:0 0 12px}p{margin:8px 0 16px}.eyebrow,.number{font-family:'Geist Mono',monospace;font-size:12px;letter-spacing:.12em}.muted{color:var(--muted)}.lead{font-size:20px;max-width:800px}.notice{border-left:4px solid var(--accent);padding:12px 20px;background:var(--tint);margin:24px 0}.meta{display:flex;gap:24px;flex-wrap:wrap;color:var(--muted);font-size:12px}.legend{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0 24px}.tag{display:inline-block;border:1px solid var(--line);border-radius:4px;padding:0 8px;font-size:12px;color:var(--muted);background:#fff}.overview{margin:32px 0 16px}.svg-scroll{overflow-x:auto;overscroll-behavior-x:contain}svg{display:block;width:100%;min-width:720px;color:var(--ink);font-family:'Geist','PingFang SC','Microsoft YaHei',sans-serif}svg text{fill:currentColor}.overview svg{min-width:1000px}.branch-map{max-width:800px;margin:16px auto 24px}
.directory{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:20px;margin:24px 0 48px}.directory section{border-top:2px solid var(--ink);padding-top:16px}.directory h3{font-size:20px}.directory p{font-size:12px;color:var(--muted)}.directory a{display:block;font-size:14px;margin:12px 0;text-decoration:none}.directory a span{font:12px 'Geist Mono',monospace;color:var(--soft);margin-right:8px}
.chapter{border-top:1px solid var(--line);padding:24px 0;scroll-margin-top:24px}.chapter>details>summary{list-style:none;cursor:pointer;display:flex;gap:16px;align-items:flex-start}.chapter>details>summary::-webkit-details-marker{display:none}.chapter>details>summary:after{content:'＋';font-size:24px;margin-left:auto}.chapter>details[open]>summary:after{content:'−'}.chapter h2{font-size:24px}.chapter .number{padding-top:4px;color:var(--accent)}.chapter .brief{color:var(--muted);margin:0}.topics{display:grid;grid-template-columns:1fr 1fr;gap:24px}.topic{background:#fff;border:1px solid #d9dce0;border-radius:8px;padding:24px;scroll-margin-top:24px}.topic h3{font-size:20px}.topic ul{list-style:none;margin:0;padding:0 0 0 16px;border-left:1px solid var(--line)}.topic li{position:relative;margin:12px 0}.topic li:before{content:'';position:absolute;width:8px;height:1px;left:-16px;top:14px;background:var(--line)}.sources{font-size:12px;color:var(--muted);margin:20px 0 4px}.sources a{margin-right:16px}.back{font-size:12px;display:block;text-align:right}.source-list li{margin:12px 0}.closing{padding-top:32px;margin-top:24px;border-top:2px solid var(--ink)}.quick{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin:24px 0}.quick>div{border:1px solid var(--line);border-radius:8px;padding:20px}.quick p{margin:8px 0}.footer{margin-top:40px;border-top:1px solid var(--line);padding-top:16px;font-size:12px;color:var(--muted)}
@media(max-width:800px){main{padding:28px 20px}.directory{grid-template-columns:1fr 1fr}.topics,.quick{grid-template-columns:1fr}.topic{padding:20px}.chapter h2{font-size:20px}.branch-map{margin:16px 0}.lead{font-size:16px}.meta{gap:12px}.notice{padding:12px 16px}}
@media(max-width:420px){main{padding:24px 16px}.directory{gap:16px}.directory h3{font-size:16px}.directory a{font-size:12px}.topic{padding:16px}.chapter>details>summary{gap:8px}.chapter h2{font-size:20px}}
@media print{main{max-width:none;padding:0}header{padding-top:12px}.directory{display:block;columns:2}.directory section{break-inside:avoid}.svg-scroll{overflow:visible}svg,.overview svg{min-width:0}.topic{break-inside:avoid}.topics{display:block}.topic{margin:16px 0}.chapter{break-before:page}.back{display:none}.notice{background:#fff}.chapter>details>summary:after{display:none}}
"""


def render_html(data):
    sections = data['sections']
    topics = sum(len(s['groups']) for s in sections)
    leaves = sum(len(g['items']) for s in sections for g in s['groups'])
    out = ['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
           '<meta name="color-scheme" content="light"><title>我们的交易系统 · 思维导图 v1</title>',
           f'<style>{CSS}</style></head><body><main id="top"><header><div class="eyebrow">SPIKE / TRADING SYSTEM / V1</div><h1>我们的交易系统</h1>',
           '<p class="lead">从盘前判断，到一笔交易结束。<br>把依据、动作和失效条件放在同一张地图里。</p>',
           f'<div class="meta"><span>{esc(data["date"])}</span><span>5 个阶段 / {len(sections)} 个分支 / {topics} 个主题 / {leaves} 条细节</span><span>灰底＋橙色重点</span></div></header>',
           '<div class="notice"><strong>这是一份逐步定稿的流程地图。</strong> #49 原始规则、#50 方法、Manual-v4 草案与 #53 监控设计分别保留状态。单笔风险、具体确认与部分退出细则仍待你决定；导图完成不代表这些打法已通过盈利验证。</div>',
           '<div class="legend"><span class="tag">原始规则 ≠ 盈利验证</span><span class="tag">已有草案</span><span class="tag">方法整理</span><span class="tag">方案待实现</span><span class="tag">待确认</span><span class="tag">检查建议</span></div>',
           '<style>.overview-mobile{display:none}@media(max-width:600px){.overview-desktop{display:none}.overview-mobile{display:block}.overview-mobile svg{min-width:0}}</style>',
           '<figure class="overview overview-desktop svg-scroll" aria-label="总览">' + overview(data) + '</figure><figure class="overview overview-mobile" aria-label="手机总览">' + mobile_overview(data) + '</figure>',
           '<p class="muted">先选阶段，再看分支。图中节点可点击；每节标题可折叠。手机可横滑导图，下方文字按屏幕宽度排列。</p><nav class="directory" aria-label="导图目录">']
    for domain in data['domains']:
        out.append(f'<section id="domain-{domain["id"]}"><h3>{esc(domain["title"])}</h3><p>{esc(domain["desc"])}</p>')
        out.extend(f'<a href="#{s["id"]}"><span>{s["id"][1:]}</span>{esc(s["title"])}</a>' for s in sections if s['domain'] == domain['id'])
        out.append('</section>')
    out.append('</nav><section class="quick" aria-label="日常使用入口"><div><h3>每笔先回答这五句</h3><p>我看见什么事实？<br>我采用哪个模型解释？<br>现在做、等，还是跳过？<br>什么变化会推翻判断？<br>何时、看到什么再检查？</p></div><div><h3>先定稿的三个区域</h3><p>模型与版本 → 第 01、05、08、09 节<br>具体确认与退出 → 第 05、06、15 节<br>风险与停手边界 → 第 12、16 节</p><p class="muted">空白项保留为待确认，不用默认数字填满。</p></div></section>')
    for section in sections:
        sid = section['id']
        out.append(f'<section class="chapter" id="{sid}"><details open><summary><span class="number">{sid[1:]}</span><div><h2>{esc(section["title"])}</h2><p class="brief">{esc(section["brief"])}</p><span class="tag">{esc(section["status"])}</span></div></summary>')
        out.append('<figure class="branch-map svg-scroll">' + branch(section) + '</figure><div class="topics">')
        for i, group in enumerate(section['groups']):
            out.append(f'<article class="topic" id="{sid}-g{i + 1}"><h3>{esc(group["title"])}</h3><ul>')
            out.extend(f'<li>{esc(item)}</li>' for item in group['items'])
            out.append('</ul></article>')
        out.append('</div><p class="sources">依据：')
        for key in section['sources']:
            source = data['sources'][key]
            out.append(f'<a href="#source-{key}">{esc(source["title"])}</a>')
        out.append('</p><a class="back" href="#top">返回总览 ↑</a></details></section>')
    out.append('<section class="closing"><h2>来源与版本</h2><p>规则图以已有材料为依据；公开文档仅用于校核时区和保证金概念，不证明交易方法有效。#57 的 Drive / Figma / PDF 位置在界面核验为空白占位，未把它们当作额外附件来源。</p><ul class="source-list">')
    for key, source in data['sources'].items():
        title = esc(source['title'])
        address = f'<br><span class="muted" style="overflow-wrap:anywhere">{esc(source["url"])}</span>' if source['url'] else ''
        out.append(f'<li id="source-{key}">{title}{address}' + (f'<br><span class="muted">{esc(source["note"])}</span>' if source.get('note') else '') + '</li>')
    out.append('</ul><p>以上为可复制的完整来源地址；Notion 任务正文另保留可点击原页引用。迭代方式：指出节点编号（如 05／5m 企稳）与修改理由，新增 v2 并保留 v1；规则变化单独记生效时刻，旧交易按旧版本复盘。此文件无脚本、无订单接口、无外部图片，正文可离线阅读。</p></section><footer class="footer">SPIKE · Notion #57 · 2026-10-15 · 字体优先 Geist / Instrument Serif，本机未安装时使用系统中文字体；无外部字体请求。导图交付与策略准入分别记录。</footer></main></body></html>')
    return ''.join(out)


def render_notion(data):
    out = ['## 思维导图 v1 · 可持续迭代', '<mention-date start="2026-10-15"/>',
           '已按浅灰底＋橙色重点制作：5 个阶段、18 个分支、72 个主题、216 条细节。下方 HTML 是可点击目录与折叠的完整导图；再下方保留原生可编辑节点，后续按编号修订。',
           '**使用状态：流程地图 v1 已交付。** 原始规则、现有草案、方法整理、待实现设计和待确认参数已分别标注。Manual-v4 仍为候选草稿且绑定 V12.8；本次没有新盈利验证、训练、promote 或下单。',
           'HTML_ATTACHMENT_PLACEHOLDER', '### 从哪里开始',
           '- 开始看盘：01–04。选模型：05–10。做一笔交易：11–13。管理与结束：14–16。复盘与验证：17–18。',
           '- 先确认：主做模型／指标版本、周一日界、5m 企稳定义、0.618 方向、初始保护、单笔与组合预算、停止新开与离屏、T1 后及跨周管理。没有依据的数值保持待填。',
           '- 操作事实与研究状态分开：模拟盘的 100× 合约／10× 权益仓位是工具设置，不是实盘风险授权；80/20 仅属于周一区间分支。',
           '### 可编辑节点']
    for section in data['sections']:
        out.append(f'<details>\n<summary>{section["id"][1:]} · {section["title"]}｜{section["status"]}</summary>')
        out.append('\t' + section['brief'])
        for group in section['groups']:
            out.append('\t- **' + group['title'] + '**')
            out.extend('\t\t- ' + item for item in group['items'])
        refs = []
        for key in section['sources']:
            source = data['sources'][key]
            if 'notion.com' in source['url']:
                refs.append(f'<mention-page url="{source["url"]}"/>')
            elif source['url']:
                refs.append(f'[{source["title"]}]({source["url"]})')
            else:
                refs.append(source['title'])
        out.append('\t依据：' + '；'.join(refs))
        out.append('</details>')
    out.extend(['### 维护约定', '修订时引用节点编号，保留旧版本和变更理由；涉及风险比例、TP／SL、退出语义或实盘准入，由 Owner 明确决定。新资料先标待验证，不用盈利个例倒推规则已验证。',
                '来源核对：第49／50／53／54条、Manual-v4、当前本机流程。第57条支持文件中的PDF、Drive、Figma已在界面核验为空白占位；未读取不存在的附件。',
                '本轮只读核对个人工作区：8条原则、2套打法均为draft。既有规则正文与附件占位保留。'])
    return '\n\n'.join(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--html', required=True, type=Path)
    parser.add_argument('--notion', type=Path)
    args = parser.parse_args()
    data = json.loads(args.source.read_text())
    ids = [s['id'] for s in data['sections']]
    if len(ids) != len(set(ids)):
        raise ValueError('Section IDs must be unique.')
    if any(len(s['groups']) != 4 for s in data['sections']):
        raise ValueError('This layout requires four groups per branch.')
    args.html.write_text(render_html(data))
    if args.notion:
        args.notion.write_text(render_notion(data))
    print(f'Wrote {args.html} ({args.html.stat().st_size} bytes).')


if __name__ == '__main__':
    main()

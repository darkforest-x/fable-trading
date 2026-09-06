"""Validate saved public-source reviews and build a bounded technical report.

No market data, Pine execution, strategy optimization, network or trading.
Inputs: frozen catalogue, official publication/source cards and three manually
authored per-item review JSONs. Exact Pine bytes, not normalized text, define
source hashes. Mechanism families are a manual navigation partition, not an
empirical independence result. Author/source/license attribution stays intact.
Official packaging contract: Data Analytics build-report portable artifact.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from urllib.parse import quote


E = Path(__file__).resolve().parent
ROOT = E.parents[2]
CATALOGUE_HASH = "15eb91896e7bebee9728bda4ecf3ba2d190ae640cebfc87f01ad214f86e1c365"
TITLE = "ChartPrime Confluence Audit"
REPORT = "analysis/p1_chartprime_public_confluence_audit_20260906.md"
REVIEW_FILES = ("reviews_000_044.json", "reviews_045_094.json", "reviews_095_147.json")
TEXT_FIELDS = ("id", "title", "category", "formula", "parameters", "clock_risk",
               "confluence_role", "independence", "test_hypothesis", "source_url", "review_level")
# Indices reference the byte-frozen catalogue, not mutable titles or live order.
FAMILIES = {
    "趋势与均线": [0,3,13,22,23,24,26,37,38,42,52,58,59,60,62,64,65,67,70,71,75,77,78,79,80,81,82,83,86,87,88,95,98,102,107,109,118,128,139,147],
    "动量与波动": [16,27,46,47,50,53,56,61,89,99,110,115,121,138,140,145],
    "结构与区间": [6,7,8,15,18,19,21,36,39,43,49,51,57,68,69,72,74,91,93,94,97,106,113,116,117,120,123,124,129,130,132,135,137],
    "成交量与成本区": [2,4,5,9,10,11,20,41,45,48,63,76,100,108,126,131,144],
    "多周期与扫描": [17,34,66,73,92,111,119],
    "预测与统计模型": [1,14,25,90,96,103,104,105,112,114,127,134,136],
    "策略与退出工具": [12,28,29,31,122,125,133,146],
    "源码受限": [30,32,33,35,40,44,54,55,84,85,101,141,142,143],
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate(catalogue, reviews, records, blobs):
    """Check evidence joins and access; not a substitute for semantic code review."""
    items = catalogue["scripts"]
    ids = [x["id"] for x in items]
    rids = [x["id"] for x in reviews]
    require(len(ids) == len(set(ids)), "duplicate catalogue ID")
    require(len(rids) == len(set(rids)), "duplicate review ID")
    require(set(ids) == set(rids) == set(records), "coverage mismatch")
    require(catalogue["declared_count"] == catalogue["actual_count"] == len(ids), "count mismatch")
    require(catalogue["complete"] is True, "incomplete catalogue")
    by_id = {x["id"]: x for x in reviews}
    levels = Counter()
    for item in items:
        sid = item["id"]
        row, card = by_id[sid], records[sid]
        for field in TEXT_FIELDS:
            require(isinstance(row.get(field), str) and row[field].strip(), f"{sid} missing {field}")
        require(row["title"] == card["title"] == item["title"], f"{sid} title mismatch")
        require(card["id"] == sid and card["url"] == item["url"], f"{sid} identity mismatch")
        require(not card.get("error"), f"{sid} collection error")
        script = card.get("script", {})
        require(script.get("access") == item.get("script_access_raw"), f"{sid} catalogue access mismatch")
        require(script.get("script_id_part") == item.get("script_id_part")
                and str(script.get("version_maj")) == str(item.get("version")), f"{sid} catalogue source identity mismatch")
        is_open = item["script_access_raw"] == 1
        require(script.get("has_access") is is_open, f"{sid} explicit permission mismatch")
        refs = row.get("source_lines")
        require(isinstance(refs, list), f"{sid} source lines must be list")
        if is_open:
            require(row["review_level"] == "source_read", f"{sid} open source not reviewed")
            require(sid in blobs, f"{sid} source missing")
            digest = sha256(blobs[sid]).hexdigest()
            require(digest == row.get("source_sha256") == card.get("source_sha256"), f"{sid} source hash mismatch")
            lines = len(blobs[sid].decode("utf-8").splitlines())
            require(lines == card["source_lines"], f"{sid} source line count mismatch")
            require(refs and all(type(n) is int and 1 <= n <= lines for n in refs), f"{sid} source line out of bounds")
            require(row["source_url"] == card["source_url"], f"{sid} source URL mismatch")
            expected_url = "https://pine-facade.tradingview.com/pine-facade/get/{}/{}".format(quote(item["script_id_part"], safe=""), item["version"])
            require(card["source_url"] == expected_url, f"{sid} catalogue source URL mismatch")
            require(card.get("source_metadata", {}).get("scriptAccess") == "open_no_auth", f"{sid} public access mismatch")
            require(str(card["source_metadata"].get("version", "")).split(".")[0] == str(item["version"]), f"{sid} payload version mismatch")
        else:
            require(row["review_level"] == "description_only", f"{sid} inaccessible source claim")
            require(not refs and not row.get("source_sha256") and sid not in blobs, f"{sid} closed source fabricated")
            require(row["source_url"] == card["url"], f"{sid} closed source must cite publication")
        levels[row["review_level"]] += 1
    require(set(blobs) == {sid for sid in ids if by_id[sid]["review_level"] == "source_read"}, "extra Pine source")
    return {"listed": len(ids), "review_levels": dict(levels), "source_sha_verified": len(blobs),
            "missing_reviews": 0, "duplicate_reviews": 0, "collection_errors": 0,
            "validation_scope": "Coverage/access/hash/reference bounds; semantic review is separate. Not Pine compilation, runtime or profit validation."}


def family_map(size=148):
    entries = [(i, name) for name, indices in FAMILIES.items() for i in indices]
    require(len(entries) == size and len({i for i, _ in entries}) == size
            and {i for i, _ in entries} == set(range(size)), "family partition mismatch")
    return dict(entries)


def load_evidence():
    raw = (E / "catalogue.json").read_bytes()
    require(sha256(raw).hexdigest() == CATALOGUE_HASH, "catalogue changed; review scope must be re-frozen")
    cat = json.loads(raw)
    rows = []
    for filename in REVIEW_FILES:
        rows.extend(json.loads((E / filename).read_text()))
    records = {p.stem: json.loads(p.read_text()) for p in (E / "sources").glob("*.json")}
    blobs = {p.stem: p.read_bytes() for p in (E / "sources").glob("*.pine")}
    return cat, rows, records, blobs


def narratives():
    return [
        ("summary", "## 能加入共振，但应补足不同信息，而不是叠加同色箭头\n\n本次逐项审查 ChartPrime 公开主页的 148 个脚本：134 个读取公开源码，14 个因源码受限只分析官方说明。**最值得推进的是结构脱离、相对参与量、成本区与空间这几类机制；不建议把多套均线/振荡器的颜色直接凑票。** 每项的实际公式、默认参数、信息何时可用、适合角色和单变量验证假说均在下方独立条目中。\n\n这是源码筛选结果，不是盈利验收。没有把任何新指标加入当前系统，没有跑新价格回测，也没有修改 TradingView。源码默认参数不是最优参数；148 个脚本不是 148 份独立证据。"),
        ("scope", "## 范围：完整公开清单，不代表全部产品源码可见\n\n截至北京时间 2026 年 9 月 6 日晚，按官方分页接口列举七页，共 148 个唯一发布 ID；与主页数量一致。不同 ID 的同名脚本保留：例如 Multiple Non-Linear Regression 有公开版和受限版，不能按名称去重。范围不含已删除、隐藏或未公开的产品。\n\n“源码已读”指检查下载到的指定版本；“仅说明”指未获公开源码，不推断其真实公式或重绘情况。每项保留作者与许可、官方链接、源码字节哈希和关键行号。来源页面版本以后可能变化，本报告是冻结快照。\n\n研究分母是脚本，不是交易。没有 OHLCV 市场样本、训练集、验证集或 holdout 消耗。AUC、置换 p、毛/净收益、胜率、匹配随机交易对照均不适用：尚未生成策略收益。对应严格检查是清单完整性、访问控制、字节哈希、引用范围和故意破坏输入时必须拒绝的测试。"),
        ("findings", "## 三类看起来很强、却不能直接作为共振的证据\n\n**名称与真实数据不同。** Liquidity Flow Surge Profile 用成交量相对 500 根最大量与 K 线涨跌标注多/空清算，不读取交易所清算流。Volumetric Trend Ribbon Pro 的量峰值只影响颜色，并没有进入 bullCross 的触发条件。不能因为图上写着 liquidation、CVD、Strong 就按真实订单流计票。\n\n**绘制时刻与可用时刻不同。** Trend Classifier、SuperTrend Oscillator 的部分菱形使用当前信号配 offset=-1，图上提前一根。HTF Conviction Divergence Matrix 未偏移的 HTF OHLC 配 lookahead_on，会令该大周期背景/价位在历史上提前知道；这不等于它所有本周期 RSI 背离计算都泄漏。枢轴右侧确认、最新波段重画、实时未收盘波动也要分别处理，不能统称一个“重绘”。\n\n**多个公式可能重复同一个条件。** Bayesian Trend 的概率形态输出来自相关均线得分组合，不是校准过的交易胜率。Dynamic Trend Bands 的 Source 输入被函数内部 close 覆盖。默认值、工具提示与真实执行路径都必须对照源码。\n\n对应原始来源：[Flow Surge](https://www.tradingview.com/script/EQSY3xU0/)、[VTR Pro](https://www.tradingview.com/script/sXyMhOCc/)、[Trend Classifier](https://www.tradingview.com/script/AtJtdaDe/)、[SuperTrend Oscillator](https://www.tradingview.com/script/JqEFTgOE/)、[HTF Matrix](https://www.tradingview.com/script/8pOsueGg/)、[Bayesian](https://www.tradingview.com/script/rVEhAQDO/)、[Dynamic Bands](https://www.tradingview.com/script/fsfPi8mp/)。"),
        ("composition", "## 库的构成：先区分信息来源，再决定要测什么\n\n下图按源码主要机制做互斥导航分类；受限脚本单独归类。数量表示条目覆盖，不表示策略质量或独立因子数量。一个脚本可能混合多种机制，因此详细条目的 category 与这里较宽的主分类可不同。价格平滑、结构、成交量代理分别有价值，但都需要与现有特征做增量比较。"),
        ("shortlist", "## 建议顺序：先减少盘整入场，再研究更好的兑现\n\n| 优先研究机制 | 可借鉴源码 | 在我们的系统中负责什么 | 使用前必须处理 |\n|---|---|---|---|\n| 已确认结构脱离 | Market Break Analytics；Breakout Boxes | K1/K2 之前的方向背景，或 K1 是否真正离开已存在区间；避免均线附近反复开仓 | 枢轴分别默认右确认10/5根；不能把确认后的结构回填到旧 K1；Market Break 默认显示单方向 |\n| 成本区与前方空间 | Session VWAP + StdDev Bands；已确认支撑阻力类 | 判断在成本区内部震荡，还是脱离成本区；避免直接撞上已知阻力 | VWAP 默认周锚，原代码时间映射的历史/实时重置需先做一致性测试；不是直接复制周末重置行为 |\n| 相对参与量 | Volumetric Trend Ribbon Pro 的 volume/SMA(volume,60)，只借鉴独立量比字段 | 比较同一入口有量支持与无量支持的增量；不能把原带突破称为量能确认 | 默认1.5只用于量峰颜色；若加成交易门是新假说，保留前置冻结与费用、匹配对照 |\n| 单一中性/冲突状态 | Trend Classifier | 作为现有均线过滤的对照或替代候选，观察能否减少来回打脸 | 橙色并非经验证的盘整；与斜率、距离、振幅重叠；移除回画的成交假象 |\n| 获利衰减与余仓管理 | SuperTrend Oscillator；Dynamic Trend Bands | 之后单独比较减仓/退出时钟，避免利润全部回吐 | 信号按实际收盘确认；兑现与保护止损分账，不能把抬高止损记成已止盈 |\n\n这些是**机制候选，不是已通过的推荐参数**。优先从一个结构状态门开始做支持度审计；不要一次把表内条件全部相与，造成只剩几笔“完美交易”。历史波动率指标只衡量自身波动分位，不等于趋势有效、也不等于波动正在扩张。真正交易所主动买卖量、持仓量或资金费率需要另一个可追溯数据源，本次不会用 OHLCV 代理冒充。\n\n既有项目已经试过斜率、穿越次数、效率、量能、突破和高周期方向等入口家族；这里不能把同类换皮当全新发现。下一步先查新公式相对旧字段的重复性，再决定是否值得一次真实实验。"),
        ("rules", "## 怎样加入 K1/K2，而不再满屏信号\n\n建议架构是“允许交易的环境 → 原有 K1/K2 → 一次有效入场 → 分账管理余仓”，不是每个指标各自开一单。首先只读已完成 bar 的环境信息；盘整或证据冲突时不放行。K1/K2 的原始定义、硬止损、成交时钟与成本先固定，单独测一个额外条件。\n\n评价保留全部原始机会：新增过滤拒绝的机会既包括亏损也包括后来赢家，必须同时统计。比较净收益、总收益机会成本、匹配随机对照超额、不同时间段、交易覆盖、错过的大趋势和尾部回撤。不能只看被筛留下来的胜率，也不能先选出后来启动的170笔再反推共同特征。\n\n若以后研究兑现，至少分别记录实际已卖出/买回的比例及成交价、仍持有部分、当时可用止损。小周期变色可作为减仓候选，不自动等于全趋势终结；但任何分批方案仍需全路径回放，不能保证留余仓一定更赚。此处不冻结新的阈值或 TP/SL 参数，也未执行任何交易。"),
        ("index_intro", "## 全部条目索引\n\n索引保留主页顺序，便于找到名称和下方同号条目。每个条目分别说明机制、参数、风险与用途；不是用统一模板把名字换一遍。受限项目也保留，不以缺源码为由悄悄排除。"),
    ]


def build(cat, reviews, cards, validation):
    stamp = datetime.now(timezone.utc).isoformat()
    fam = family_map()
    by_id = {r["id"]: r for r in reviews}
    ordered = [by_id[item["id"]] for item in cat["scripts"]]
    source_base = E.relative_to(ROOT).as_posix()
    sources = [{"id":"catalogue", "label":"TradingView / ChartPrime · 冻结完整公开清单与逐项审查",
                "href":cat["profile_url"], "path":source_base+"/catalogue.json",
                "query":{"language":"python", "engine":"Python stdlib", "executed_at":stamp,
                         "tables_used":[source_base+"/catalogue.json"]+[source_base+"/"+p for p in REVIEW_FILES],
                         "description":"Exact ID joins plus manually assigned primary-family counts; build_report.py. No market rows or return estimates.",
                         "filters":["All 148 publications on frozen ChartPrime profile; no sampling or title deduplication"],
                         "metric_definitions":["script_count = count of unique publication IDs in a mutually exclusive manually assigned main-mechanism family", "Closed-source family counts inaccessible source cards, not failed downloads"]}}]
    blocks = [{"id":"title", "type":"markdown", "layout":"full", "body":"# "+TITLE}]
    text_sections = []
    for sid, body in narratives():
        blocks.append({"id":sid,"type":"markdown","layout":"full","body":body})
        text_sections.append(body)
        if sid == "composition":
            blocks.append({"id":"family_plot", "type":"chart", "chartId":"families", "layout":"full"})
        if sid == "index_intro":
            blocks.append({"id":"index_table", "type":"table", "tableId":"index", "layout":"full"})
        if sid == "shortlist":
            additions = [
                ("proxy_limits", "## 对候选名称的限定\n\n上文“成本区”指成交量加权价格或价格停留分布代理，不是持仓者真实成本；量比是单独提取的字段，不代表已证统计独立。部分公开源码采用 CC BY-NC-SA 等许可，并非全部都是 MPL；本次保留原许可与署名，后续复制、改写和分发前须逐项核对，不能把公开可读等同于任意用途许可。"),
                ("breadth", "## 比再加一条均线更值得验证：跨资产市场广度\n\n[Multi Asset Histogram](https://www.tradingview.com/script/KkoxM97D/) 确实读取十个外部资产，默认 BTC、ETH、BNB、SOL、XRP、DOGE、ADA、AVAX、DOT、LINK 的 USD 对。它将当前 HL2 与过去50根逐根比较，逐次计+1或−1后求和；这不是涨幅或资金流。\n\n可借鉴的共振是假设：当 BTC/ETH 的 K1 启动时，其他事前固定资产是否也在同方向推进，而不是单币在均线附近抖动。研究时排除目标币自己的重复票、固定币池与数据源、只合并共同已完成时刻，缺少历史记未知，不能沿用源码 nz→0 造成的暖机偏高。原输入未锁交易所/永续口径，也不能默认与本项目一致。\n\n这比同币再加一种平滑有更明确的信息来源差异，但加密资产共同 beta 很强，仍需要同环境随机入场对照；不能说它已经提供独立超额收益。对黄金不直接套加密币池。"),
                ("core_colour", "## 原版 K 线变色不是完整趋势反转\n\n[Moving Average Shift](https://www.tradingview.com/script/aApUyBnk/) 默认用 HL2 与 SMA40 比较：HL2≥均线为青色，否则橙色，实体、影线和边框都用同一个侧色。它不检查均线斜率；振荡器的四色与转向菱形是另外一套条件。\n\n所以小周期变色表示“价格中点换到均线另一侧”，可能是真反转，也可能仅是趋势内回踩。用它当减仓或离场信号可以提出假说，但不能在定义上把它直接叫作趋势终结。源码主线和光晕的默认线宽与用户希望的细线/关闭光晕也需分开：本次只做语义审查，不修改已有图表样式。"),
            ]
            for extra_id, extra_body in additions:
                blocks.append({"id":extra_id,"type":"markdown","layout":"full","body":extra_body})
                text_sections.append(extra_body)
    index_rows = []
    for i, row in enumerate(ordered):
        sid = row["id"]
        card = cards[sid]
        level = "源码已读" if row["review_level"] == "source_read" else "仅官方说明"
        index_rows.append({"number":i+1,"title":row["title"],"family":fam[i],"evidence":level,"publication_id":sid})
        sources.append({"id":sid,"label":row["title"]+" · "+level,"href":card["url"],
                        "path":source_base+"/sources/"+sid+(".pine" if row["review_level"] == "source_read" else ".json"),
                        "query":{"language":"pine" if row["review_level"] == "source_read" else "text",
                                 "description":"Official source: "+row["source_url"]+"; source SHA256: "+str(row.get("source_sha256") or "unavailable")+"; cited lines: "+str(row["source_lines"]),
                                 "tables_used":[source_base+"/sources/"+sid+".json"],
                                 "filters":["Frozen official version "+str(card.get("script",{}).get("version_maj")),"Static source review, not runtime verification"]}})
        body = "## {:03d} · {}\n\n{} · {}\n\n".format(i+1,row["title"],level,row["category"])
        for field, label in [("formula","实际机制"),("parameters","参数与默认值"),("clock_risk","确认时刻与重绘风险"),("confluence_role","在本系统中的角色"),("independence","重复性与独立性"),("test_hypothesis","可验证假说（未回测）")]:
            body += "**{}：** {}\n\n".format(label,row[field])
        body += "[官方发布页]({})".format(card["url"])
        if row["review_level"] == "source_read":
            body += " · [指定版本源码]({}) · 关键行 {}。".format(row["source_url"],", ".join(map(str,row["source_lines"])))
        else:
            body += "。未读取、猜测或绕过保护获取源码。"
        blocks.append({"id":"review_"+sid,"type":"markdown","layout":"full","body":body,"sourceId":sid})
        text_sections.append(body)
    tail = [
        ("limitations", "## 风险与诚实声明\n\n134份公开源逐项阅读不等于134项已经编译、实时重载与多周期一致性全通过；外部导入库除另存明确审查记录的调用路径外，不能视为全部递归审完。源码中的窗口、默认参数与条件有证据，经验上的有效性、相互独立性、最优周期没有被本次证明。\n\n14项仅能审官方描述，不知道其私有公式，不能提供无重绘或盈利保证。列表是当前公开存量，有删除/隐藏产品的存续偏差。图形对象随最新bar移动不必然使所有数值非因果；相反，closed-bar门也不能修复 request.security 表达式本身的未来泄漏。\n\n这是静态技术审查，不是财务建议、收益承诺或实盘升级。现有盈利目标仍未达成；本次没有用这一批指标产生任何新收益结果。选择待测候选本身有研究者自由度，未来试验仍应记录失败、采用时间切分、固定成本并保留全机会与匹配对照。"),
        ("validation", "## 验证：清单与源码证据通过，盈利与运行正确性尚未验证\n\n检查逐项发布ID、标题、公开权限、源码原始字节哈希、引用行范围及必填分析字段；对缺项、重复、错误哈希、闭源冒充已读、越界行等输入用负例测试拒绝。所有公开源码从官方允许 open_no_auth 的接口获取；受限条目不请求源码端点。\n\n这些检验能发现错配、遗漏与证据伪装，不能自动证明每句公式解释正确。高影响结论另做了跨审查的源码路径核对。最终报告由规范化内容生成并经过便携报告验证；具体验证收据随产物保存，未实际执行的浏览器/手机测试不能称为通过。"),
        ("questions", "## 下一步仍需回答的关键问题\n\n1. 新结构/成本区字段是否比已试过的斜率、距离、突破字段提供新增信息，还是只是再次筛同一批交易？\n2. 一个新过滤能否在多个时间段减少盘整损失，同时不过度牺牲后来大趋势的捕获率？\n3. 同一机制对 BTC、ETH、黄金分别是否成立？必须分币种与成本验证，不能由一个截图外推全周期。\n4. 小周期信号用于减仓还是全部离场更好？这是下一阶段退出实验，不能与入口过滤同时修改后混淆归因。\n\n建议先从完成bar的结构状态做一个可用性/覆盖审计，通过后才做固定旧退出的单变量回测。当前没有选定“最优共振组合”，也不以默认参数冒充优化结果。"),
        ("reproduce", "## 可复现路径\n\n使用仓库现有环境，不安装依赖。以下从已冻结的完整清单和人工审查文件开始：采集器仅对缺失的公开证据发起只读请求，已有文件不覆盖；下次网页更新不会改变这里冻结的源码。\n\n```bash\n.venv/bin/python experiments/active/exp-chartprime-public-confluence-audit-20260906-v1/collect_sources.py --catalogue experiments/active/exp-chartprime-public-confluence-audit-20260906-v1/catalogue.json --out experiments/active/exp-chartprime-public-confluence-audit-20260906-v1/sources\n.venv/bin/python -m pytest -q experiments/active/exp-chartprime-public-confluence-audit-20260906-v1/test_collect_sources.py experiments/active/exp-chartprime-public-confluence-audit-20260906-v1/test_build_report.py\n.venv/bin/python experiments/active/exp-chartprime-public-confluence-audit-20260906-v1/build_report.py\n```\n\n最后执行 ARTIFACT_PLAN 中选定的官方便携 HTML 打包命令；报告构建器会先自动运行仓库要求的 MD→HTML 转换。清单分页 URL、检索时间、版本与 SHA 保存在证据目录。人工语义审查不能靠重新运行采集器自动再现，三份逐项审查 JSON 是保留的判断记录。"),
    ]
    for sid, body in tail:
        blocks.append({"id":sid,"type":"markdown","layout":"full","body":body})
        text_sections.append(body)
    counts = Counter(fam.values())
    family_rows = [{"family":k,"script_count":v,"total_scripts":148} for k,v in sorted(counts.items(), key=lambda kv:(-kv[1],kv[0]))]
    manifest = {"version":1,"surface":"report","title":TITLE,"generatedAt":stamp,"filters":[],"cards":[],
        "charts":[{"id":"families","title":"公开脚本主要机制分类","type":"bar","intent":"comparison","layout":"full",
                   "dataset":"families","sourceId":"catalogue","maxRows":8,"valueFormat":"number",
                   "question":"148个公开脚本主要属于哪些机制，而不是独立胜率信号？",
                   "rationale":"One full-width count series across eight mutually exclusive manually reviewed navigation families; not a profitability ranking.",
                   "palette":{"kind":"sequential","name":"blue"},"labels":{"values":"all"},
                   "settings":{"sort":"none","categoryLabelPolicy":"wrap"},
                   "referenceLines":[{"axis":"y","value":0,"color":"neutral","lineStyle":"solid","label":"0 个"}],
                   "encodings":{"x":{"field":"family","type":"nominal","label":"主要机制（导航分类）"},
                                "y":{"field":"script_count","type":"quantitative","label":"脚本数","format":"number"},
                                "tooltip":[{"field":"total_scripts","type":"quantitative","label":"全部脚本"}]}}],
        "tables":[{"id":"index","title":"148项完整索引","dataset":"index","sourceId":"catalogue","defaultSort":{"field":"number","direction":"asc"},
                   "columns":[{"field":"number","label":"序号","format":"number"},{"field":"title","label":"指标","type":"text"},{"field":"family","label":"主机制","type":"text"},{"field":"evidence","label":"证据","type":"text"}]}],
        "blocks":blocks,"sources":sources}
    artifact = {"surface":"report","manifest":manifest,"snapshot":{"version":1,"generatedAt":stamp,"status":"ready","datasets":{"families":family_rows,"index":index_rows}},"sources":sources}
    md = "# "+TITLE+"\n\n"+"\n\n".join(text_sections)+"\n"
    return artifact, md


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    cat, rows, cards, blobs = load_evidence()
    result = validate(cat, rows, cards, blobs)
    family_map()
    require(result["listed"] == 148 and result["review_levels"] == {"source_read":134,"description_only":14}, "scope changed")
    result["catalogue_sha256"] = CATALOGUE_HASH
    result["review_file_sha256"] = {p:sha256((E/p).read_bytes()).hexdigest() for p in REVIEW_FILES}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.validate_only:
        return
    artifact, md = build(cat, rows, cards, result)
    (ROOT/REPORT).write_text(md)
    subprocess.run(["python3","scripts/md_to_html.py",REPORT,"--out-dir","analysis/html"],cwd=ROOT,check=True)
    (E/"artifact.json").write_text(json.dumps(artifact,ensure_ascii=False,indent=2)+"\n")
    (E/"validation.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")


if __name__ == "__main__":
    main()

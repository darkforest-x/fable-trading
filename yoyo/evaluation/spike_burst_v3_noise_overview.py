"""Read-only, authenticated overview of original V1/V3 and fixed A-F research.

The explicit completed F validation SHA and F report manifest are mandatory.
All historical receipts/outputs/source hashes are verified through the frozen F
report authenticator. This module reads stored aggregate results only: no market
fetch, detector, simulator, scorer or parameter selection. The plot places every
prespecified A-F arm against unchanged label-reduction and timely-recall targets.
Original V1 has no authenticated V3-hit-retention metric and is table-only. Each
historical arm retains its own matched control schedule; excesses cannot be
ranked as though evaluated against one common baseline. Unknown F labels are
added back for its primary gate; plotting raw publication savings does not
turn unknown cases, same-bar merging, or old trade coverage into signal quality.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_burst_v3_price_acceptance_report as report

ROOT = report.ROOT
OUT = report.OUT
MD = ROOT / "analysis/p1_spike_v3_noise_overview_20260910.md"
HTML = ROOT / "analysis/html/p1_spike_v3_noise_overview_20260910.html"
MANIFEST = OUT / "noise_overview_manifest.json"
FIGURE = OUT / "figures/noise_recall_tradeoff.png"
ARMS = (
    ("A", "reference", report.AB, "参考仍有效时不再接新父"),
    ("B", "near_box", report.AB, "重新完整近零蓄势后突破"),
    ("C", "confirmation_gate", report.C_SOURCE, "得到确认后才取得抑制权"),
    ("D", "formation_gate", report.D_SOURCE, "启动前六均线逐步收拢"),
    ("E", "htf_gate", report.E_SOURCE, "已收盘4H主线不弱于信号线"),
    ("F", "price_acceptance", OUT, "突破后下一根收盘仍站上边界"),
)
HISTORY_LINKS = (
    ("原V1/V3逐门比较", "p1_spike_v3_gate_diagnostic_20260910.html"),
    ("A/B", "p1_spike_v3_focus_20260910.html"),
    ("C", "p1_spike_v3_confirmation_gate_20260910.html"),
    ("D", "p1_spike_v3_formation_gate_20260910_r2.html"),
    ("E", "p1_spike_v3_htf_gate_20260910.html"),
    ("F", "p1_spike_v3_price_acceptance_20260910.html"),
)


def authenticate(validation_sha):
    """No aggregate may be read before F and the completed report are linked."""
    prepared, validation, inputs = report.authenticate(validation_sha)
    path = OUT / "price_acceptance_report_manifest.json"
    manifest = json.loads(path.read_text())
    report.checked(path, report.sha(path), inputs)
    if (manifest["status"] != "complete" or manifest["validation_sha256"] != validation_sha
            or manifest["prepared_sha256"] != validation["prepared_sha"]
            or not manifest["no_detection_or_rescoring"]
            or not manifest["no_outcome_based_case_selection"]):
        raise ValueError("A complete F report linked to this validation is required")
    if Path(manifest["report_builder"]).resolve() != Path(report.__file__).resolve():
        raise ValueError("F report manifest must name the authenticated report builder")
    report.checked(manifest["report_builder"], manifest["report_builder_sha256"], inputs)
    for artifact in manifest["inputs"] + manifest["outputs"]:
        report.checked(artifact["path"], artifact["sha256"], inputs)
    for figure in manifest["figures"]:
        report.checked(figure["image"], figure["image_sha256"], inputs)
    if {image["asset"] for image in manifest["figures"]} != {"HYPE", "NEAR", "PEPE"} or len(manifest["figures"]) != 3:
        raise ValueError("Exactly the three fixed illustrations are required")
    return prepared, validation, manifest, inputs


def one(table, arm, stage=None):
    selected = table[table.arm.eq(arm) & table.period.eq("full")]
    if stage is not None: selected = selected[selected.stage.eq(stage)]
    if len(selected) != 1:
        raise ValueError("Exactly one stored full-period summary row required: " + arm)
    return selected.iloc[0]


def assemble(inputs):
    """Join frozen summaries, never refit thresholds or recompute market outcomes."""
    def read(directory, filename):
        path = (directory / filename).resolve()
        if str(path) not in inputs:
            raise ValueError("Aggregate has not been authenticated: " + str(path))
        return pd.read_csv(path, float_precision="round_trip")

    retention = read(OUT, "retention_summary.csv")
    trades = read(OUT, "trade_summary.csv")
    family = read(OUT, "structural_holm_family.csv")
    if set(family.arm) != {spec[1] for spec in ARMS} or len(family) != 6:
        raise ValueError("A-F complete frozen Holm family required")
    f_assessment = json.loads((OUT / "assessment.json").read_text())
    r = one(retention, "v3", "early")
    if (r.signals, r.distinct_labels, r.positive_events, r.baseline_hit_events,
            r.hits_1, r.large_positive_events, r.large_hits_1) != (10386,12042,1660,1463,1463,947,798):
        raise ValueError("Frozen V3/label denominators changed")
    base_trade = one(trades, "v3")
    baseline = [dict(version="V3", parents=int(r.signals), labels=int(r.distinct_labels),
        timely_hits=int(r.hits_1), recall=r.recall_1, large_recall=r.large_recall_1,
        mean_net_bp=base_trade.mean_net_bp, matched_excess_bp=base_trade.mean_excess_bp,
        natural_win_rate=base_trade.natural_win_rate, controls="本轮V3/F共同控制")]
    original_r = one(read(report.ORIGINAL, "recall_summary.csv"), "v1")
    original_t = one(read(report.ORIGINAL, "trade_summary.csv"), "v1")
    baseline.insert(0, dict(version="原版V1", parents=int(original_r.signals), labels=int(original_r.signals),
        timely_hits=int(original_r.hits_1), recall=original_r.recall_1, large_recall=original_r.large_recall_1,
        mean_net_bp=original_t.mean_net_bp, matched_excess_bp=original_t.mean_excess_bp,
        natural_win_rate=original_t.natural_win_rate, controls="原V1认证历史控制，不重新匹配"))
    rows = []
    for letter, arm, directory, mechanism in ARMS:
        local_retention = read(directory, "retention_summary.csv")
        r = one(local_retention, arm, "early")
        child = one(local_retention, arm, "confirmed")
        t = one(read(directory, "trade_summary.csv"), arm)
        if (r.positive_events, r.baseline_hit_events, r.large_positive_events) != (1660,1463,947):
            raise ValueError("A-F arms must retain all original denominators")
        if not np.isclose(r.retention, r.retained_hit_events / 1463) or not np.isclose(r.large_recall_1, r.large_hits_1 / 947):
            raise ValueError("Stored recall differs from fixed counts")
        f = family[family.arm.eq(arm)].iloc[0]
        raw_p = t.permutation_p
        if not ((pd.isna(raw_p) and pd.isna(f.raw_p)) or raw_p == f.raw_p):
            raise ValueError("Holm input must equal this historical arm's frozen p")
        unknown = int(f_assessment["unknown_original_labels"]) if arm == "price_acceptance" else 0
        conservative = int(r.distinct_labels) + unknown
        label_pass = bool(r.label_drop >= .5 and r.distinct_labels <= 6021)
        if arm == "price_acceptance":
            label_pass &= conservative <= 6021 and 1 - conservative/12042 >= .5
        retained_pass = bool(r.retention >= .9)
        large_pass = bool(r.large_recall_1 >= .8)
        economic_pass = bool(t.mean_excess_bp > 0 and f.holm_p_six < .01)
        rows.append(dict(letter=letter, arm=arm, mechanism=mechanism, parents=int(r.signals),
            children=int(child.signals), labels=int(r.distinct_labels), label_drop=r.label_drop,
            original_retained=int(r.retained_hit_events), original_retention=r.retention,
            large_hits=int(r.large_hits_1), large_recall=r.large_recall_1,
            mean_net_bp=t.mean_net_bp, matched_excess_bp=t.mean_excess_bp,
            matched_actual_bp=t.paired_actual_net_bp, matched_random_bp=t.paired_random_net_bp,
            natural_win_rate=t.natural_win_rate, natural_exits=int(t.natural_exits), censored=int(t.censored),
            raw_p=raw_p, holm6_p=f.holm_p_six, label_pass=label_pass,
            retention_pass=retained_pass, large_pass=large_pass, economic_pass=economic_pass,
            all_pass=label_pass and retained_pass and large_pass and economic_pass,
            unknown_original_labels=unknown, conservative_labels=conservative,
            control_schedule="A/B共同日程" if letter in "AB" else letter+"与其当轮V3共同日程",
            source_retention=str(directory/"retention_summary.csv"), source_trades=str(directory/"trade_summary.csv")))
    # F gate decisions were already frozen, so an overview must not silently amend them.
    current = rows[-1]
    for local, stored in (("label_pass","label_drop_pass"),("retention_pass","baseline_retention_pass"),
                          ("large_pass","large_recall_pass"),("economic_pass","economic_pass")):
        if current[local] != bool(f_assessment[stored]):
            raise ValueError("Overview F decision differs from frozen assessment")
    return pd.DataFrame(baseline), pd.DataFrame(rows), r, family


def plot_tradeoff(rows, baseline, destination):
    """Prespecified full-period points only; originalV1 retention is not guessed."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter
    from matplotlib.patches import Rectangle
    plt.rcParams["font.sans-serif"] = ["Arial Unicode MS","PingFang SC","Heiti TC","DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, axes = plt.subplots(1,2,figsize=(14,6.2),constrained_layout=True)
    fig.patch.set_facecolor("#FFFFFF")
    palette = {"A":"#547CA5","B":"#898598","C":"#BE975C","D":"#96836D","E":"#917AAB","F":"#008C7E"}
    offsets = {"A":(-25,11),"B":(-26,12),"C":(-15,16),"D":(10,-18),"E":(12,7),"F":(11,-15)}
    for ax, metric, target, title in zip(axes,("original_retention","large_recall"),(.9,.8),
        ("原V3及时命中的保留率 / 固定1463个","大幅行情及时召回 / 固定947个")):
        ax.add_patch(Rectangle((.5,target),.5,1-target,facecolor="#DCF1E7",edgecolor="none",zorder=0))
        ax.axvline(.5,color="#788E89",ls="--",lw=1)
        ax.axhline(target,color="#788E89",ls="--",lw=1)
        ax.text(.75,target+(1-target)*.48,"两项同时达标区",ha="center",va="center",fontsize=9,color="#347B63")
        v3_y = 1. if metric == "original_retention" else float(baseline.loc[baseline.version.eq("V3"),"large_recall"].iloc[0])
        ax.scatter(0,v3_y,s=75,marker="s",color="#364958",zorder=4)
        ax.annotate("V3 基准",(0,v3_y),xytext=(9,-16),textcoords="offset points",fontsize=9,color="#364958")
        for row in rows.to_dict("records"):
            color=palette[row["letter"]]
            ax.scatter(row["label_drop"],row[metric],s=100 if row["letter"]=="F" else 65,
                color=color,edgecolor="white",linewidth=.7,zorder=4)
            ax.annotate(row["letter"],(row["label_drop"],row[metric]),xytext=offsets[row["letter"]],
                textcoords="offset points",fontsize=11,fontweight="bold",color=color,
                arrowprops=dict(arrowstyle="-",color=color,lw=.65))
        ax.set_xlim(min(-.04,float(rows.label_drop.min())-.04),max(1.04,float(rows.label_drop.max())+.04))
        ax.set_ylim(-.04,1.07)
        ax.xaxis.set_major_formatter(PercentFormatter(1))
        ax.yaxis.set_major_formatter(PercentFormatter(1))
        ax.set_xlabel("同根合并公开标签减少（相对V3的12042个）")
        ax.set_title(title,loc="left",fontweight="bold",fontsize=11)
        ax.grid(alpha=.16)
        ax.spines[["top","right"]].set_visible(False)
    fig.suptitle("少提示与抓住启动：原V3及六项固定探索",fontsize=17,fontweight="bold")
    fig.text(.5,-.025,"原V1仅列表，不猜旧命中保留率。F主门还须加回未知；图中减少包含公开合并/时移，不等于消除亏损。",ha="center",fontsize=9,color="#697680")
    destination.parent.mkdir(parents=True,exist_ok=True)
    fig.savefig(destination,dpi=160,bbox_inches="tight",facecolor="white")
    plt.close(fig)


def build(validation_sha):
    builder=Path(__file__).resolve()
    relative=str(builder.relative_to(ROOT))
    if subprocess.check_output(["git","show","HEAD:"+relative],cwd=ROOT)!=builder.read_bytes():
        raise ValueError("Commit exact overview builder before rendering")
    if any(path.exists() for path in (MD,HTML,MANIFEST,FIGURE)):
        raise ValueError("Refusing to overwrite an overview or rescore history")
    prepared,validation,f_report,inputs=authenticate(validation_sha)
    baseline,rows,_,family=assemble(inputs)
    plot_tradeoff(rows,baseline,FIGURE)
    passing=rows.loc[rows.all_pass,"letter"].tolist()
    verdict="A—F没有任何一项同时通过四个固定门" if not passing else "固定门通过项："+"、".join(passing)+"；仍不能代替独立前向验收"
    current=rows[rows.letter.eq("F")].iloc[0]
    table,pct,number,integer=report.table,report.pct,report.number,report.integer
    yn=lambda value:"通过" if value else "未通过"
    parts=["# SPIKE V3 降噪总览：原版与六次固定探索",
        f"**{verdict}。** 原V3为了提前发现启动，放宽了原V1的蓄势、密集及单根量价硬门；因此观察数量增多。减少提示必须同时检查错过多少正确启动，不能只看信号更少或个别漂亮截图。",
        f"最新F：公开标签减少{pct(current.label_drop)}，保留原及时命中{integer(current.original_retained)}/1463（{pct(current.original_retention)}），大幅命中{integer(current.large_hits)}/947（{pct(current.large_recall)}）；对应历史独立事件平均净收益{number(current.mean_net_bp)}bp，匹配超额{number(current.matched_excess_bp)}bp。未部署，也未证明原生Pine逐根一致。",
        "## 一眼看取舍",
        f"![原V3和A—F降噪召回取舍]({FIGURE})",
        "绿色区域是各图两项结构目标同时满足的位置；还须另一幅图的门、标签≤6021及正匹配超额/Holm<.01。横轴是公开标签减少，不能直接叫假信号减少。原V1缺少认证的原V3命中保留率，故只列表、不猜散点。",
        "## 原版基准",
        table(baseline,[("version","版本",None),("parents","原信号/父",integer),("labels","合并标签",integer),("timely_hits","1660正例及时命中",integer),("recall","及时召回",pct),("large_recall","947大幅召回",pct),("natural_win_rate","自然退出净胜率",pct),("mean_net_bp","平均净bp",number),("matched_excess_bp","各自匹配超额bp",number),("controls","对照来源",None)]),
        "## 六项机制，没有把失败隐藏",
        table(rows,[("letter","假设",None),("mechanism","唯一新机制",None),("parents","公开父",integer),("children","确认升级",integer),("labels","合并标签",integer),("label_drop","标签减少",pct),("original_retained","保留/1463",integer),("original_retention","保留率",pct),("large_hits","大幅命中/947",integer),("large_recall","大幅召回",pct)]),
        table(rows,[("letter","假设",None),("label_pass","少提示≥50%/≤6021",yn),("retention_pass","旧命中≥90%",yn),("large_pass","大幅召回≥80%",yn),("economic_pass","正超额且Holm6<.01",yn),("all_pass","四门同时",yn)]),
        f"F主门保守加回{integer(current.unknown_original_labels)}个未知所属原标签，保守标签数{integer(current.conservative_labels)}；未知不能帮助过门。F时移、拒绝、未知和早子确认合并逐条分解在其报告，旧参考覆盖不能补进1463及时保留的分子。",
        "## 收益必须带各自随机对照",
        table(rows,[("letter","假设",None),("natural_win_rate","自然退出净胜率",pct),("natural_exits","自然退出",integer),("censored","截尾",integer),("mean_net_bp","净均值bp",number),("matched_actual_bp","匹配事件bp",number),("matched_random_bp","匹配随机bp",number),("matched_excess_bp","配对超额bp",number),("raw_p","冻结原p",lambda x:number(x,5)),("holm6_p","Holm6",lambda x:number(x,5)),("control_schedule","各自对照日程",None)]),
        "**A/B、C、D、E、F使用各自实验冻结的匹配日程，超额不能当作完全相同随机基线下的策略排名。** 每项内部仍是同币×UTC周×因果ATR桶、相同风险退出/20bp的随机入场对照。A—E原p直接读取冻结表，统一展示F已经冻结的Holm6，没有重评分旧策略。原V1控制也是其历史版本。",
        "## 下一步如何使用这些结论",
        "现有证据支持先在呈现上把结构观察、质量升级和参考趋势生命周期明确区分：保留真实发现时点，不让每个观察都长得像新开仓。它能减少重复开仓暗示，但属于交互语义改进，不代表统计上的假信号已经减少。",
        "任何过滤版尚未同时过门时，保持研究状态，不用‘精选/超级趋势’名称冒充成功。下一项研究必须另立机制与事前计划；不继续在同池穷举阈值。即使历史门过，也要未见数据前向验证、真实延迟/成交假设及Pine逐根验证。",
        "## 三张固定F复盘图",
        "下图来自已认证F报告，HYPE/NEAR/PEPE日期固定，每张完整96根；是机制说明，不是收益筛选后的成功案例。",
    ]
    for image in f_report["figures"]:
        parts += ["### "+image["asset"],f"![{image['asset']}固定F复盘]({image['image']})"]
    parts += ["## 风险与诚实声明",
        "同一278个历史OKX合约、1H多头、UTC2026-07-10至09-09共61天，不含BTC/ETH。固定8046锚点：1660正例/6240负例/146未知；947大幅正例。原V3及时命中1463、合并标签12042保持，不以旧持仓覆盖、晚确认或移动时钟修补。原V1不是与V3数量相等的候选池。",
        "这里的正例有固定定义：独立价格突破锚点后24根收盘先达到+4ATR而非-2ATR；大幅子组还要求这24根最高价涨幅至少8%。它们不是人工确认的均线密集启动金标，也不等于按实际入场与退出规则获利的交易。90%保留是本轮检验门，不能解释成未来正确信号不丢的保证。",
        "13项单门加A—F六项顺序探索反复使用已见历史；这是授权研究、每配置第1次消费该配置holdout，不是盲OOS。Holm6没有消除全部研究者选择偏差，也不能证明未来山寨季会有收益。",
        "没有训练新模型，val AUC/训练val样本数不适用；各原报告保留量比/TR描述AUC和top10%毛净收益单特征对照。描述性排序不能替代配对超额。",
        "独立事件可能重叠，未计算账户NAV/最大回撤，不填0；20bp不覆盖全部资金费、滑点和冲击。峰值R与在场覆盖不等于已实现收益。不存在已验证的F原生Pine移植，图表只是存储事件的科学绘图。",
        "## 原报告与可核验证据",
        "、".join(f"[{label}]({ROOT/'analysis/html'/filename})" for label,filename in HISTORY_LINKS)+"。",
        "本轮关键文件："+"、".join(f"[{label}]({OUT/filename})" for label,filename in (("F候选裁决","candidate_registry.csv.gz"),("F实际公开事件","signals.csv.gz"),("F时钟分解","clock_summary.csv"),("F召回","retention_summary.csv"),("F收益","trade_summary.csv"),("F六假设校正","structural_holm_family.csv")))+"。",
        "独立QA："+"、".join(f"[{filename}]({report.EXP/'qa'/filename})" for filename in ("preflight_review.json","independent_review.json") if (report.EXP/'qa'/filename).exists())+"。",
        "### 复现与完整性",
        "本builder先提交；先完成并认证F prepare/evaluate及F图文报告，再用其完整validation SHA运行。只读取已有结果，不重新检测或评分，拒绝覆盖已完成总览。",
        "```bash\n.venv/bin/python -m yoyo.evaluation.spike_burst_v3_noise_overview --validation-sha "+validation_sha+"\n```",
        f"F prepared SHA：{validation['prepared_sha']}；F validation SHA：{validation_sha}。输入/输出/散点图/固定案例图SHA记录在{MANIFEST}。",
    ]
    text="\n\n".join(parts)+"\n"
    MD.write_text(text)
    from scripts.md_to_html import CSS,convert
    converter=ROOT/"scripts/md_to_html.py"
    inputs[str(converter)]=report.sha(converter)
    document='<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    document+="<title>SPIKE V3降噪总览</title><style>"+CSS+"</style></head><body>"
    document+=convert(text,asset_base=MD.parent,embed_images=True)+"</body></html>"
    HTML.parent.mkdir(exist_ok=True)
    HTML.write_text(document)
    for path,digest in list(inputs.items()): report.checked(path,digest,inputs)
    authenticate(validation_sha)
    if subprocess.check_output(["git","show","HEAD:"+relative],cwd=ROOT)!=builder.read_bytes():
        raise ValueError("Overview builder changed during rendering")
    receipt=dict(status="complete",validation_sha256=validation_sha,prepared_sha256=validation["prepared_sha"],
        code_commit=subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),
        builder=str(builder),builder_sha256=report.sha(builder),
        inputs=[dict(path=p,sha256=s) for p,s in sorted(inputs.items())],
        outputs=[dict(path=str(p),sha256=report.sha(p)) for p in (MD,HTML,FIGURE)],
        table_rows=rows.to_dict("records"),baselines=baseline.to_dict("records"),
        plotted_arms=["v3"]+[x[1] for x in ARMS],original_v1_retention_not_inferred=True,
        all_hypotheses_included=True,no_detector_or_scoring=True,no_online_changes=True,
        historical_matched_control_schedules_differ=True)
    MANIFEST.write_text(json.dumps(report.clean(receipt),ensure_ascii=False,indent=2,allow_nan=False)+"\n")
    print(json.dumps(dict(md=str(MD),html=str(HTML),figure=str(FIGURE),manifest_sha256=report.sha(MANIFEST)),ensure_ascii=False))


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validation-sha",required=True,help="Completed authenticated F validation SHA256")
    build(parser.parse_args().validation_sha)

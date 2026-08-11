#!/usr/bin/env python3
"""Render the Owner ETH short-delay contract as a portable Telegram PDF.

Sources are the frozen contract PNG and the V2 review summary.  The PDF is a
reader-facing delivery artifact only; it does not read market data, labels,
model weights, validation rows, or holdout.
"""

from __future__ import annotations

import json
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parents[1]
SUMMARY = ROOT / "analysis/output/owner_eth_target_review_v2_shortdelay/summary.json"
CONTRACT_IMAGE = ROOT / "analysis/reference/owner_ethusdt_15m_semantic_delay_contract_20260811.png"
OUTPUT = ROOT / "output/pdf/owner_eth_shortdelay_boundary_contract_20260811.pdf"
CJK_FONT = Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf")


def footer(canvas, document) -> None:
    canvas.saveState()
    canvas.setFont("CJK", 8)
    canvas.setFillColor(colors.HexColor("#657581"))
    canvas.drawString(16 * mm, 10 * mm, "fable-trading | Owner ETH short-delay contract | 2026-08-11")
    canvas.drawRightString(194 * mm, 10 * mm, f"{document.page}")
    canvas.restoreState()


def build_pdf() -> Path:
    summary = json.loads(SUMMARY.read_text())
    profile = summary["profile"]
    if not CJK_FONT.exists():
        raise FileNotFoundError(f"CJK font missing: {CJK_FONT}")
    pdfmetrics.registerFont(TTFont("CJK", str(CJK_FONT)))
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "TitleCN", parent=styles["Title"], fontName="CJK", fontSize=22,
        leading=29, textColor=colors.HexColor("#14212c"), spaceAfter=6 * mm,
    )
    h1 = ParagraphStyle(
        "H1CN", parent=styles["Heading1"], fontName="CJK", fontSize=15,
        leading=20, textColor=colors.HexColor("#14212c"), spaceBefore=4 * mm,
        spaceAfter=2.5 * mm,
    )
    body = ParagraphStyle(
        "BodyCN", parent=styles["BodyText"], fontName="CJK", fontSize=9.8,
        leading=15, textColor=colors.HexColor("#24333d"), spaceAfter=2.2 * mm,
    )
    bullet = ParagraphStyle(
        "BulletCN", parent=body, leftIndent=5 * mm, firstLineIndent=-3.5 * mm,
        bulletIndent=0, spaceAfter=2.5 * mm,
    )
    status = ParagraphStyle(
        "StatusCN", parent=body, alignment=TA_CENTER, backColor=colors.HexColor("#fff4d6"),
        borderColor=colors.HexColor("#e0a21b"), borderWidth=0.8, borderPadding=6,
        textColor=colors.HexColor("#6a4a00"), spaceAfter=5 * mm,
    )

    doc = SimpleDocTemplate(
        str(OUTPUT), pagesize=A4, rightMargin=15 * mm, leftMargin=15 * mm,
        topMargin=14 * mm, bottomMargin=17 * mm, title="ETH完美平台：竖线内核心与3-5根短延迟合同",
        author="fable-trading",
    )
    story = [
        Paragraph("ETH完美平台：竖线内核心与3-5根短延迟合同", title),
        Paragraph("合同和审查池已纠正；尚未生成新训练金标，尚未训练新模型", status),
        Paragraph("Executive Summary", h1),
        Paragraph("• <b>上一版框确实偏了。</b> 两条青色竖线之间的平台/转折段才是核心，本例约6根K；右侧快速下跌不进入红框。", bullet),
        Paragraph("• <b>10根延迟已经撤销。</b> 核心结束后只允许3-5根确认：3根优先，5根硬封顶，6-10根退出新训练与验收。", bullet),
        Paragraph("• <b>输入窗口不固定。</b> 从最短充分上下文开始动态变化；首轮只试约14-22根，再按precision继续向更短收缩。", bullet),
        Paragraph("• <b>旧资产保留但不能直接训练。</b> delay3-5旧候选只有316张，right位置带占83.86%；Stage A权重仅作初始化底座。", bullet),
        Spacer(1, 2 * mm),
        Image(str(CONTRACT_IMAGE), width=180 * mm, height=120 * mm),
        Spacer(1, 4 * mm),
        Paragraph("Owner裁决：红框只包两条青线之间的核心；第3根优先确认，第5根硬封顶。", status),
        PageBreak(),
        Paragraph("红框与短延迟合同", h1),
    ]

    contract_rows = [
        ["项目", "被撤销的上一版", "当前合同"],
        ["核心边界", "向右偏，包入启动后下跌", "只在Owner两条竖线之间，本例约6根K"],
        ["核心宽度", "机械沿用旧5/7根", "语义约4-7根；旧框只是待复核提案"],
        ["输入窗口", "固定20-30根", "动态最短充分上下文；首轮约14-22根"],
        ["框后确认", "0-10根", "3-5根；3优先、5硬封顶"],
        ["框位置", "人为覆盖宽位置带", "随最短充分上下文自然变化"],
    ]
    contract_table = Table(contract_rows, colWidths=[31 * mm, 63 * mm, 86 * mm], repeatRows=1)
    contract_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "CJK"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.6),
        ("LEADING", (0, 0), (-1, -1), 12),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#14212c")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#c8d3da")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f8fa")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.extend([
        contract_table,
        Paragraph("旧候选收紧后仍有右侧偏置", h1),
        Paragraph("沿用修复后的Stage-A时间split，只检查旧Owner提案中框后正好3-5根的事件。结果证明旧W20-30图只能用于审查。它不符合新的动态短窗训练合同。", body),
    ])

    audit_rows = [
        ["检查项", "结果", "裁决"],
        ["Stage-A联结", "2,378 / 2,378", "完整，无orphan"],
        ["Stage-A val排除", str(profile["stage_val_rows_excluded"]), "未参与候选选择"],
        ["delay3-5候选", str(profile["eligible_train_events"]), "审查母池，不是训练集"],
        ["delay3 / 4 / 5", "94 / 107 / 115", "三档均有覆盖"],
        ["旧框5根 / 7根", "171 / 145", "仅作边界提案"],
        ["middle / right / far-right", "36 / 265 / 15", "right占83.86%"],
        ["旧路径含images/val", "45 / 316", "历史错split目录名；Stage-A均为train"],
        ["holdout读取", "0", "未消耗holdout"],
    ]
    audit_table = Table(audit_rows, colWidths=[66 * mm, 45 * mm, 69 * mm], repeatRows=1)
    audit_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "CJK"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.7),
        ("LEADING", (0, 0), (-1, -1), 12),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#14212c")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#c8d3da")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f8fa")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.extend([
        audit_table,
        Paragraph("V2审查页同时审形态和边界", h1),
        Paragraph("从316张旧候选中确定性抽取200张：delay3/4/5分别80/65/55。选项为“形态和框都准”“形态像但框要改”“不是目标”。Owner裁决前全部保持semantic_status=unreviewed、geometry_status=unreviewed、training_eligible=false。审查样本中30张旧路径含images/val；修复后的Stage-A split均为train。", body),
        Paragraph("下一步", h1),
        Paragraph("1. 冻结第一批形态和框都准确的正例；边界有偏差的样本先重标。", body),
        Paragraph("2. 重新渲染动态短窗：先试6-10根框前上下文，框后只保留3-5根；同事件禁止跨时间split复制。", body),
        Paragraph("3. 构建同时间块、同窗口长度分布的真实空背景与难负例，Stage A best.pt只作初始化。", body),
        Paragraph("4. 单变量比较短窗档位，分别报告delay3/4/5的event precision、recall、FP/1000和首次命中，选择满足精度要求的最短窗口。", body),
        Paragraph("诚实声明", h1),
        Paragraph("本轮未读取holdout，未进行交易回测，未修改阈值、成本、障碍参数、ACTIVE或forward配置；未训练、未晋升、未部署。AUC、置换检验、top-decile收益和匹配随机对照组均为N/A。", body),
    ])

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return OUTPUT


if __name__ == "__main__":
    print(build_pdf())

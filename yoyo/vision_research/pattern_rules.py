"""Carry existing project morphology semantics into visual review prompts.

Sources (definitions/counterexamples, not validated trading thresholds):
- analysis/p1_spike_ma_density_20260920.md, sections 5 and 7.
- analysis/p0_15m_ma_launch_owner_strict_review50_20260827.md.
- HANDOFF.md, 2026-09-04 model-first breakout audit: launching is not necessarily
  the first crossing of all six moving averages.
- docs/protocol/local_signal_v2.md for core/launch separation only. Its 3-5 bar
  retrospective confirmation window is NOT a current-edge review requirement.
- exp-spike-gemini-vision-20260923-v1/references_v3.json and its versioned manifest.

No indicators, thresholds, future features, or new labels are computed here.
Reference descriptions are attached by normalized PNG hash, never by filename.
"""

DEFAULT_CRITERIA = """按项目既有的双均线密集启动语义，审阅最右端当前可见形态：
1. 均线对象是 SMA20、EMA20、SMA60、EMA60、SMA120、EMA120。按图例区分三组周期，检查整体及组间是否靠拢；只有20组内部两条线偶然交叉、60/120组仍明显分离，不能当作整体收拢。
2. 密集核心是启动前连续的局部紧凑区，观察六线是否相对前段靠拢、局部交织，以及K线与线束的相对位置；由散到拢的过程可见时描述，不猜测窗口外过程，也不把必须看见完整收窄过程设为额外硬门。交叉次数不能代替几何接近；单纯平行、宽束或价格早已离束不是充分证据。窄而平行只能说明紧凑，仍需检查局部结构与新鲜启动；不能硬要求核心每根实体都穿过所有均线，也不能因为某图“相对最窄”就强行找框。
3. 启动是在核心之后、与该核心保持结构关联的有方向离束/释放，区分核心、第一根启动K线和后续延伸，不自行规定固定间隔。不能机械等同于第一次穿越完整六线；价格原已在目标侧时，仍要检查局部收拢后的新鲜释放。启动后的大K线不能反过来充当先前密集的证据。
4. 判断当前状态：密集但未启动为不确定；当前仍在紧邻核心的启动阶段才可符合；已明显远离核心、持续发散或仅有左侧旧形态则不符合。不能仅因启动K线已收盘或又出现一根K线，就自动认定已经走远。依据是当前结构与核心的连续关系，不自行设置统一根数、百分比或ATR阈值。
5. 框只包含启动前密集核心的K线与六均线范围，右边界止于第一根启动K线之前，不包含启动大K线、后续涨跌，也不照搬参考图的位置和宽度。找不到可靠核心可不画框，不能为出框而降低形态要求。
6. 说明可观察的支持与反对证据；能可靠定位时指出核心结束、第一根启动及距当前几根，不能定位就明确未知。SPIKE信号确认时间、形态启动时间和本次观察时间分别理解；信号后12根只是复查范围。最右未收盘K线只能描述当前可见状态，不称已收盘确认；不得用信号之后的走势倒推信号当时已经成立。
7. 本标准不以成交量、订单簿或精确价格为必要条件；没有这些信息不能单独成为否定或不确定的理由。只列会影响上述判断的证据缺口，不猜图中看不清的价格，不用未来盈利或后续大涨跌认定形态。"""

# These hashes identify the exact metadata-free images sent by the workbench.
# Existing retrospective references stay references; neither selection reasons
# nor the old FIL semantic endorsement confer approval on the new box geometry.
_REFERENCE_NOTES = {
    "56dba06ef8adda3a7cee16dc5d95d5179c74a3846a0afba036157de462259340":
        "AUCTION：观察窄均线束与启动前4根小K线；第一根向上释放在框外。",
    "19f80aacb22107e446766ae0a0008bffd50d4b58a1964bfd1869b5a28d56e2e2":
        "CHZ：观察紧凑均线束与启动前5根小K线；向上释放与核心分开。",
    "d84a4cd1247f980cec9df86ee5fd60ee25e0a0d9096d7e81d22cee54ba25d00d":
        "FIL：原样本形态曾获Owner认可；当前框左侧收紧1根、保留启动前4根核心，第一根大幅向下释放在框外。原形态认可不等于本次新框坐标已确认。",
    "c59ebb96552699ea624c54ed9804cc5dde35a6c796a718f576affcc5a799ebfc":
        "TRUST：框左侧收紧1根、保留启动前4根核心；第一根明显向下离束在框外。",
    "b7f9671c4aaa41a31af57810492be85b3e0e0d33ce7206a6216c20d28ae8124e":
        "JELLYJELLY：观察收拢线束下方的5根小平台K线，随后的大阴线启动单独留在框外。",
}


def reference_note(sha256: str) -> str:
    """Return provenance-backed guidance only for an exact known reference."""
    note = _REFERENCE_NOTES.get(sha256)
    if note is None:
        return ""
    qualification = ("" if sha256 == "d84a4cd1247f980cec9df86ee5fd60ee25e0a0d9096d7e81d22cee54ba25d00d"
                     else " 本图形态是助手筛选的提案，尚未获Owner逐样本认可。")
    return (note + qualification + " 本图为回顾性外观示例，当前框坐标仍待逐样本确认；"
            "示例根数不是统一准入门槛，框后涨跌不作为待判图证据。")

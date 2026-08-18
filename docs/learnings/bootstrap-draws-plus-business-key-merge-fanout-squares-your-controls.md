# bootstrap 抽样 + 按业务键 merge = 平方膨胀,对照组静默失真

- **问题**:yoyo-eth 的 `matched_random_control` 用 `replace=True` 抽随机对照
  (合法 bootstrap),再交给 `add_labels`,而后者用 `events.merge(labeled,
  on="decision_pos")` 拼标签。`decision_pos` 被抽中 k 次 → merge 两侧各 k 行 →
  输出 k×k 行。44×20=880 的对照组实报 1076,978 应 800——重复位置拿到平方权重,
  对照均值偏向被重复抽中的 bar。
- **死胡同**:两轮对抗性审查(每轮都逐行验证了 labels 的算术与因果性)都没抓到——
  因为审查视角盯着"真实事件"路径,真实事件的 decision_pos 天然唯一,merge 恰好
  无害;bug 只在"允许重复键的调用方"(bootstrap 对照)进来时发作。**join 的正确性
  取决于调用方的键分布,单看被调函数验不出来。**最后是 owner 侧外部复核对着
  44×20=880 的应然数对账抓到的:报告里的 1076 一直摆在那,只是没人做这道乘法。
- **有效路径**:行级主键。`events["event_row_id"] = np.arange(len(events))`,标签
  按唯一 pos 计算一次、按 row_id 回贴,`merge(on="event_row_id",
  validate="one_to_one")`,再加运行时断言 `actual == n_events × n_per_event`,
  不等直接 RuntimeError 让整轮失败。
- **通用规则**:①任何可能含重复键的 frame,禁止拿业务键当 join 键——先造行级
  主键,merge 一律带 `validate=`。②对照组/重采样的产出行数是**可事先算出的应然
  数**,必须用断言钉死,而不是报告里顺手一贴。③审计一个函数时,问一句"哪个调用
  方会送重复键进来?"
- **牵连**:`/Users/zhangzc/yoyo-eth/src/yoyo_eth/labels.py`(event_row_id)、
  `evaluate.py`(requested==actual 断言)、tests 11.1/11.2;P02 walk-forward 的
  per-fold 对照数字带同一偏差未重跑,已在 ITERATION_V1 报告声明。相关:
  [rolling-max-of-cumulative-streak-is-not-in-window-streak](rolling-max-of-cumulative-streak-is-not-in-window-streak.md)
  ——同一项目三轮审查,每轮漏的都是"语义正确性依赖调用上下文"这类 bug。

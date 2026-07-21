# dense_owner stem 数字是窗末 bar，不是窗起点

- **问题**：pad200 左补后，ORIG 青框是向上突破，PAD 绿框却罩住另一段 K 线（HOME 对照一眼错）。
- **死胡同**：只在「cut/global 算术」里打转——`cut_global = win_start + cut_local` 公式本身没错；用当前 kline 自洽时 ORIG/PAD close 相关还是 1.0，但 `win_start` 已经指错窗，自洽只能证明「错窗内部一致」。
- **有效路径**：把存档 PNG 与候选窗重渲做像素 MAD：`end_incl`（`win_start = idx - 199`）MAD=0，`start`（`win_start = idx`）明显更差。根因是 `find_window_start` 优先把 stem 数字当窗起点，而 round8/9 / dense_owner_v11 约定是窗末（`iloc[idx-199:idx+1]`）。
- **通用规则**：从 owner 图回算 series 时，先核对 stem 约定（end vs start）；有存档图就用重渲 MAD 消歧，不要信「索引合法就当 start」。
- **牵连**：`scripts/build_crop_pad200_dataset.py`（`resolve_win_start`）；同源坑 `scripts/build_htip_dataset.py` / `tip_detectability.py` 的 `find_window_start`；约定说明见 `scripts/build_hts_dataset.py`。

# 目录级 .gitignore 之下，所有 `!` 否定规则都是死的

- **问题**：`.gitignore` 第 5 行是 `datasets/`（目录级），于是数据集的
  manifest / summary / audit 每次都要 `git add -f` 才进得来（4b5f48b、aa352a0
  两次提交都是这么进的）。写过 `!datasets/*/manifest.jsonl` 想放行，没有任何效果。

- **死胡同**：以为否定规则不生效是**写法**问题——试 `!datasets/**/manifest.jsonl`、
  试把否定行挪到排除行后面（顺序本来就是对的）、试更具体的 `!datasets/foo/manifest.jsonl`。
  全都没用，因为问题根本不在模式匹配上。

- **有效路径**：git 的 pathspec 规则里有一句决定一切的话——
  **被排除的目录，git 不会下降进去**。`datasets/` 排掉的是那个*目录*，
  git 连 `datasets/foo/manifest.jsonl` 这个路径都不会枚举出来，
  自然轮不到任何 `!` 规则去匹配它。否定规则只能救「父目录仍可见」的文件。
  修法只有一条：**别排除目录，排除目录里的东西**——
  把 `datasets/` 换成 `datasets/**/images/`、`datasets/**/labels/` 这样的子树规则，
  数据集根目录重新可见，`datasets/*/manifest.jsonl` 就自然能 `git add` 了。
  （对照：仓库里 `!models/owner_*.pt` 一直有效，因为 `*.pt` 排的是**文件**不是目录。）

- **通用规则**：写下 `!something` 之前，先跑
  `git check-ignore -v <那个文件>` ——如果输出里的规则是一条**以 `/` 结尾的目录规则**，
  你的否定规则永远不会被求值，改写法是白费力气，只能把目录规则拆细。
  推论：凡是「这个目录里大部分不入库，但元数据要入库」的需求，
  第一版就不要写目录级排除。

- **牵连**：
  - `.gitignore:4-26`（2026-08-10 改写；原第 5 行 `datasets/`）
  - 同批删掉的 `datasets/dense_owner_short_star_tip_v10/`（同一个病的第二例）
  - 拆细规则时**不能只按目录名拆**，见
    [按布局写忽略规则会漏掉换了布局的数据集](layout-based-ignores-miss-the-datasets-with-a-different-layout.md)
  - 被这条 bug 拖累的具体产物：`datasets/*/manifest.jsonl`、`w20_summary.json`、
    `manifest_audit.json` —— 参见
    [可复现性要分轴验证](reproducibility-is-per-axis-not-a-boolean.md)

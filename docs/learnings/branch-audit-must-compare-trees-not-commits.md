# 分支审计要比树，不能比提交

**2026-07-30**，清理 7 个陈旧分支只保留 main。

## 现象

`git log main..<branch>` 说 5 个分支「0 个 main 里没有的提交」、2 个分支有独有提交。
按这个结论删，会丢东西吗？看着不会。**实际两头都错。**

## 为什么提交数会骗人

**并行会话把内容 cherry-pick / rebase 进了 main**，SHA 变了内容没变。
`claude/heuristic-pike-d21067` 的 Optional[] 修复以 `2ba1b82` 进了 main，
原提交 `99ad27c` 仍显示为「main 里没有」。**按提交数看是 1 个未合并，按内容看是 0。**

反过来，`git diff main...<branch>`（三点）从 merge-base 出发，
也照样把「分支加过、main 后来独立加过同样内容」的改动列成差异。

## 唯一可信的问法

**「哪些文件只存在于分支、main 里没有」**：

```bash
git ls-tree -r --name-only main | sort > /tmp/main.txt
git ls-tree -r --name-only $b  | sort > /tmp/b.txt
comm -13 /tmp/main.txt /tmp/b.txt      # 分支独有
```

这一问揭出 52 个文件，而 `git log` 说其中 5 个分支「什么都没有」。

## 但 52 个里 51 个是假的，第二层陷阱：路径搬迁

`comm` 比的是**路径**，而 main 三周里搬过大量文件：

| 「分支独有」 | 其实 main 有，在 |
|---|---|
| 13 个 round/train 脚本 | `scripts/_archive_pretip/` |
| 3 个 planning docs | `docs/archive/` |
| 2 个 mock html | `docs/design/` |
| `DESIGN.md` `OFFLINE_RESULTS.md` | `docs/design/` `docs/archive/` |

**我一度真按「main 缺这 3 个 docs」把分支旧版覆盖了 main 的新版**，
`git status` 显示 `M` 而不是 `A` 才暴露——**`A` 才是新增，`M` 意味着你正在覆盖**。
这是本次唯一造成实际损害的动作，靠 `git checkout --` 回退。

所以第三问是必须的：**按 basename 找，而不是按全路径**。

```bash
git ls-tree -r --name-only main | grep -E "/$basename$"
```

## 第三层：main 故意不要的东西，不能「抢救」回去

- 根目录 `NEXT_STEPS.md`(399 行) / `PROJECT_STATUS.md`(110 行)：
  main 把它们缩成 12 行/10 行指针，理由写在文件里——
  「同一项目出现了三份互相矛盾的当前状态」。**恢复它们等于把已修好的病重新引入。**
- `models/owner_best.json`：v11 的 frozen-F1 排行榜，而铁律 12 明文废掉了
  frozen-F1 作为晋升门。**它不在 main 是决策，不是遗漏。**

**判断「该不该救」不能只看 main 有没有，要看 main 为什么没有。**

## 第四层：能跑的测试才是资产

4 个测试看着该救（含一个 `test_dashboard_holdout_guard`，听着是铁律 1 的守卫）。
实跑：**10 个断言全红**。`test_ma206_runtime_paths` 要求代码无 `EMA55/EMA21` 遗留引用，
而 main 的 `src/scout_mtf/*`、`scripts/h13_btc_regime.py` 里就有；另两个测的是
正在退役的 VPS 部署契约。**加进去等于交付一个 10 个失败测试的仓库，比没有更糟。**

先跑再决定，别按文件名判断价值。（那个 legacy EMA 引用是真的不一致，
但该单独查，不该由一个陈旧测试来喊。）

## 删之前问「有什么不可再生」

52 个文件里最后只留了 1 个：`grok_tasks/RESULTS.md`——一夜测了 10 个假设、
8 个 dead 并附 IC 数字。它引用的 4 份分析报告 main 都有，但**合并成一张
「这些已死、别重测」的清单值那 117 行**，这正是本仓库纪律要求留存的东西。

工作树里 `data/` `datasets/` `runs/` `.venv` 多数是**指向主仓的软链**，
`git worktree remove --force` 只删链不动真数据——但这必须**先验证再删**：

```bash
[ -L "$p" ] && echo "软链 -> $(readlink $p)"
```

唯一真正紧张的是 43M 的 `output/label_studio`（owner round1~7 的人工标注导出）。
逐文件核对：**分支独有 0 个、内容不同 0 个，main 是超集（90 vs 84）**。
只有核到这一步才能按删除键。

## HEAD 漂移：一个差点丢掉 6 个提交的事故

同一天发现：**并行会话在 02:09 把 HEAD 从 main 切到了
`codex/eth3m-v2-dataset-diagnosis`，我随后 6 个提交全落在那个分支上，我毫无察觉。**

没丢是因为我一直用 `git push origin HEAD:main` 显式指定目标。
**如果写的是裸 `git push`，那 6 个提交会推到那个分支，owner 在 main 上什么都看不到。**

已写进 CLAUDE.md 铁律 13：只有 main，提交前先 `git branch --show-current`。

## 复现

```bash
scripts/  # 无需脚本，全部是 git 命令，见上文各段
```

被删的 7 个分支尖端 SHA 记在提交 `chore: only main from here on` 的正文里，
GitHub 与本地 reflog 在 GC 前仍可按 SHA 取回。

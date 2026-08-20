# 如何执行归档（**待 owner 执行，尚未执行**）

任务书 §9 C7.5：**只有在 fable-trading 最终验收通过、分支已推送并形成 PR 后**，
才执行来源仓的归档 commit。**不要删除任何来源仓。**

本目录下四个 `.md` 是各仓 README 顶部要插入的段落。逐仓：

```bash
cd ~/<repo>
git switch -c archive/consolidated-into-fable-trading
# 把 reports/consolidation/archive_readmes/<repo>.md 的内容插到 README.md 最顶部
git add README.md
git commit -m "ARCHIVED: consolidated into darkforest-x/fable-trading"
git push -u origin HEAD
```

四个仓：`darkforest-one`、`yolo-xx`、`yoyo-trading`、`yoyo-eth`。

归档后建议在 GitHub 上把仓库设为 Archived（只读），
但**不要 delete**——`experiments/historical/` 里多条 `REFERENCE_ONLY` 登记
（3060 权重清单、2.4 MiB 的 legacy 标签迁移逐行审计）按 commit + SHA 指回这些仓。

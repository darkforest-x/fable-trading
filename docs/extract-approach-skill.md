# extract-approach skill（待安装）

> 安装方法（需项目所有者执行，Claude 的自动权限不允许自行创建 skill）：
>
> ```bash
> mkdir -p .claude/skills/extract-approach
> cp docs/extract-approach-skill.md .claude/skills/extract-approach/SKILL.md
> # 然后删掉本说明块和这段注释，保留 frontmatter 起的正文
> ```

---
name: extract-approach
description: 在解决非平凡问题（修复 bug、架构决策、反直觉实验结论）之后，把解决思路蒸馏成一条 learnings 笔记写入 docs/learnings/。CLAUDE.md 的 learning law 要求每次非平凡解决后自动执行本 skill。
---

# extract-approach

在 `docs/learnings/` 下新建一条笔记，kebab-case 文件名，一个问题一个文件。

## 笔记格式

```markdown
# <一句话标题>

- **问题**：什么现象 / 什么任务（一两句）
- **死胡同**：先试了什么、为什么不行 ← 这部分最值钱，别省略
- **有效路径**：最终怎么解决的，关键判断是哪一步
- **通用规则**：下次遇到同类问题，第一步做什么
- **牵连**：涉及的文件 / 参数 / 外部约束
```

## 规则

- 一个洞见一条笔记；宁可五条短的，不要一条长的（原子化才能被检索复用）
- 写"为什么"，不写"做了什么"——git log 已经记录了做了什么
- 如果洞见修正了 CLAUDE.md 的某条规则，同步更新 CLAUDE.md
- 笔记之间可以互相引用（相对链接）

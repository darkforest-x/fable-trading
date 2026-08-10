# 按目录布局写忽略规则，会漏掉换了布局的那几个数据集

- **问题**：把 `.gitignore` 里的 `datasets/` 拆细时，最顺手的写法是
  `datasets/**/images/` + `datasets/**/labels/`——YOLO 的标准布局就长这样。
  照这个写完，`git status` 看起来是干净的（因为 `--short` 会把未跟踪目录**折叠成一行**，
  `?? datasets/eth_3m_v10_prebox200/` 底下藏着什么根本看不见）。

- **死胡同**：用 `git status --short` 验收。它折叠目录，
  9 万个文件可以缩成 14 行，肉眼完全看不出泄漏。必须 `git status --short -uall` 展开。

- **有效路径**：先枚举**实际持有大文件的目录**，再写规则，而不是反过来：
  ```bash
  find datasets -type f \( -name '*.png' -o -name '*.jpg' \) | sed 's|/[^/]*$||' | sort -u
  ```
  18 个数据集里只有 11 个是 `images/` + `labels/`；另外 4 个用的是
  `causal_images/` `review_images/`、分类布局 `train/<类名>/`、`weak_or_review/`，
  还有 15MB 的 `*_mobile.html`（base64 内嵌整套审阅图）。
  只写 images/labels 两条规则会放进 **884 张图 / 123MB**。
  最终写法是**双保险**：子树规则（让 git 直接跳过，`git status` 仍是 0.046s，
  不用走 95,890 个文件）+ 扩展名规则 `datasets/**/*.{png,jpg,npy,txt,cache}`
  （新数据集换任何目录名都照样挡住）。**目录规则管性能，扩展名规则管正确性。**

- **通用规则**：忽略规则要按**体积/类型**写，不按**目录名**写；
  目录名只是当前 18 个数据集碰巧长成这样，下一个 builder 换个名字就绕过去了。
  验收一律用 `git status --short -uall`，并且加一条硬检查：
  ```bash
  git status --short -uall | grep -E '\.(png|jpg|npy|cache)$' && echo LEAK
  git add --dry-run -A datasets/ | wc -l   # 看真实会 stage 多少
  ```

- **牵连**：
  - `.gitignore:4-26`（2026-08-10）
  - 非标准布局的四个数据集：`eth_3m_v10_prebox200`、
    `eth_3m_entry_timing_calibration30`、`eth_3m_short_pilot_v2`、
    `eth_3m_short_pilot_v2_cls_letterbox960`
  - 起因见 [目录级 .gitignore 之下否定规则都是死的](directory-level-gitignore-kills-every-negation-below-it.md)

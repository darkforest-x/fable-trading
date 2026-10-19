# pip 解析通过不等于框架关键模块可运行

- **问题**：2026-09-07 安装 Freqtrade 2026.8，PyPI 默认依赖组合通过安装和 `pip check`，但 Hyperopt 实际导入失败。
- **死胡同**：宽松依赖允许选入 joblib 1.6.0；它不再提供框架所用的 `joblib.externals.cloudpickle`。`--version`、子命令 `--help` 与依赖元数据校验均不足以发现这条延迟导入路径。
- **有效路径**：读取 [Freqtrade 2026.8 官方 requirements](https://github.com/freqtrade/freqtrade/blob/2026.8/requirements.txt)，在专属 venv 内重新 dry-run，再对齐官方发布依赖组合（joblib 1.5.3）。实际导入 Hyperopt、Backtesting、ApiServer，验证 TA-Lib 计算及 `pip check` 后才宣告成功。
- **通用规则**：安装有回测、优化等可选功能的框架，要执行关键模块的真实导入或最小离线运行；出现问题先核对同一发布版本的官方依赖组合。
- **牵连**：独立环境 `~/.local/share/crypto-toolkit/venvs/freqtrade`；失败证据 `install-evidence/freqtrade-verification.log`，通过证据 `install-evidence/freqtrade-verification-final.log`，两者均在 Toolkit 目录内。没有改 `constraints-ci.txt` 或项目主 venv。

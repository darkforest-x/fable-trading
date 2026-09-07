# 审计补强不能倒改原始产物的构建来源

- **问题**：V34真实覆盖已由49c0d8c完成，随后复核发现未行经的ZIP网络异常分支及delta二次校验需要补强。
- **死胡同**：改完代码就改原summary的source_commit，会让旧输出看似由未来代码生成；直接松开源码hash门也会掩盖范围或parser变化。
- **有效路径**：原manifest/summary不动。修补仅审计行为，先提交新版校验代码，独立verify复跑同24份本地档案，核对原提交blob、固定parser/config和所有月字段/CSV哈希，另存verification收据。
- **通用规则**：修复版本与产出版本分别记；只有重新运行并逐字段相等后，才能说补强校验支持原结论，不得声称原实验事先用了修复后的代码。
- **牵连**：yoyo/evaluation/binance_flow_coverage.py的verify路径；V33 parser、日期、成本和交易逻辑均保持不变。ZIP网络分支本次24份原缓存未行经，合成失败注入补测。

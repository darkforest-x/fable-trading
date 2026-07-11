# LightGBM / CatBoost / XGBoost / Ensemble 影子比较

**日期**：2026-07-11

**实验边界**：相同 MA206 数据、相同 28 维特征、相同时间切分、相同 purge、相同 top-decile
经济性口径；未读取 holdout，未保存 challenger 模型，未修改 ACTIVE、阈值或任何前向账本。

## 结论

- **LightGBM 继续作为 ACTIVE。** 它已有完整冻结、解释、指纹和前向链路，单条本地评分最快；
  本轮没有足够证据承担迁移成本。
- **XGBoost 是唯一值得进入独立前向账本的 challenger。** 旧 val 上 AUC 与 LightGBM 几乎
  相同，但 top-decile 毛收益较高；扣 0.2% 后仅 `+0.002%`，经济上仍接近零，不能晋升。
- **CatBoost 暂不进入前向。** 当前特征全部为数值，类别特征优势未被利用，旧 val 的 AUC
  和净收益均弱于 LightGBM。
- **当前 Ensemble 拒绝。** 三个基础模型分数 Spearman 为 `0.852–0.898`，错误高度相关；
  等权 soft vote 没有改善经济性，却把单条推理延迟提高到约 `1.31 ms`。

## 框架优缺点

| 框架 | 优点 | 缺点 | Fable 判断 |
|---|---|---|---|
| LightGBM | 数值 tabular 成熟；训练和单条推理快；原生缺失值、特征重要性和 contribution；现有冻结链路完整 | leaf-wise 在样本不大时更容易过拟合，需要 `num_leaves/min_child/max_depth` 约束；当前标签仍与净收益错位 | **保留 ACTIVE** |
| CatBoost | Ordered boosting 可降低小数据目标泄漏风险；`has_time=True` 可保留对象顺序；对称树推理快；类别特征处理强 | 当前 28 维没有类别特征；包和模型更重；单条 Python 推理略慢；参数语义与 LightGBM 不完全等价 | **保留研究位** |
| XGBoost | `hist` 训练快且正则化控制直接；sklearn 接口 early stopping 清晰；`inplace_predict` 可绕开 DMatrix 构建；部署生态成熟 | 默认 depth-wise 可能需要更仔细的深度/叶重约束；native Booster early stopping 推理必须显式限制 iteration；本轮 best iteration 仅 5，需新鲜前向确认不是阶段偶然 | **进入独立 shadow** |
| Ensemble | 可降低单模型方差；固定等权易审计；个别模型失效时可能更稳 | 只有错误互补才有效；概率尺度不同需校准；stacking 极易在时间序列上泄漏；延迟和运维成本相加 | **当前拒绝** |

LightGBM 官方说明 leaf-wise 收敛快但小数据更容易过拟合；CatBoost 官方提供 `has_time` 保留
对象顺序，并指出默认对称树具有快速推理特性；XGBoost 官方说明 `inplace_predict` 可避免
构造 DMatrix，但 native Booster 在 early stopping 后需显式指定 best iteration；scikit-learn
建议 soft voting 用于概率已校准的分类器。来源：
[LightGBM](https://lightgbm.readthedocs.io/en/latest/Parameters-Tuning.html)、
[CatBoost time/order](https://catboost.ai/docs/en/references/training-parameters/common)、
[CatBoost symmetric trees](https://catboost.ai/docs/en/concepts/parameter-tuning)、
[XGBoost prediction](https://xgboost.readthedocs.io/en/stable/prediction.html)、
[VotingClassifier](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.VotingClassifier.html)。

## 同口径实测

数据：12,032 train / 3,030 val；本机 Apple Silicon；下表延迟包含 pandas 到模型适配器的
Python 开销，只用于同机相对比较。

| 模型 | best iter | AUC | PR-AUC | top 10% 毛收益 | 净收益@0.2% | 胜率 | 单条 ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| LightGBM | 32 | 0.5775 | 0.3245 | +0.131% | -0.069% | 34.65% | 0.266 |
| CatBoost | 33 | 0.5731 | 0.3191 | +0.119% | -0.081% | 33.99% | 0.397 |
| XGBoost | 5 | 0.5774 | 0.3268 | +0.202% | +0.002% | 36.96% | 0.626 |
| 等权 Ensemble | - | 0.5763 | 0.3253 | +0.141% | -0.059% | 35.31% | 1.315 |

`+0.002%` 不是可交易优势：它远小于滑点、maker 排队和资金费误差，而且来自已反复使用的
旧 val。完整机器输出见 `analysis/output/shadow_booster_benchmark.json`。

## 影子迁移方案

### 第一阶段：固定 XGBoost challenger

1. 继续使用现有 28 维特征、候选规则、TP/SL、成本和时间切分，只替换模型算法。
2. 用 val q90 生成 **XGBoost 自己的冻结影子阈值**，不复用 `0.340933` 的概率数值。
3. 模型和元数据写入新的 `models/shadow_xgboost_*`，不得修改 ACTIVE 指针。
4. 新建 `data/forward_log_xgboost_shadow_ma206.csv`，候选和退出逻辑与主线一致。
5. 达到至少 100 笔 closed 后，比较同窗口净收益、PF、成交率、分数漂移和延迟；此前不晋升。

### 第二阶段：只在有互补证据时集成

如果 XGBoost 与 LightGBM 的新鲜前向收益残差相关性明显下降，再做固定等权 ensemble。
不得在旧 val 上学习权重。Stacking 必须使用按时间生成的 base-model OOF 分数训练 meta model，
并在更晚的完整窗口评估；否则 meta model 会看到同一批标签，形成隐性前视。

## 可执行代码

### 特征工程

```python
enriched = add_indicators(kline_frame)  # SMA/EMA 20/60/120 + causal rule fields
signal_indices = forward_candidate_indices(enriched)
featured = add_features(enriched)       # 28 causal features
x_live = extract_feature_rows(featured, signal_indices)[FEATURE_COLUMNS]
```

### 同口径训练

```python
train, val = load_shadow_splits(dataset_path)  # physical pre-holdout stop + purge
result = run_shadow_benchmark(
    train,
    val,
    ("lightgbm", "catboost", "xgboost", "ensemble"),
)
```

隔离运行，不污染主 `.venv`：

```bash
PYTHONPATH=. uv run scripts/benchmark_shadow_boosters.py
```

### 实时推理

```python
lgb_score = lgb_booster.predict(x_live, num_iteration=lgb_best_iteration)
cat_score = cat_model.predict_proba(x_live)[:, 1]
xgb_score = xgb_model.predict_proba(x_live)[:, 1]  # sklearn API uses best_iteration

# Only after forward calibration and complementarity evidence.
ensemble_score = np.vstack([lgb_score, cat_score, xgb_score]).mean(axis=0)
```

完整训练参数、early stopping、计时和输出契约在
`src/judgment/shadow_boosters.py`；入口在 `scripts/benchmark_shadow_boosters.py`。

## 风险与诚实声明

- 这是算法单变量诊断，不是新模型验收；val 已复用，结果只能决定“是否值得前向”。
- 所有模型仍学习 TP/SL 二分类标签，未解决“分类目标与扣费收益目标错位”的根因。
- 本轮没有测试 VPS 并发吞吐；本机所有单模型延迟都低于 1 ms，当前瓶颈不在推理速度。
- 没有读取 holdout，也没有保存或晋升任何 challenger。

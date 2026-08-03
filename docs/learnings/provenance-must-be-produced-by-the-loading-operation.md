# 溯源必须由实际加载操作产生

- **问题**：manifest 可以声明图片路径与模型 SHA，但真实运行对象未必就是被声明的对象。本轮真实 Ultralytics 烟测中，路径列表输入被改名为 `image0.jpg`；最初的 L2 bridge 也允许任意 booster 只靠自报 `model_sha256` 冒充 artifact 模型。单测都能绿，身份链仍然可能是假的。
- **死胡同**：按模型返回顺序把结果配回输入；或给已构造的模型对象附一个调用者提供的 hash，再与文件 hash 比较。这两种方式都在操作结束后“补标签”，没有证明实际推理读取了那张图、实际打分加载了那个模型文件。
- **有效路径**：YOLO 对每张图片做隔离调用，要求每次恰好返回一个结果且 `result.path` 精确等于该输入；LightGBM 只经显式路径工厂加载，在同一次操作中执行加载前 stat/hash、从该路径构造 booster、加载后 stat/hash，并把不可混用的 verified wrapper 交给 scorer。candidate ID 另绑定 detector、dataset、source、image 与 detection 内容的 SHA。
- **通用规则**：provenance 不是调用者可以事后填写的描述字段，而是执行边界产生的证据。凡输出声称“由 X 生成”，必须让读取 X、核验 X、构造运行对象和记录 X 身份发生在同一受控操作中；排序、文件名或自报 hash 都不能替代事实绑定。
- **牵连**：`~/yolo-xx/src/yolo_xx/predict.py`、`~/yoyo-trading/yoyo/layers/l2_judgment/candidate_bridge.py`、schema-v2 prediction manifest、模型加载器、批量推理设计、first-seen/dedupe 与所有机器可读审计。

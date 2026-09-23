"""Shared research asset composition and explicit engine admission.

The platform sits above existing layer packages: it reads registries and binds
versioned research plans, never imports one execution layer into another. Qlib
provides the task composition reference; MLflow informs model lineage/version
management. Existing engines remain the sole owners of their frozen semantics.
"""
from __future__ import annotations

from copy import deepcopy
from collections import Counter
import hashlib
import json
import uuid

from .store import now


SOURCES = [
    {"name": "Qlib · 数据、模型与研究任务", "url": "https://qlib.readthedocs.io/en/stable/component/workflow.html"},
    {"name": "MLflow · 模型版本与实验血缘", "url": "https://mlflow.org/docs/latest/ml/model-registry/"},
    {"name": "LEAN · 模块化策略框架", "url": "https://www.quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/overview"},
]
ROUTES = [
    dict(id="rules", name="规则与因子策略", description="行情和因子形成规则信号，由已有策略适配器回测或向前模拟。",
         steps=["行情", "因子 / 规则", "策略", "回测 / 前向模拟"]),
    dict(id="yolo_lgbm", name="YOLO 与数值判断", description="YOLO 找形态候选，LightGBM 可选做数值评分；组合运行需独立适配器与证据。",
         steps=["因果图表", "YOLO 候选", "特征 / LightGBM 可选判断", "策略", "评估"]),
    dict(id="vlm", name="VLM 研究", description="VLM 阅读图表并输出结构化观察；与 YOLO 并行，接入策略前验证时间边界与输出契约。",
         steps=["图表 / 上下文", "VLM 观察", "结构化证据", "策略研究", "评估"]),
]


def _component(id, name, kind, view, description, source_path, status="available"):
    return dict(id=id, name=name, kind=kind, view=view, status=status, description=description, source_path=source_path)


class ResearchPlatform:
    def __init__(self, catalog, datasets, models, store, factors, strategies, recipes, paper_store):
        self.catalog, self.datasets, self.models, self.store = catalog, datasets, models, store
        self.factors, self.strategies, self.recipes, self.paper_store = factors, strategies, recipes, paper_store

    def assets(self):
        return dict(datasets=self.datasets.overview()["items"], factors=self.factors(),
                    models=self.models.overview()["items"], strategies=self.strategies(),
                    experiments=self.catalog.experiments())

    @staticmethod
    def _map(assets, kind):
        key = "experiment_id" if kind == "experiments" else "id"
        return {x[key]: x for x in assets[kind]}

    def _check_refs(self, plan, assets):
        for field, kind in (("dataset_ids", "datasets"), ("factor_ids", "factors"), ("model_ids", "models")):
            if set(plan[field]) - self._map(assets, kind).keys():
                raise ValueError(f"{field} 含未登记或已消失的引用，请刷新目录。")
        for field, kind in (("strategy_id", "strategies"), ("experiment_id", "experiments")):
            if plan[field] and plan[field] not in self._map(assets, kind):
                raise ValueError(f"{field} 未登记，请刷新目录。")

    def validate(self, plan, assets=None):
        assets = assets or self.assets()
        blockers = []
        try:
            self._check_refs(plan, assets)
        except ValueError as error:
            blockers.append(str(error))
        if plan["stage"] == "archived":
            blockers.append("流程已归档；历史记录保留。")
        if not plan["experiment_id"]:
            blockers.append("请选择实验，运行结果需要归属。")
        strategy = self._map(assets, "strategies").get(plan["strategy_id"])
        if not strategy:
            blockers.append("请选择已登记策略。")
        elif strategy.get("status") in {"archived", "rejected"}:
            blockers.append("所选策略已归档或拒绝，请先在策略库复核。")
        if plan["route"] != "rules":
            blockers.append("此模型路线尚无完整的交易运行适配器；可继续在模型与研究工作流验证。")
        if plan["model_ids"]:
            blockers.append("现有回测 / 模拟适配器不消费模型输出，不能在运行时忽略所选模型。")
        if plan["factor_ids"]:
            blockers.append("现有冻结策略不接受额外因子覆盖；请先实现并登记对应策略适配器。")
        recipe = next((x for x in self.recipes()["items"] if x["id"] == "spike-v128-frozen"), {})
        backtest = list(blockers)
        if (plan["strategy_id"] != "spike-v128" or plan["experiment_id"] != recipe.get("experiment_id")
                or plan["dataset_ids"] != [recipe.get("dataset_id")] or not recipe.get("available")):
            backtest.append("当前回测适配器仅支持 SPIKE V12.8 原始策略、原冻结实验及其两月数据集。")
        paper = list(blockers)
        if plan["dataset_ids"]:
            paper.append("向前模拟使用已有实时闭合行情；不接受历史数据集替代。")
        if not strategy or not strategy.get("paper_supported"):
            paper.append("此策略尚无完整模拟成交与退出适配器。")
        result = dict(backtest=dict(allowed=not backtest, reason="；".join(backtest)),
                      paper=dict(allowed=not paper, reason="；".join(paper)),
                      blockers=blockers, status="ready" if not backtest or not paper else "blocked")
        return result

    def pipelines(self):
        assets = self.assets()
        items = [dict(id=key, **value, validation=self.validate(value, assets))
                 for key, value in self.store.notes("pipeline").items()]
        options = {kind: [dict(id=x["experiment_id"] if kind == "experiments" else x["id"],
                                   name=x.get("name") or x.get("title") or x.get("question") or x.get("experiment_id"),
                                   **({"family": x["family"]} if kind == "models" else {})) for x in rows]
                   for kind, rows in assets.items()}
        dataset_names = Counter(x["name"] for x in options["datasets"])
        dataset_paths = {x["id"]: x.get("path") or x["id"] for x in assets["datasets"]}
        for item in options["datasets"]:
            if dataset_names[item["name"]] > 1:
                item["name"] += " · " + dataset_paths[item["id"]]
        return dict(items=items, templates=deepcopy(ROUTES), options=options)

    def get(self, key):
        value = self.store.notes("pipeline").get(key)
        if value is None:
            raise KeyError("研究流程不存在")
        return dict(id=key, **value, validation=self.validate(value))

    def save(self, payload, key=None):
        plan = payload.model_dump(exclude={"expected_revision"})
        assets = self.assets()
        self._check_refs(plan, assets)
        if key is None:
            if payload.expected_revision:
                raise ValueError("新流程的版本必须为 0。")
            key = "pipeline-" + uuid.uuid4().hex[:16]
        else:
            self.get(key)
        self.store.save_note("pipeline", key, dict(plan, schema_version=1), payload.expected_revision)
        return self.get(key)

    def admit(self, ref, mode, request):
        """Freeze exact composition into a run; reject silently unused bindings."""
        plan = self.get(ref.id)
        if plan["revision"] != ref.revision:
            raise ValueError("研究流程版本已变化，请刷新后新建运行。")
        permission = plan["validation"][mode]
        if not permission["allowed"]:
            raise ValueError(permission["reason"])
        if mode == "backtest":
            if request.recipe != "spike-v128-frozen" or request.experiment_id != plan["experiment_id"]:
                raise ValueError("回测请求与研究流程的策略 / 实验不一致。")
        elif request.strategy_id != plan["strategy_id"]:
            raise ValueError("模拟请求与研究流程策略不一致。")
        snapshot = {k: v for k, v in plan.items() if k not in {"validation", "updated_at"}}
        raw = json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()
        return dict(snapshot=snapshot, sha256=hashlib.sha256(raw).hexdigest(),
                    mode=mode, training_eligible=False, production_eligible=False)

    def overview(self):
        assets = self.assets()
        summary = {key: len(value) for key, value in assets.items()}
        with self.store.connect() as db:
            summary["backtest_runs"] = db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        with self.paper_store.connect() as db:
            summary["paper_runs"] = db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        C = _component
        layers = [
            dict(id="data", name="数据层", description="共用数据目录、行情读取、图表与事件谱系，复用本地数据。",
                 inputs=["已有行情", "图像 / 标签", "原始样本"], outputs=["可追溯数据集", "闭合行情"], count=summary["datasets"],
                 components=[C("datasets", "数据集目录", "registry", "datasets", "分类、时间范围、存储位置与按需读取。", "yoyo/data/dataset_catalog.py")]),
            dict(id="features", name="特征与标签层", description="因子说明输入窗口；标签与特征保持各自时间边界。",
                 inputs=["决策时点前数据"], outputs=["因果特征", "独立标签"], count=summary["factors"],
                 components=[C("factors", "因子库", "registry", "factors", "L2 特征、开源因子及研究定义统一登记。", "yoyo/research_workspace/catalog.py"),
                             C("l2-features", "LightGBM 特征与标签", "transform", "models", "保留历史特征顺序、方向语义和标签协议。", "yoyo/layers/l2_judgment/features.py")]),
            dict(id="models", name="模型层", description="YOLO、VLM 与数值模型并行提供不同能力，由策略选用。",
                 inputs=["因果图表", "特征矩阵", "上下文"], outputs=["形态候选", "结构化观察", "数值评分"], count=summary["models"],
                 components=[C("yolo", "YOLO", "detection", "yolo", "图表形态检测与标注研究。", "yoyo/layers/l1_detection"),
                             C("vlm", "VLM", "vision_language", "vision", "图表理解、观察与证据复核。", "yoyo/vision_research"),
                             C("lightgbm", "LightGBM / 数值基线", "judgment", "models", "模型版本、特征契约和历史训练证据；当前无新训练入口。", "yoyo/layers/l2_judgment", "research")]),
            dict(id="strategies", name="策略层", description="组合规则、模型输出、风险与退出语义；插件明确支持的运行方式。",
                 inputs=["因子", "候选 / 模型输出"], outputs=["带时间戳的决策"], count=summary["strategies"],
                 components=[C("strategy-plugins", "策略插件", "registry", "strategies", "版本、关联实验、入场退出与适配器。", "yoyo/research_workspace/strategies.py")]),
            dict(id="evaluation", name="评估与回测层", description="时间切分、原成本与匹配随机对照；识别与交易评估分开。",
                 inputs=["冻结策略", "历史数据"], outputs=["指标", "原始结果", "失败证据"], count=summary["backtest_runs"],
                 components=[C("backtests", "回测任务", "runner", "backtests", "独立工作进程与不可变运行规格。", "yoyo/research_workspace/worker.py")]),
            dict(id="forward", name="前向运行层", description="同一策略按实际观察时点向前运行，保留决策和模拟成交。",
                 inputs=["已闭合实时行情", "已登记策略"], outputs=["运行记录", "模拟成交", "信号观察"], count=summary["paper_runs"],
                 components=[C("paper", "模拟实盘", "runner", "paper", "选择插件、暂停 / 恢复、版本快照。", "yoyo/research_workspace/paper_worker.py"),
                             C("signals", "信号中心", "observation", "signals", "实时观察与通知；不等同模拟成交。", "yoyo/monitor")]),
        ]
        return dict(layers=layers, summary=summary, pipelines=deepcopy(ROUTES), sources=SOURCES,
                    cross_cutting=["实验登记与失败记录", "数据 / 代码 / 模型版本", "时间边界与因果检查", "训练与生产准入独立管理"],
                    generated_at=now())

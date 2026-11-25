"""dc_agent 策略层工作流拓扑与事件折叠器（策略层 Layer 2，零 import 期全局副作用）。

四套正式静态有向无环图（`WorkflowPlan`）：
1. `CODE_PARSE_PLAN`（内层纯内存）：切分骨架 -> 侦察规划 -> 并发阶段翻译 -> 汇总封板；
2. `DOC_PARSE_PLAN`（内层纯内存）：条款切分 -> 主领域规划 -> 并发批量提纯 -> 领域收敛封板；
3. `ASSET_SYNTHESIS_PLAN`（外层 DB 账本）：加载实证 -> 按域对齐 -> 并发 AI 口径提纯 -> 资产落库发布；
4. `BATCH_INGEST_PLAN`（外层 DB 账本）：静默扫描 -> Tier 复核门 -> 文档波 -> Tier2 波 -> Tier1 波 -> 克隆与重聚 -> 封板。
"""

from dataclasses import replace
from typing import Any, Dict, Iterable, Optional

from sqlalchemy.orm import Session

from app.infra.db_tables import RunModel
from app.infra.errors import ProviderAuthOrBillingError
from app.kernel.event_log import RunState, fold_with, project_many_with, project_with
from app.kernel.plan import PlanEdge, PlanNode, WorkflowPlan
from app.kernel.run import BudgetExceededError, RunCancelledError
from app.kernel.scheduler import WaveExecutionError

CODE_PARSE_PLAN = WorkflowPlan(
    name="dc_code_parse",
    entry="created",
    nodes=(
        PlanNode("created", "初始化脚本解析", "stage"),
        PlanNode("extract_skeleton", "切分逻辑块与提取骨架", "stage"),
        PlanNode("survey_and_plan", "业务侦察与结构规划", "stage"),
        PlanNode("translate_stage_worker", "分阶段纯业务翻译", "stage"),
        PlanNode("validate_and_finalize", "汇总校验与实体封板", "terminal"),
    ),
    edges=(
        PlanEdge("created", "extract_skeleton", "always"),
        PlanEdge("extract_skeleton", "survey_and_plan", "always"),
        PlanEdge("extract_skeleton", "validate_and_finalize", "ddl_only"),
        PlanEdge("survey_and_plan", "translate_stage_worker", "fanout"),
        PlanEdge("survey_and_plan", "validate_and_finalize", "direct"),
        PlanEdge("translate_stage_worker", "validate_and_finalize", "always"),
    ),
)

DOC_PARSE_PLAN = WorkflowPlan(
    name="dc_doc_parse",
    entry="created",
    nodes=(
        PlanNode("created", "初始化制度解析", "stage"),
        PlanNode("split_clauses", "切分制度条款", "stage"),
        PlanNode("plan_doc_structure", "AI 制度主领域规划", "stage"),
        PlanNode("extract_clause_worker", "批量条款结构化提纯", "stage"),
        PlanNode("consolidate_clauses", "领域收敛与术语封板", "terminal"),
    ),
    edges=(
        PlanEdge("created", "split_clauses", "always"),
        PlanEdge("split_clauses", "plan_doc_structure", "always"),
        PlanEdge("split_clauses", "consolidate_clauses", "empty"),
        PlanEdge("plan_doc_structure", "extract_clause_worker", "fanout"),
        PlanEdge("plan_doc_structure", "consolidate_clauses", "direct"),
        PlanEdge("extract_clause_worker", "consolidate_clauses", "always"),
    ),
)

ASSET_SYNTHESIS_PLAN = WorkflowPlan(
    name="dc_asset_synthesis",
    entry="created",
    nodes=(
        PlanNode("created", "创建资产合成任务", "stage"),
        PlanNode("parse_sources", "加载条款与代码实证", "stage"),
        PlanNode("align_evidence", "按业务域对齐与构草稿", "stage"),
        PlanNode("refine_domain_worker", "AI 领域规则口径提纯", "stage"),
        PlanNode("synthesize_assets", "资产落库与快照发布", "terminal"),
    ),
    edges=(
        PlanEdge("created", "parse_sources", "always"),
        PlanEdge("parse_sources", "align_evidence", "always"),
        PlanEdge("align_evidence", "refine_domain_worker", "fanout"),
        PlanEdge("align_evidence", "synthesize_assets", "direct"),
        PlanEdge("refine_domain_worker", "synthesize_assets", "always"),
    ),
)

BATCH_INGEST_PLAN = WorkflowPlan(
    name="dc_batch_ingest",
    entry="created",
    nodes=(
        PlanNode("created", "创建批量摄入任务", "stage"),
        PlanNode("static_scan", "静默扫描与依赖分层", "stage"),
        PlanNode("tier_review_gate", "分层人工复核门禁", "gate"),
        PlanNode("doc_wave", "制度文件先验解析波次", "stage"),
        PlanNode("tier2_wave", "上游基础逻辑解析波次", "stage"),
        PlanNode("tier1_wave", "下游核心逻辑解析波次", "stage"),
        PlanNode("clone_wave", "同构克隆与孤立兜底", "stage"),
        PlanNode("budget_pause_gate", "预算阈值挂起恢复门", "gate"),
        PlanNode("auto_synthesize", "自动重聚资产台账", "stage"),
        PlanNode("finalize_batch", "批量入库封板完成", "terminal"),
    ),
    edges=(
        PlanEdge("created", "static_scan", "always"),
        PlanEdge("static_scan", "tier_review_gate", "always"),
        PlanEdge("tier_review_gate", "doc_wave", "always"),
        PlanEdge("doc_wave", "tier2_wave", "always"),
        PlanEdge("doc_wave", "budget_pause_gate", "paused"),
        PlanEdge("tier2_wave", "tier1_wave", "always"),
        PlanEdge("tier2_wave", "budget_pause_gate", "paused"),
        PlanEdge("tier1_wave", "clone_wave", "always"),
        PlanEdge("tier1_wave", "budget_pause_gate", "paused"),
        PlanEdge("clone_wave", "budget_pause_gate", "paused"),
        PlanEdge("clone_wave", "auto_synthesize", "synthesize"),
        PlanEdge("clone_wave", "finalize_batch", "always"),
        PlanEdge("budget_pause_gate", "doc_wave", "always"),
        PlanEdge("auto_synthesize", "finalize_batch", "always"),
    ),
)

WORKFLOW_PLANS: Dict[str, WorkflowPlan] = {
    CODE_PARSE_PLAN.name: CODE_PARSE_PLAN,
    DOC_PARSE_PLAN.name: DOC_PARSE_PLAN,
    ASSET_SYNTHESIS_PLAN.name: ASSET_SYNTHESIS_PLAN,
    BATCH_INGEST_PLAN.name: BATCH_INGEST_PLAN,
}


def get_plan(spec_id: Optional[str]) -> WorkflowPlan:
    """按 Run.spec_id 返回对应的静态 `WorkflowPlan`，未命中时默认回落资产合成图。"""
    if spec_id and spec_id in WORKFLOW_PLANS:
        return WORKFLOW_PLANS[spec_id]
    if spec_id and "batch" in spec_id:
        return BATCH_INGEST_PLAN
    return ASSET_SYNTHESIS_PLAN


def unwrap_wave_error(exc: BaseException) -> BaseException:
    """从并发波次 `WaveExecutionError` 中按优先级提取根因异常（取消 > 鉴权欠费 > 预算 > 首错）。"""
    if not isinstance(exc, WaveExecutionError):
        return exc
    errs = list(getattr(exc, "errors", []) or [])
    for priority_type in (RunCancelledError, ProviderAuthOrBillingError, BudgetExceededError):
        for _label, err in errs:
            if isinstance(err, priority_type):
                return err
    return getattr(exc, "first_error", exc)


def _apply(state: RunState, kind: str, payload: Dict[str, Any]) -> RunState:
    payload = payload or {}
    if kind == "RUN_CREATED":
        return replace(state, status="RUNNING", current_node="created", when="always", pending_target=None)
    if kind == "CODE_SKELETON_EXTRACTED":
        return replace(state, status="RUNNING", current_node="extract_skeleton", result_json=payload)
    if kind == "CODE_STRUCTURE_PLANNED":
        return replace(state, status="RUNNING", current_node="survey_and_plan", result_json=payload)
    if kind == "CODE_STAGE_TRANSLATED":
        return replace(state, status="RUNNING", current_node="translate_stage_worker", result_json=payload)
    if kind == "CODE_PARSED":
        return replace(state, status="COMPLETED", current_node="validate_and_finalize", result_json=payload, pending_target=None)
    if kind == "DOC_CLAUSES_SPLIT":
        return replace(state, status="RUNNING", current_node="split_clauses", result_json=payload)
    if kind == "DOC_STRUCTURE_PLANNED":
        return replace(state, status="RUNNING", current_node="plan_doc_structure", result_json=payload)
    if kind == "DOC_BATCH_EXTRACTED":
        return replace(state, status="RUNNING", current_node="extract_clause_worker", result_json=payload)
    if kind == "DOC_PARSED":
        return replace(state, status="COMPLETED", current_node="consolidate_clauses", result_json=payload, pending_target=None)
    if kind == "BATCH_SCANNED":
        return replace(state, status="READY_REVIEW", current_node="static_scan", result_json=payload)
    if kind == "TIER_REVIEW_REQUIRED":
        return replace(
            state,
            status="WAITING_REVIEW",
            current_node="tier_review_gate",
            pending_target="tier_review_gate",
            resume_from_node="tier_review_gate",
        )
    if kind == "BATCH_STARTED":
        return replace(
            state,
            status="RUNNING",
            current_node="parse_batch",
            result_json=payload,
            pending_target=None,
            when="always",
        )
    if kind == "BATCH_CHECKPOINT":
        return replace(
            state,
            status="RUNNING",
            current_node=str(payload.get("phase") or "parse_batch"),
            result_json=payload,
        )
    if kind == "BATCH_PAUSED":
        return replace(state, status="PAUSED_BUDGET", current_node="paused", result_json=payload)
    if kind == "BATCH_PAUSE_REQUIRED":
        return replace(
            state,
            status="WAITING_BUDGET",
            current_node="budget_pause_gate",
            pending_target="budget_pause_gate",
            resume_from_node="budget_pause_gate",
        )
    if kind == "BATCH_COMPLETED":
        return replace(
            state,
            status="COMPLETED",
            current_node="finalize_batch",
            result_json=payload,
            pending_target=None,
        )
    if kind == "SOURCES_PARSED":
        return replace(state, status="RUNNING", current_node="parse_sources", result_json=payload)
    if kind == "EVIDENCE_ALIGNED":
        return replace(state, status="RUNNING", current_node="align_evidence", result_json=payload)
    if kind == "DOMAIN_REFINED":
        return replace(state, status="RUNNING", current_node="refine_domain_worker", result_json=payload)
    if kind == "ASSETS_SYNTHESIZED":
        return replace(
            state,
            status="COMPLETED",
            current_node="synthesize_assets",
            result_json=payload.get("summary") or payload,
            pending_target=None,
        )
    if kind == "GO_REDIRECT":
        target = payload.get("target")
        return replace(
            state,
            status="RUNNING",
            current_node=str(target or state.current_node),
            pending_target=str(target) if target else state.pending_target,
        )
    if kind == "RUN_RESUMED":
        resume_nodes = payload.get("resume_nodes") or []
        target = str(resume_nodes[0]) if resume_nodes else state.pending_target
        return replace(
            state,
            status="RUNNING",
            current_node=target or state.current_node,
            pending_target=target,
            when="always",
        )
    if kind == "RUN_CANCELLED":
        return replace(state, status="CANCELLED", current_node="cancelled", result_json=payload, pending_target=None)
    if kind == "RUN_FAILED":
        return replace(state, status="FAILED", current_node="failed", result_json=payload, pending_target=None)
    return state


def fold(events) -> RunState:
    """将 Run 事件流折叠为确定性 `RunState`。"""
    return fold_with(events, reducer=_apply)


def project(session: Session, run: RunModel) -> RunState:
    """投影单个 Run 的最新状态快照。"""
    return project_with(session, run, reducer=_apply)


def project_many(session: Session, runs: Iterable[RunModel]) -> Dict[str, RunState]:
    """批量投影多个 Run 的最新状态，单条 SQL IN 查询避免列表接口 N+1。"""
    return project_many_with(session, runs, reducer=_apply)

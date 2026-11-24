"""dc_agent 双源解析与业务资产合成拓扑策略（策略层 Layer 2）。"""

from dataclasses import replace
from typing import Any, Dict, Iterable, Optional, Tuple

from sqlalchemy.orm import Session

from app.infra.db_tables import RunModel
from app.kernel.event_log import RunState, fold_with, project_many_with, project_with, register_default_reducer
from app.kernel.plan import PlanEdge, PlanNode, WorkflowPlan

SYNTHESIS_NODES: Tuple[PlanNode, ...] = (
    PlanNode("created", "已创建", "stage"),
    PlanNode("parse_sources", "双源解析提取", "stage"),
    PlanNode("align_evidence", "代码与制度对齐", "stage"),
    PlanNode("synthesize_assets", "合成业务资产层", "stage"),
    PlanNode("published", "已发布就绪", "terminal"),
    PlanNode("failed", "失败", "terminal"),
)

SYNTHESIS_EDGES: Tuple[PlanEdge, ...] = (
    PlanEdge("created", "parse_sources", "always"),
    PlanEdge("parse_sources", "align_evidence", "always"),
    PlanEdge("align_evidence", "synthesize_assets", "always"),
    PlanEdge("synthesize_assets", "published", "always"),
    PlanEdge("synthesize_assets", "failed", "failed"),
)

SYNTHESIS_PLAN = WorkflowPlan(
    name="dc_asset_synthesis",
    nodes=SYNTHESIS_NODES,
    edges=SYNTHESIS_EDGES,
    entry="created",
)


def _apply(state: RunState, kind: str, payload: Dict[str, Any]) -> RunState:
    payload = payload or {}
    if kind == "RUN_CREATED":
        return replace(state, status="RUNNING", current_node="created", when="always")
    if kind == "BATCH_SCANNED":
        return replace(state, status="READY_REVIEW", current_node="static_scan", result_json=payload)
    if kind == "BATCH_STARTED":
        return replace(state, status="RUNNING", current_node="parse_batch", result_json=payload)
    if kind == "BATCH_CHECKPOINT":
        return replace(state, status="RUNNING", current_node=str(payload.get("phase") or "parse_batch"), result_json=payload)
    if kind == "BATCH_PAUSED":
        return replace(state, status="PAUSED_BUDGET", current_node="paused", result_json=payload)
    if kind == "BATCH_COMPLETED":
        return replace(state, status="COMPLETED", current_node="completed", result_json=payload)
    if kind == "SOURCES_PARSED":
        return replace(state, status="RUNNING", current_node="parse_sources", result_json=payload)
    if kind == "EVIDENCE_ALIGNED":
        return replace(state, status="RUNNING", current_node="align_evidence", result_json=payload)
    if kind == "ASSETS_SYNTHESIZED":
        return replace(state, status="COMPLETED", current_node="published", result_json=payload.get("summary") or payload)
    if kind == "RUN_CANCELLED":
        return replace(state, status="CANCELLED", current_node="cancelled", result_json=payload)
    if kind == "RUN_FAILED":
        return replace(state, status="FAILED", current_node="failed", result_json=payload)
    return state


register_default_reducer(_apply)


def fold(events) -> RunState:
    return fold_with(events, reducer=_apply)


def project(session: Session, run: RunModel) -> RunState:
    return project_with(session, run, reducer=_apply)


def project_many(session: Session, runs: Iterable[RunModel]) -> Dict[str, RunState]:
    return project_many_with(session, runs, reducer=_apply)

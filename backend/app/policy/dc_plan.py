"""dc_agent 批任务与合成 Run 事件状态折叠策略（策略层 Layer 2，零 import 期全局副作用）。"""

from dataclasses import replace
from typing import Any, Dict, Iterable

from sqlalchemy.orm import Session

from app.infra.db_tables import RunModel
from app.kernel.event_log import RunState, fold_with, project_many_with, project_with


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


def fold(events) -> RunState:
    """将 Run 事件流折叠为确定性 `RunState`。"""
    return fold_with(events, reducer=_apply)


def project(session: Session, run: RunModel) -> RunState:
    """投影单个 Run 的最新状态快照。"""
    return project_with(session, run, reducer=_apply)


def project_many(session: Session, runs: Iterable[RunModel]) -> Dict[str, RunState]:
    """批量投影多个 Run 的最新状态快照。"""
    return project_many_with(session, runs, reducer=_apply)

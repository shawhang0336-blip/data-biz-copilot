"""平台与运行观测路由（`/api/health` / `/api/events/stream` / `/api/runs/*` / `/api/settings/*`）。"""

import json
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.common import db_session
from app.api.platform.audit_service import build_audit_overview, build_audit_run_detail
from app.infra.config import DEFAULT_BUDGET, get_budget, get_llm_config, get_settings
from app.infra.db_tables import EventModel, RunModel, SystemSettingModel
from app.kernel.bus import global_bus
from app.kernel.event_log import build_trace, verify_chain
from app.policy.dc_plan import get_plan, project

router = APIRouter(tags=["platform"])


class BudgetUpdateRequest(BaseModel):
    max_retries: int = Field(default=3, ge=1, le=20)
    max_llm_concurrency: int = Field(default=6, ge=1, le=16)
    max_llm_rpm: int = Field(default=60, ge=0, le=600)
    max_calls_per_script: int = Field(default=8, ge=1, le=30)
    max_llm_calls: int = Field(default=2400, ge=0, le=20000)
    max_total_tokens: int = Field(default=20000000, ge=0, le=200000000)
    max_elapsed_seconds: int = Field(default=7200, ge=0, le=86400)


class LlmConfigUpdateRequest(BaseModel):
    llm_provider: str = Field(default="scripted", min_length=1, max_length=64)
    llm_base_url: str = Field(default="https://open.bigmodel.cn/api/paas/v4", min_length=1, max_length=500)
    llm_model: str = Field(default="glm-4-plus", min_length=1, max_length=128)
    llm_api_key: str = Field(default="", max_length=500)
    llm_timeout: int = Field(default=120, ge=5, le=600)


@router.get("/health")
def health_check(session: Session = Depends(db_session)) -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": "data-biz-copilot",
        "runs": session.query(RunModel).count(),
    }


@router.get("/events/stream")
async def stream_events(request: Request):
    """SSE 实时事件流，供前端查看双源合成与问答阶段进展。"""
    async def _generator():
        with global_bus().stream(topic="*", maxsize=128, overflow="drop") as sub:
            yield f"data: {json.dumps({'topic': 'stream.connected', 'timestamp': time.time()})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                ev = sub.get(timeout=0.5)
                if ev is None:
                    yield ": keep-alive\n\n"
                    continue
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    return StreamingResponse(_generator(), media_type="text/event-stream")


@router.get("/runs/audit-overview")
def audit_overview(
    run_kind: Optional[str] = Query(default="ALL"),
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(db_session),
) -> Dict[str, Any]:
    return build_audit_overview(session, run_kind=run_kind, limit=limit)


@router.get("/runs/{run_id}/audit-detail")
def audit_run_detail(run_id: str, session: Session = Depends(db_session)) -> Dict[str, Any]:
    run = session.get(RunModel, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return build_audit_run_detail(session, run)


@router.get("/runs/{run_id}")
def get_synthesis_run(run_id: str, session: Session = Depends(db_session)) -> Dict[str, Any]:
    run = session.get(RunModel, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    state = project(session, run)
    events = (
        session.query(EventModel)
        .filter(EventModel.run_id == run_id)
        .order_by(EventModel.seq.asc())
        .all()
    )
    trace = build_trace(events, run_id=run.id, plan=get_plan(run.spec_id)).as_dict()
    return {
        "id": run.id,
        "spec_id": run.spec_id,
        "status": state.status,
        "current_node": state.current_node,
        "result": state.result_json,
        "chain_valid": verify_chain(session, run),
        "total_duration_ms": trace["total_duration_ms"],
        "retry_count": trace["retry_count"],
        "rejection_count": trace["rejection_count"],
        "interrupt_count": trace["interrupt_count"],
        "spans": trace["spans"],
        "by_node": trace["by_node"],
        "events": [
            {"seq": e.seq, "kind": e.kind, "node": e.node, "payload": e.payload_json}
            for e in events
        ],
    }


@router.get("/settings/execution-budget")
def read_budget(session: Session = Depends(db_session)) -> Dict[str, Any]:
    return {"budget": get_budget(session), "defaults": DEFAULT_BUDGET}


@router.put("/settings/execution-budget")
def update_budget(req: BudgetUpdateRequest, session: Session = Depends(db_session)) -> Dict[str, Any]:
    data = req.model_dump()
    row = session.get(SystemSettingModel, "execution_budget")
    if row is None:
        row = SystemSettingModel(key="execution_budget", value_json=data)
        session.add(row)
    else:
        row.value_json = data
    session.commit()
    return {"budget": get_budget(session)}


@router.get("/settings/llm-config")
def read_llm_config(session: Session = Depends(db_session)) -> Dict[str, Any]:
    return {"config": get_llm_config(session, get_settings())}


@router.put("/settings/llm-config")
def update_llm_config(req: LlmConfigUpdateRequest, session: Session = Depends(db_session)) -> Dict[str, Any]:
    row = session.get(SystemSettingModel, "llm_config")
    existing = dict(row.value_json or {}) if (row is not None and isinstance(row.value_json, dict)) else {}
    data = req.model_dump()
    # 未输入新密钥时保留已有持久化密钥，防止误覆盖清空
    if not str(data.get("llm_api_key") or "").strip():
        data["llm_api_key"] = str(existing.get("llm_api_key") or "")
    if row is None:
        row = SystemSettingModel(key="llm_config", value_json=data)
        session.add(row)
    else:
        row.value_json = data
    session.commit()
    return {"config": get_llm_config(session, get_settings())}

"""平台与运行观测路由（`/api/health` / `/api/events/stream` / `/api/runs/*` / `/api/settings/*`）。"""

import json
import time
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.common import db_session
from app.infra.config import DEFAULT_BUDGET, get_budget
from app.infra.db_tables import EventModel, RunModel, SystemSettingModel
from app.kernel.bus import global_bus
from app.kernel.event_log import verify_chain
from app.policy.dc_plan import project

router = APIRouter(tags=["platform"])


class BudgetUpdateRequest(BaseModel):
    max_retries: int = Field(default=3, ge=1, le=20)
    max_llm_concurrency: int = Field(default=4, ge=1, le=16)
    max_llm_calls: int = Field(default=120, ge=0, le=2000)
    max_total_tokens: int = Field(default=1500000, ge=0, le=50000000)
    max_elapsed_seconds: int = Field(default=900, ge=0, le=86400)


@router.get("/health")
def health_check(session: Session = Depends(db_session)) -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": "dc-agent",
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
    return {
        "id": run.id,
        "status": state.status,
        "current_node": state.current_node,
        "result": state.result_json,
        "chain_valid": verify_chain(session, run),
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

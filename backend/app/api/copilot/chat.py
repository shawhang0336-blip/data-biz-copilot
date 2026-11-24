"""纯业务 Data Copilot 问答路由（`/api/copilot/*`）。"""

import json
import queue
import threading
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.common import db_session
from app.copilot.answer_engine import (
    ask_business_copilot,
    get_copilot_session_detail,
    list_copilot_sessions,
)
from app.copilot.business_tools import BUSINESS_TOOL_SCHEMAS
from app.infra.config import get_settings
from app.infra.db import SessionLocal

router = APIRouter(prefix="/copilot", tags=["copilot"])


class AskRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    session_id: Optional[str] = None
    role_lens: str = Field(default="业务", max_length=64)


@router.get("/tools")
def list_tools() -> Dict[str, Any]:
    return {"tools": BUSINESS_TOOL_SCHEMAS}


@router.post("/ask")
def ask(req: AskRequest, session: Session = Depends(db_session)) -> Dict[str, Any]:
    try:
        return ask_business_copilot(
            session,
            req.query,
            session_id=req.session_id,
            role_lens=req.role_lens,
            settings=get_settings(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/ask/stream")
def ask_stream(req: AskRequest):
    """SSE 实时流式推送思考/检索阶段与最终解答。"""
    event_queue: "queue.Queue[Optional[Dict[str, Any]]]" = queue.Queue()

    def _worker() -> None:
        local_session = SessionLocal()
        try:
            result = ask_business_copilot(
                local_session,
                req.query,
                session_id=req.session_id,
                role_lens=req.role_lens,
                settings=get_settings(),
                progress_callback=lambda ev: event_queue.put(ev),
            )
            event_queue.put({"type": "result", "data": result})
        except Exception as exc:
            try:
                local_session.rollback()
            except Exception:
                pass
            event_queue.put({"type": "error", "detail": str(exc)})
        finally:
            local_session.close()
            event_queue.put(None)

    threading.Thread(target=_worker, daemon=True).start()

    def _stream():
        while True:
            try:
                item = event_queue.get(timeout=0.4)
            except queue.Empty:
                yield ": keep-alive\n\n"
                continue
            if item is None:
                break
            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/sessions")
def sessions(session: Session = Depends(db_session)) -> List[Dict[str, Any]]:
    return list_copilot_sessions(session)


@router.get("/sessions/{session_id}")
def session_detail(session_id: str, session: Session = Depends(db_session)) -> Dict[str, Any]:
    try:
        return get_copilot_session_detail(session, session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

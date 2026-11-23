"""纯业务 Data Copilot 问答路由（`/api/copilot/*`）。"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
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


@router.get("/sessions")
def sessions(session: Session = Depends(db_session)) -> List[Dict[str, Any]]:
    return list_copilot_sessions(session)


@router.get("/sessions/{session_id}")
def session_detail(session_id: str, session: Session = Depends(db_session)) -> Dict[str, Any]:
    try:
        return get_copilot_session_detail(session, session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

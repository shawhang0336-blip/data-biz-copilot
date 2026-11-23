"""双源接入路由（`/api/sources/*`）：监管材料接入、代码逻辑静默解析、一键载入样例与触发融合。"""

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.api.common import db_session
from app.catalog.synthesizer import (
    ingest_material,
    load_builtin_sample_pack,
    load_real_sac_corpus,
    synthesize_business_assets,
)
from app.catalog.views import list_source_materials
from app.infra.config import get_settings

router = APIRouter(prefix="/sources", tags=["ingestion"])


@router.get("")
def list_sources(session: Session = Depends(db_session)) -> List[Dict[str, Any]]:
    return list_source_materials(session)


@router.post("/upload", status_code=201)
async def upload_source(
    material_kind: str = Form(...),
    file: UploadFile = File(...),
    session: Session = Depends(db_session),
) -> Dict[str, Any]:
    if material_kind not in {"REGULATORY_DOC", "CODE_LOGIC"}:
        raise HTTPException(status_code=400, detail="material_kind 必须为 REGULATORY_DOC 或 CODE_LOGIC")
    content = await file.read()
    filename = file.filename or "untitled"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "txt"
    row = ingest_material(
        session,
        title=filename,
        material_kind=material_kind,
        file_type=ext,
        content=content,
        settings=get_settings(),
    )
    session.commit()
    return {
        "id": row.id,
        "title": row.title,
        "material_kind": row.material_kind,
        "file_type": row.file_type,
        "status": row.status,
        "summary": row.summary,
        "item_count": row.item_count,
    }


@router.post("/synthesize")
def trigger_synthesis(session: Session = Depends(db_session)) -> Dict[str, Any]:
    return synthesize_business_assets(session, settings=get_settings())


@router.post("/load-sample")
def load_sample_pack(session: Session = Depends(db_session)) -> Dict[str, Any]:
    return load_builtin_sample_pack(session, settings=get_settings())


@router.post("/load-sac-real")
def load_real_sac_pack(session: Session = Depends(db_session)) -> Dict[str, Any]:
    return load_real_sac_corpus(session, settings=get_settings())

"""双源接入路由（`/api/sources/*`）：监管材料接入、千级脚本批量工程导入、断点续跑与触发融合。"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.common import db_session
from app.catalog.batch_ingest import (
    cancel_batch_job,
    classify_extension_kind,
    collect_directory_files,
    create_batch_ingestion_job,
    extract_zip_archive_safely,
    get_batch_job_detail,
    list_batch_jobs,
    override_material_tier,
    resume_batch_job,
    start_batch_job,
)
from app.catalog.synthesizer import (
    ingest_material,
    load_builtin_sample_pack,
    load_real_sac_corpus,
    synthesize_business_assets,
)
from app.catalog.views import list_source_materials
from app.infra.config import get_settings

router = APIRouter(prefix="/sources", tags=["ingestion"])


class ImportDirRequest(BaseModel):
    dir_path: str = Field(..., min_length=1, max_length=1000)
    include_globs: List[str] = Field(default_factory=list)
    exclude_globs: List[str] = Field(default_factory=list)
    job_name: str = Field(default="", max_length=255)
    enable_template_dedup: bool = True
    auto_start: bool = False
    auto_synthesize: bool = False


class TierOverrideRequest(BaseModel):
    tier: str = Field(..., pattern="^(TIER_1_CORE|TIER_2_UPSTREAM|TIER_3_SKIP_AI)$")


class ResumeJobRequest(BaseModel):
    mode: str = Field(default="pending_and_failed", pattern="^(pending_and_failed|failed_only)$")


class SynthesizeRequest(BaseModel):
    target_domains: Optional[List[str]] = None


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
def trigger_synthesis(
    req: Optional[SynthesizeRequest] = Body(default=None),
    session: Session = Depends(db_session),
) -> Dict[str, Any]:
    target_domains = req.target_domains if req is not None else None
    return synthesize_business_assets(session, settings=get_settings(), target_domains=target_domains)


@router.post("/batch-upload", status_code=201)
async def batch_upload_sources(
    files: List[UploadFile] = File(...),
    material_kind: str = Form(default="AUTO"),
    job_name: str = Form(default=""),
    enable_template_dedup: bool = Form(default=True),
    auto_start: bool = Form(default=False),
    auto_synthesize: bool = Form(default=False),
    session: Session = Depends(db_session),
) -> Dict[str, Any]:
    if not files:
        raise HTTPException(status_code=400, detail="请至少选择一个 SQL 脚本、文档或 ZIP 压缩包")
    preferred = material_kind if material_kind in {"AUTO", "REGULATORY_DOC", "CODE_LOGIC"} else "AUTO"
    collected: List[Dict[str, Any]] = []
    source_mode = "UPLOAD_BATCH"
    for f in files:
        fname = f.filename or "untitled"
        raw = await f.read()
        if fname.lower().endswith(".zip"):
            source_mode = "ZIP_ARCHIVE"
            try:
                collected.extend(extract_zip_archive_safely(raw, preferred_kind=preferred))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            continue
        ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else "txt"
        kind = classify_extension_kind(fname, preferred)
        if kind is None:
            continue
        collected.append(
            {
                "title": fname.rsplit("/", 1)[-1],
                "relative_path": fname,
                "material_kind": kind,
                "file_type": ext,
                "content": raw,
            }
        )
    if not collected:
        raise HTTPException(status_code=400, detail="未在上传内容中发现支持的 SQL 脚本或制度文档格式")

    settings = get_settings()
    try:
        detail = create_batch_ingestion_job(
            session,
            collected,
            job_name=job_name.strip() or f"批量接入（{len(collected)} 份材料）",
            source_mode=source_mode,
            settings=settings,
            enable_template_dedup=enable_template_dedup,
            auto_synthesize=auto_synthesize,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if auto_start:
        start_batch_job(session, detail["job"]["id"], settings=settings)
        detail = get_batch_job_detail(session, detail["job"]["id"], page=1, page_size=50)
    return detail


@router.post("/import-dir", status_code=201)
def import_directory_sources(
    req: ImportDirRequest,
    session: Session = Depends(db_session),
) -> Dict[str, Any]:
    try:
        collected = collect_directory_files(
            req.dir_path,
            include_globs=req.include_globs,
            exclude_globs=req.exclude_globs,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not collected:
        raise HTTPException(status_code=400, detail="目标目录下未匹配到任何 SQL 脚本或制度文档")
    settings = get_settings()
    detail = create_batch_ingestion_job(
        session,
        collected,
        job_name=req.job_name.strip() or f"目录扫描：{req.dir_path.rsplit('/', 1)[-1] or req.dir_path}",
        source_mode="LOCAL_DIR",
        settings=settings,
        enable_template_dedup=req.enable_template_dedup,
        auto_synthesize=req.auto_synthesize,
    )
    if req.auto_start:
        start_batch_job(session, detail["job"]["id"], settings=settings)
        detail = get_batch_job_detail(session, detail["job"]["id"], page=1, page_size=50)
    return detail


@router.get("/jobs")
def list_ingestion_jobs(session: Session = Depends(db_session)) -> List[Dict[str, Any]]:
    return list_batch_jobs(session)


@router.get("/jobs/{job_id}")
def get_ingestion_job(
    job_id: str,
    tier: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(db_session),
) -> Dict[str, Any]:
    try:
        return get_batch_job_detail(session, job_id, tier=tier, status=status, page=page, page_size=page_size)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/jobs/{job_id}/materials/{material_id}/tier")
def patch_material_tier(
    job_id: str,
    material_id: str,
    req: TierOverrideRequest,
    session: Session = Depends(db_session),
) -> Dict[str, Any]:
    try:
        return override_material_tier(session, job_id, material_id, req.tier)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/start")
def start_ingestion_job(job_id: str, session: Session = Depends(db_session)) -> Dict[str, Any]:
    try:
        return start_batch_job(session, job_id, settings=get_settings())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/cancel")
def cancel_ingestion_job(job_id: str, session: Session = Depends(db_session)) -> Dict[str, Any]:
    try:
        return cancel_batch_job(session, job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/resume")
def resume_ingestion_job(
    job_id: str,
    req: Optional[ResumeJobRequest] = Body(default=None),
    session: Session = Depends(db_session),
) -> Dict[str, Any]:
    mode = req.mode if req is not None else "pending_and_failed"
    try:
        return resume_batch_job(session, job_id, mode=mode, settings=get_settings())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/load-sample")
def load_sample_pack(session: Session = Depends(db_session)) -> Dict[str, Any]:
    return load_builtin_sample_pack(session, settings=get_settings())


@router.post("/load-sac-real")
def load_real_sac_pack(session: Session = Depends(db_session)) -> Dict[str, Any]:
    return load_real_sac_corpus(session, settings=get_settings())

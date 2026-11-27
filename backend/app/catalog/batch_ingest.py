"""千级 SQL 脚本异步流式接入、拓扑波次调度与逐脚本断点续跑协调器（资产层 Layer 4）。

核心保障：
1. Blob 先落盘再建账：`PENDING -> STATIC_INDEXED -> PARSING -> PARSED / SKIPPED_AI / FAILED`；
2. Worker 线程池只做无状态的 AST 切块与受控大模型 HTTP 调用，**全部 SQLite 写入与提交收敛在主协调线程**；
3. 按脚本级事务立即 Checkpoint：单脚本完成即刻提交，宕机/取消/触达批预算后可零重复断点续跑；
4. 两级预算守卫：单脚本 `script_bus` 超限只跳过本脚本，批任务 `job_bus` 超限优雅暂停为 `PAUSED_BUDGET`；
5. 波次拓扑执行与跨脚本上下文透传：Tier 2 上游摘要经严格去技术化后注入下游 Tier 1，同构模板零模型克隆实证。
"""

from collections import deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
import fnmatch
import hashlib
import io
from pathlib import Path, PurePosixPath
import stat
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
import uuid
import zipfile

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.catalog.synthesizer import (
    _persist_material_items,
    _resolve_optional_llm,
    synthesize_business_assets,
)
from app.infra.blob_store import LocalBlobStore
from app.infra.config import Settings, get_budget, get_effective_settings, get_settings
from app.infra.db import SessionLocal
from app.infra.errors import ProviderAuthOrBillingError
from app.infra.db_tables import (
    IngestionJobModel,
    LogicEvidenceModel,
    RunModel,
    SourceMaterialModel,
)
from app.infra.llm import (
    configure_llm_concurrency,
    configure_llm_rate_limit,
    llm_execution_scope,
    submit_with_context,
)
from app.ingestion.code_parser import parse_code_logic
from app.ingestion.doc_parser import parse_regulatory_document
from app.ingestion.project_scanner import (
    TIER_1_CORE,
    TIER_2_UPSTREAM,
    TIER_3_SKIP_AI,
    scan_project_batch,
)
from app.kernel.bus import Bus, global_bus
from app.kernel.event_log import append_event
from app.kernel.run import (
    BudgetExceededError,
    CancellationToken,
    RunCancelledError,
    cancel_run,
    create_run as kernel_create_run,
    recover_interrupted_runs,
    submit_run,
)
from app.kernel.scheduler import Outcome, Scheduler, Send
from app.policy.dc_bus import build_run_bus, build_script_child_bus, default_bus, sanitize_business_text
from app.policy.dc_channels import build_batch_ingest_context
from app.policy.dc_plan import BATCH_INGEST_PLAN, project as project_dc_plan, unwrap_wave_error

CODE_EXTENSIONS = {"sql", "prc", "pck", "bdy", "ddl", "hql", "plsql", "pls"}
DOC_EXTENSIONS = {"md", "txt", "docx", "doc", "pdf", "xlsx", "xls", "csv"}
IGNORED_DIR_NAMES = {".git", "__MACOSX", "node_modules", "__pycache__", ".venv", "venv", ".idea", ".vscode"}
TERMINAL_MATERIAL_STATUSES = {"PARSED", "SKIPPED_AI"}


def classify_extension_kind(filename: str, preferred_kind: str = "AUTO") -> Optional[str]:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if preferred_kind in {"CODE_LOGIC", "REGULATORY_DOC"}:
        return preferred_kind
    if ext in CODE_EXTENSIONS:
        return "CODE_LOGIC"
    if ext in DOC_EXTENSIONS:
        return "REGULATORY_DOC"
    return None


def _is_ignored_relative_path(rel_path: str) -> bool:
    parts = PurePosixPath(rel_path.replace("\\", "/")).parts
    if not parts:
        return True
    for part in parts:
        if part in IGNORED_DIR_NAMES or part.startswith("."):
            return True
    return False


def extract_zip_archive_safely(
    raw_bytes: bytes,
    *,
    preferred_kind: str = "AUTO",
    max_files: int = 5000,
    max_file_bytes: int = 5 * 1024 * 1024,
    max_total_bytes: int = 200 * 1024 * 1024,
) -> List[Dict[str, Any]]:
    """安全解压 ZIP 压缩包：拦截 Zip Slip 路径穿越、符号链接、超限文件与目录炸弹。"""
    files: List[Dict[str, Any]] = []
    total_uncompressed = 0
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw_bytes))
    except zipfile.BadZipFile as exc:
        raise ValueError(f"无效的 ZIP 压缩包：{exc}") from exc

    with zf:
        for info in zf.infolist():
            norm_raw = (info.filename or "").replace("\\", "/")
            posix = PurePosixPath(norm_raw)
            if norm_raw.startswith("/") or (len(norm_raw) >= 2 and norm_raw[1] == ":") or ".." in posix.parts:
                raise ValueError(f"不安全的压缩包路径（路径穿越被拦截）：{info.filename}")
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError(f"压缩包内禁止包含符号链接：{info.filename}")
            if info.is_dir():
                continue
            rel_path = "/".join(p for p in posix.parts if p not in ("", "."))
            if _is_ignored_relative_path(rel_path):
                continue
            ext = rel_path.rsplit(".", 1)[-1].lower() if "." in rel_path else ""
            mat_kind = classify_extension_kind(rel_path, preferred_kind)
            if mat_kind is None:
                continue
            if info.file_size > max_file_bytes:
                raise ValueError(f"单文件体积超限（最大 {max_file_bytes // (1024 * 1024)}MB）：{rel_path}")
            total_uncompressed += info.file_size
            if total_uncompressed > max_total_bytes:
                raise ValueError(f"压缩包解压总体积超限（最大 {max_total_bytes // (1024 * 1024)}MB）")
            if len(files) >= max_files:
                raise ValueError(f"单批导入文件数量超出上限（最大 {max_files} 个）")
            payload = zf.read(info)
            if len(payload) > max_file_bytes:
                raise ValueError(f"单文件实际解压体积超限：{rel_path}")
            files.append(
                {
                    "title": PurePosixPath(rel_path).name,
                    "relative_path": rel_path,
                    "material_kind": mat_kind,
                    "file_type": ext or "txt",
                    "content": payload,
                }
            )
    return files


def collect_directory_files(
    dir_path: str,
    *,
    include_globs: Optional[List[str]] = None,
    exclude_globs: Optional[List[str]] = None,
    preferred_kind: str = "AUTO",
    max_files: int = 5000,
    max_file_bytes: int = 5 * 1024 * 1024,
) -> List[Dict[str, Any]]:
    """受控扫描本地工程目录并收集 SQL 脚本与制度文档。"""
    root = Path(dir_path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"目录不存在或不可访问：{dir_path}")

    inc = [g.strip() for g in (include_globs or []) if g and g.strip()]
    exc = [g.strip() for g in (exclude_globs or []) if g and g.strip()]
    collected: List[Dict[str, Any]] = []

    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if _is_ignored_relative_path(rel):
            continue
        if inc and not any(fnmatch.fnmatch(path.name, g) or fnmatch.fnmatch(rel, g) for g in inc):
            continue
        if exc and any(fnmatch.fnmatch(path.name, g) or fnmatch.fnmatch(rel, g) for g in exc):
            continue
        mat_kind = classify_extension_kind(path.name, preferred_kind)
        if mat_kind is None:
            continue
        size = path.stat().st_size
        if size > max_file_bytes:
            continue
        if len(collected) >= max_files:
            break
        ext = path.name.rsplit(".", 1)[-1].lower() if "." in path.name else "sql"
        collected.append(
            {
                "title": path.name,
                "relative_path": rel,
                "material_kind": mat_kind,
                "file_type": ext,
                "content": path.read_bytes(),
            }
        )
    return collected


def _job_to_dict(job: IngestionJobModel) -> Dict[str, Any]:
    processed = int(job.completed_count or 0) + int(job.skipped_count or 0) + int(job.failed_count or 0)
    total = int(job.total_files or 0)
    progress_pct = round(min(100.0, (processed / total) * 100.0), 1) if total > 0 else 0.0
    ai_targets = int(job.tier1_count or 0) + int(job.tier2_count or 0)
    saved_calls_pct = round(((total - ai_targets) / total) * 100.0, 1) if total > 0 else 0.0
    cfg = job.config_json if isinstance(job.config_json, dict) else {}
    return {
        "id": job.id,
        "run_id": job.run_id,
        "name": job.name,
        "source_mode": job.source_mode,
        "status": job.status,
        "current_phase": job.current_phase,
        "total_files": total,
        "tier1_count": int(job.tier1_count or 0),
        "tier2_count": int(job.tier2_count or 0),
        "tier3_count": int(job.tier3_count or 0),
        "template_clone_count": int(job.template_clone_count or 0),
        "completed_count": int(job.completed_count or 0),
        "skipped_count": int(job.skipped_count or 0),
        "failed_count": int(job.failed_count or 0),
        "processed_count": processed,
        "progress_pct": progress_pct,
        "estimated_ai_savings_pct": saved_calls_pct,
        "llm_calls_used": int(job.llm_calls_used or 0),
        "tokens_used": int(job.tokens_used or 0),
        "elapsed_seconds": int(job.elapsed_seconds or 0),
        "skip_reasons": cfg.get("skip_reasons") or {},
        "reused_count": len(cfg.get("reused_material_ids") or []),
        "error_summary": job.error_summary or "",
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


def _recount_job_tiers(session: Session, job: IngestionJobModel) -> None:
    rows = (
        session.query(SourceMaterialModel)
        .filter(SourceMaterialModel.batch_job_id == job.id)
        .all()
    )
    cfg = dict(job.config_json or {})
    reused_count = len(cfg.get("reused_material_ids") or [])
    job.total_files = len(rows) + reused_count
    job.tier1_count = sum(1 for r in rows if r.material_kind == "CODE_LOGIC" and r.tier == TIER_1_CORE)
    job.tier2_count = sum(1 for r in rows if r.material_kind == "CODE_LOGIC" and r.tier == TIER_2_UPSTREAM)
    job.tier3_count = sum(1 for r in rows if r.material_kind == "CODE_LOGIC" and r.tier == TIER_3_SKIP_AI)
    job.template_clone_count = sum(
        1 for r in rows if r.material_kind == "CODE_LOGIC" and r.skip_reason == "TEMPLATE_CLONE"
    )
    job.completed_count = sum(1 for r in rows if r.status == "PARSED") + reused_count
    job.skipped_count = sum(1 for r in rows if r.status == "SKIPPED_AI")
    job.failed_count = sum(1 for r in rows if r.status == "FAILED")


def create_batch_ingestion_job(
    session: Session,
    files: List[Dict[str, Any]],
    *,
    job_name: str = "批量代码工程接入",
    source_mode: str = "UPLOAD_BATCH",
    settings: Optional[Settings] = None,
    enable_template_dedup: bool = True,
    auto_synthesize: bool = False,
) -> Dict[str, Any]:
    """登记大批量双源材料、立即落盘 Blob，并执行 Phase 0 零 LLM 静态预扫描与 DAG 分层。"""
    if not files:
        raise ValueError("未检测到可接入的 SQL 脚本或制度文档")

    active_settings = settings or get_settings()
    blob_store = LocalBlobStore(active_settings.blob_dir)
    job_id = str(uuid.uuid4())
    run: RunModel = kernel_create_run(
        session,
        "dc_batch_ingest",
        initial_node="created",
        initial_payload={"job_id": job_id, "job_name": job_name, "source_mode": source_mode},
    )

    job = IngestionJobModel(
        id=job_id,
        run_id=run.id,
        name=job_name or "批量代码工程接入",
        source_mode=source_mode,
        status="SCANNING",
        current_phase="STATIC_SCAN",
        config_json={
            "enable_template_dedup": bool(enable_template_dedup),
            "auto_synthesize": bool(auto_synthesize),
            "reused_material_ids": [],
            "upstream_map": {},
            "topo_order": {},
            "skip_reasons": {},
        },
    )
    session.add(job)
    session.flush()

    ctx = build_batch_ingest_context(
        session,
        run,
        settings=active_settings,
        bus=default_bus(),
        shared={"job_id": job.id},
    )

    def _node_static_scan(_inp: Any, _ctx: Any) -> Outcome:
        seen_digests = set()
        staged_code_inputs: List[Tuple[str, str, str, str]] = []
        staged_materials_by_id: Dict[str, SourceMaterialModel] = {}
        reused_ids: List[str] = []

        for f in files:
            raw = f.get("content") or b""
            raw_bytes = raw if isinstance(raw, bytes) else str(raw).encode("utf-8")
            digest = hashlib.sha256(raw_bytes).hexdigest()
            if digest in seen_digests:
                continue
            seen_digests.add(digest)

            existing = session.query(SourceMaterialModel).filter(SourceMaterialModel.sha256 == digest).first()
            if existing is not None:
                if existing.status in TERMINAL_MATERIAL_STATUSES:
                    reused_ids.append(existing.id)
                    continue
                existing.batch_job_id = job.id
                if not existing.relative_path:
                    existing.relative_path = str(f.get("relative_path") or existing.title)
                existing.status = "PENDING"
                existing.error_message = ""
                staged_materials_by_id[existing.id] = existing
                if existing.material_kind == "CODE_LOGIC":
                    staged_code_inputs.append(
                        (
                            existing.id,
                            existing.title,
                            existing.relative_path or existing.title,
                            raw_bytes.decode("utf-8", errors="replace"),
                        )
                    )
                continue

            blob_uri = blob_store.put(raw_bytes)
            mat_id = str(uuid.uuid4())
            title = str(f.get("title") or "untitled")[:255]
            rel_path = str(f.get("relative_path") or title)[:512]
            mat_kind = str(f.get("material_kind") or "CODE_LOGIC")
            file_type = str(f.get("file_type") or "sql")[:16]
            row = SourceMaterialModel(
                id=mat_id,
                title=title,
                material_kind=mat_kind,
                file_type=file_type,
                sha256=digest,
                blob_uri=blob_uri,
                status="PENDING",
                summary="等待 Phase 0 静态预扫描分层" if mat_kind == "CODE_LOGIC" else "等待制度文档提取",
                item_count=0,
                batch_job_id=job.id,
                relative_path=rel_path,
                tier=TIER_1_CORE,
            )
            session.add(row)
            staged_materials_by_id[mat_id] = row
            if mat_kind == "CODE_LOGIC":
                staged_code_inputs.append(
                    (mat_id, title, rel_path, raw_bytes.decode("utf-8", errors="replace"))
                )

        session.flush()

        scan_result = scan_project_batch(
            staged_code_inputs,
            enable_template_dedup=enable_template_dedup,
            max_workers=4,
        )
        topo_order_map: Dict[str, int] = {}
        for prof in scan_result["profiles"]:
            row = staged_materials_by_id.get(prof.material_id)
            if row is None:
                continue
            row.tier = prof.tier
            row.skip_reason = prof.skip_reason
            row.ast_template_hash = prof.ast_template_hash
            row.template_master_id = prof.template_master_id
            row.target_tables_json = prof.target_tables
            row.source_tables_json = prof.source_tables
            row.status = "STATIC_INDEXED"
            row.summary = _static_summary_for_profile(prof.tier, prof.skip_reason)
            topo_order_map[prof.material_id] = prof.topo_order

        for row in staged_materials_by_id.values():
            if row.material_kind == "REGULATORY_DOC" and row.status == "PENDING":
                row.status = "STATIC_INDEXED"

        cfg = dict(job.config_json or {})
        cfg["reused_material_ids"] = reused_ids
        cfg["upstream_map"] = scan_result["upstream_map"]
        cfg["topo_order"] = topo_order_map
        cfg["skip_reasons"] = scan_result["summary"]["skip_reasons"]
        job.config_json = cfg
        _recount_job_tiers(session, job)
        job.status = "READY_REVIEW"
        job.current_phase = "STATIC_SCAN"
        session.flush()

        return Outcome(
            when="always",
            shared={"job_id": job.id, "scan_summary": scan_result["summary"]},
            output=scan_result["summary"],
            events=[
                (
                    "BATCH_SCANNED",
                    "static_scan",
                    {
                        "job_id": job.id,
                        "summary": scan_result["summary"],
                        "reused_count": len(reused_ids),
                    },
                )
            ],
        )

    def _node_tier_review_gate(_inp: Any, _ctx: Any) -> Outcome:
        return Outcome.park(
            "TIER_REVIEW_REQUIRED",
            "静态分层预扫描已完成，请确认分层优先级并启动批量解析。",
            payload={
                "job_id": job.id,
                "total_files": int(job.total_files or 0),
                "tier1_count": int(job.tier1_count or 0),
                "tier2_count": int(job.tier2_count or 0),
                "tier3_count": int(job.tier3_count or 0),
            },
            resume_node="tier_review_gate",
        )

    sched = Scheduler(
        BATCH_INGEST_PLAN,
        {
            "static_scan": _node_static_scan,
            "tier_review_gate": _node_tier_review_gate,
        },
    )
    sched.drive(ctx)
    return get_batch_job_detail(session, job.id, page=1, page_size=50)


def _static_summary_for_profile(tier: str, skip_reason: str) -> str:
    if tier == TIER_3_SKIP_AI:
        reason_text = {
            "BACKUP_FILE": "历史备份归档，已标记免模型跳过",
            "PURE_DDL": "纯结构定义语句，无业务加工规则",
            "HOUSEKEEPING_LOG": "运维日志登记语句，已免模型跳过",
            "ODS_PASSTHROUGH": "原样同步透传（无条件过滤与公式），已免模型跳过",
            "TEMPLATE_CLONE": "同构分支/年度副本，将零成本复用主模板实证",
            "ORPHAN_INTERMEDIATE": "无核心报送下游依赖的孤立中间过程，已跳过防污染版图",
        }.get(skip_reason, "已标记免模型跳过")
        return reason_text
    if tier == TIER_2_UPSTREAM:
        return "上游关键加工环节：待执行精简解析并向核心报送透传前置背景"
    return "核心报送/终表逻辑：待执行深度解析提取完整业务规则"


def override_material_tier(
    session: Session,
    job_id: str,
    material_id: str,
    new_tier: str,
) -> Dict[str, Any]:
    """允许用户在预审或暂停阶段手工覆盖单个脚本分层优先级。"""
    if new_tier not in {TIER_1_CORE, TIER_2_UPSTREAM, TIER_3_SKIP_AI}:
        raise ValueError(f"不支持的分层类型：{new_tier}")
    job = session.get(IngestionJobModel, job_id)
    if job is None:
        raise KeyError("Ingestion job not found")
    if job.status == "RUNNING":
        raise ValueError("运行中的批任务暂不允许修改分层，请先中止或等待完成")
    mat = (
        session.query(SourceMaterialModel)
        .filter(SourceMaterialModel.id == material_id, SourceMaterialModel.batch_job_id == job_id)
        .first()
    )
    if mat is None:
        raise KeyError("Material not found in this batch job")
    mat.tier = new_tier
    mat.tier_override = new_tier
    if new_tier != TIER_3_SKIP_AI:
        mat.skip_reason = ""
        mat.template_master_id = None
    elif not mat.skip_reason:
        mat.skip_reason = "MANUAL_SKIP"
    if mat.status in {"SKIPPED_AI", "FAILED"}:
        mat.status = "STATIC_INDEXED"
    mat.summary = _static_summary_for_profile(mat.tier, mat.skip_reason)
    _recount_job_tiers(session, job)
    session.commit()
    return _material_item_to_dict(mat)


def _material_item_to_dict(r: SourceMaterialModel) -> Dict[str, Any]:
    return {
        "id": r.id,
        "title": r.title,
        "relative_path": r.relative_path or r.title,
        "material_kind": r.material_kind,
        "file_type": r.file_type,
        "status": r.status,
        "tier": r.tier,
        "tier_override": r.tier_override,
        "skip_reason": r.skip_reason,
        "template_master_id": r.template_master_id,
        "item_count": r.item_count,
        "summary": r.summary,
        "error_message": r.error_message or "",
    }


def list_batch_jobs(session: Session, limit: int = 20) -> List[Dict[str, Any]]:
    rows = (
        session.query(IngestionJobModel)
        .order_by(IngestionJobModel.created_at.desc())
        .limit(max(1, min(100, int(limit))))
        .all()
    )
    return [_job_to_dict(j) for j in rows]


def get_batch_job_detail(
    session: Session,
    job_id: str,
    *,
    tier: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
) -> Dict[str, Any]:
    job = session.get(IngestionJobModel, job_id)
    if job is None:
        raise KeyError("Ingestion job not found")
    q = session.query(SourceMaterialModel).filter(SourceMaterialModel.batch_job_id == job_id)
    if tier:
        q = q.filter(SourceMaterialModel.tier == tier)
    if status:
        q = q.filter(SourceMaterialModel.status == status)
    total_items = q.count()
    pg = max(1, int(page))
    ps = max(1, min(200, int(page_size)))
    rows = (
        q.order_by(SourceMaterialModel.tier.asc(), SourceMaterialModel.relative_path.asc())
        .offset((pg - 1) * ps)
        .limit(ps)
        .all()
    )
    return {
        "job": _job_to_dict(job),
        "materials": [_material_item_to_dict(r) for r in rows],
        "pagination": {
            "page": pg,
            "page_size": ps,
            "total": total_items,
            "total_pages": max(1, (total_items + ps - 1) // ps),
        },
    }


def _build_upstream_digest_from_evidences(evidences: Sequence[Any]) -> Optional[Dict[str, Any]]:
    """把已提取的一份上游逻辑实证浓缩为纯中文业务摘要，供下游 Tier 1 参考。"""
    if not evidences:
        return None
    lead = evidences[0]
    if hasattr(lead, "business_domain_hint"):
        domain = lead.business_domain_hint
        concept = lead.target_concept_hint
        source_sum = lead.source_summary
        inclusions = getattr(lead, "inclusion_predicates", []) or []
        exclusions = getattr(lead, "exclusion_predicates", []) or []
    else:
        domain = lead.business_domain_hint
        concept = lead.target_concept_hint
        source_sum = lead.source_summary
        inclusions = getattr(lead, "inclusion_predicates_json", []) or []
        exclusions = getattr(lead, "exclusion_predicates_json", []) or []

    stage_topic = sanitize_business_text(f"{domain}-{concept}" if concept and concept != domain else domain)[:40]
    biz_sum = sanitize_business_text(str(source_sum or ""))[:140]
    rules: List[str] = []
    for item in list(exclusions)[:2] + list(inclusions)[:2]:
        if isinstance(item, dict):
            txt = f"{item.get('title', '')}：{item.get('condition', '')}".strip("：")
            clean = sanitize_business_text(txt)[:100]
            if clean and clean not in rules:
                rules.append(clean)
    if not biz_sum and not rules:
        return None
    return {
        "stage_topic": stage_topic or "上游业务加工",
        "business_summary": biz_sum,
        "key_rules": rules,
    }


def _collect_upstream_contexts(
    mat_id: str,
    upstream_map: Dict[str, List[str]],
    upstream_summaries: Dict[str, Dict[str, Any]],
    *,
    max_hops: int = 2,
) -> List[Dict[str, Any]]:
    contexts: List[Dict[str, Any]] = []
    seen = {mat_id}
    queue = deque((uid, 1) for uid in upstream_map.get(mat_id, []))
    while queue and len(contexts) < 6:
        uid, hop = queue.popleft()
        if uid in seen:
            continue
        seen.add(uid)
        digest = upstream_summaries.get(uid)
        if digest is not None:
            contexts.append(digest)
        if hop < max_hops:
            for prev_id in upstream_map.get(uid, []):
                if prev_id not in seen:
                    queue.append((prev_id, hop + 1))
    return contexts


def _seed_existing_upstream_summaries(
    session: Session,
    job_id: str,
    reused_ids: List[str],
) -> Dict[str, Dict[str, Any]]:
    """断点续跑时从 SQLite 恢复已完成上游脚本的纯中文业务摘要，保证下游 Tier 1 不断链。"""
    parsed_ids = [
        r.id
        for r in session.query(SourceMaterialModel)
        .filter(
            SourceMaterialModel.batch_job_id == job_id,
            SourceMaterialModel.status == "PARSED",
            SourceMaterialModel.material_kind == "CODE_LOGIC",
        )
        .all()
    ] + list(reused_ids or [])
    if not parsed_ids:
        return {}
    summaries: Dict[str, Dict[str, Any]] = {}
    # 分块避免单次 IN 参数过长
    for start in range(0, len(parsed_ids), 250):
        chunk_ids = parsed_ids[start : start + 250]
        evs = (
            session.query(LogicEvidenceModel)
            .filter(LogicEvidenceModel.material_id.in_(chunk_ids))
            .all()
        )
        by_mat: Dict[str, List[LogicEvidenceModel]] = {}
        for ev in evs:
            by_mat.setdefault(ev.material_id, []).append(ev)
        for mid, mat_evs in by_mat.items():
            digest = _build_upstream_digest_from_evidences(mat_evs)
            if digest is not None:
                summaries[mid] = digest
    return summaries


def _worker_parse_single(
    title: str,
    material_kind: str,
    raw_bytes: bytes,
    *,
    active_llm: Any,
    script_bus: Bus,
    execution_mode: str,
    upstream_context: Optional[List[Dict[str, Any]]],
    cancellation: Optional[CancellationToken],
) -> List[Any]:
    """纯计算/HTTP Worker：绝不接触 SQLAlchemy Session，全量在隔离 Bus 与取消令牌下运行。"""
    with llm_execution_scope(bus=script_bus, cancellation=cancellation):
        if material_kind == "REGULATORY_DOC":
            return parse_regulatory_document(title, raw_bytes, llm=active_llm, bus=script_bus)
        return parse_code_logic(
            title,
            raw_bytes,
            llm=active_llm,
            bus=script_bus,
            execution_mode=execution_mode,
            upstream_context=upstream_context,
        )


def _sync_job_guard_metrics(job: IngestionJobModel, job_bus: Bus, started_monotonic: float) -> None:
    guard = job_bus.budget_guard
    if guard is not None:
        snap = guard.snapshot()
        job.llm_calls_used = int(snap.get("llm_calls") or 0)
        job.tokens_used = int(snap.get("total_tokens") or 0)
    job.elapsed_seconds = max(0, int(time.monotonic() - started_monotonic))


def _drive_wave(
    session: Session,
    job: IngestionJobModel,
    run: RunModel,
    materials: List[SourceMaterialModel],
    *,
    execution_mode: str,
    phase_name: str,
    blob_store: LocalBlobStore,
    active_llm: Any,
    job_bus: Bus,
    per_script_calls: int,
    per_script_elapsed: int,
    workers: int,
    cancel_token: CancellationToken,
    upstream_map: Dict[str, List[str]],
    upstream_summaries: Dict[str, Dict[str, Any]],
    has_tier1_downstream: Dict[str, bool],
    started_monotonic: float,
    affected_domains: set,
) -> str:
    """运行单波次（滑动窗口并发 + 主线程逐脚本 SQLite Checkpoint）。

    返回状态：`OK` | `CANCELLED` | `PAUSED_BUDGET`。
    """
    if not materials:
        return "OK"
    job.current_phase = phase_name
    session.commit()

    max_in_flight = max(workers, workers * 2)
    pending_futs: Dict[Future, Tuple[SourceMaterialModel, Bus]] = {}
    last_sse_at = 0.0
    since_last_ckpt_event = 0
    stop_state = "OK"

    def _emit_progress(force: bool = False) -> None:
        nonlocal last_sse_at
        now = time.monotonic()
        if not force and (now - last_sse_at) < 0.5:
            return
        last_sse_at = now
        global_bus().fire("batch.progress", _job_to_dict(job))

    def _commit_completed_future(fut: Future, mat: SourceMaterialModel, script_bus: Bus) -> Optional[str]:
        nonlocal since_last_ckpt_event
        script_tag = f"script:{mat.id}"
        try:
            parsed_items = fut.result()
        except RunCancelledError:
            mat.status = "STATIC_INDEXED"
            session.commit()
            return "CANCELLED"
        except ProviderAuthOrBillingError as exc:
            mat.status = "STATIC_INDEXED"
            mat.error_message = ""
            job.error_summary = f"模型服务账户余额不足或鉴权失效，批任务已安全暂停：{exc}"[:500]
            _sync_job_guard_metrics(job, job_bus, started_monotonic)
            session.commit()
            return "PAUSED_BILLING"
        except BudgetExceededError as exc:
            is_script_breach = exc.run_id == script_tag
            if not is_script_breach and job_bus.budget_guard is not None:
                # 兜底校验：若批任务预算并未触限而脚本子预算超限，同样只隔离该脚本
                jg = job_bus.budget_guard
                if jg._find_breach(jg.snapshot()) is None:
                    is_script_breach = True
            if is_script_breach:
                mat.status = "FAILED"
                mat.error_message = f"单脚本调用超出保护上限，已跳过以保护整批：{exc}"
                job.failed_count = int(job.failed_count or 0) + 1
                _sync_job_guard_metrics(job, job_bus, started_monotonic)
                session.commit()
                return None
            mat.status = "STATIC_INDEXED"
            _sync_job_guard_metrics(job, job_bus, started_monotonic)
            session.commit()
            return "PAUSED_BUDGET"
        except Exception as exc:  # noqa: BLE001
            mat.status = "FAILED"
            mat.error_message = f"{type(exc).__name__}: {exc}"[:500]
            job.failed_count = int(job.failed_count or 0) + 1
            _sync_job_guard_metrics(job, job_bus, started_monotonic)
            session.commit()
            return None

        # 成功解析：补充 Tier 2 标记与影响域，立即落库
        if execution_mode == "lite":
            absorbed = bool(has_tier1_downstream.get(mat.id))
            for ev in parsed_items:
                if hasattr(ev, "internal_locator") and isinstance(ev.internal_locator, dict):
                    ev.internal_locator["tier"] = TIER_2_UPSTREAM
                    ev.internal_locator["absorbed_by_downstream"] = absorbed
            digest = _build_upstream_digest_from_evidences(parsed_items)
            if digest is not None:
                upstream_summaries[mat.id] = digest
        elif mat.material_kind == "CODE_LOGIC":
            for ev in parsed_items:
                if hasattr(ev, "internal_locator") and isinstance(ev.internal_locator, dict):
                    ev.internal_locator["tier"] = mat.tier
            digest = _build_upstream_digest_from_evidences(parsed_items)
            if digest is not None:
                upstream_summaries[mat.id] = digest

        for item in parsed_items:
            dom = getattr(item, "business_domain_hint", None) or getattr(item, "business_domain", None)
            if dom:
                affected_domains.add(dom)

        _persist_material_items(session, mat, mat.material_kind, parsed_items)
        mat.status = "PARSED"
        mat.error_message = ""
        job.completed_count = int(job.completed_count or 0) + 1
        _sync_job_guard_metrics(job, job_bus, started_monotonic)
        since_last_ckpt_event += 1
        if since_last_ckpt_event >= 25:
            since_last_ckpt_event = 0
            append_event(
                session,
                run,
                "BATCH_CHECKPOINT",
                phase_name,
                {"job_id": job.id, **_job_to_dict(job)},
            )
        session.commit()
        _emit_progress(force=False)
        return None

    idx = 0
    total = len(materials)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        while idx < total or pending_futs:
            while idx < total and len(pending_futs) < max_in_flight:
                if cancel_token.is_cancelled:
                    stop_state = "CANCELLED"
                    break
                try:
                    job_bus.check_budget()
                except BudgetExceededError:
                    stop_state = "PAUSED_BUDGET"
                    break

                mat = materials[idx]
                idx += 1
                mat.status = "PARSING"
                session.commit()

                try:
                    raw_bytes = blob_store.get(mat.blob_uri)
                except Exception as exc:  # noqa: BLE001
                    mat.status = "FAILED"
                    mat.error_message = f"读取文件存储失败：{exc}"
                    job.failed_count = int(job.failed_count or 0) + 1
                    session.commit()
                    continue

                up_ctx = (
                    _collect_upstream_contexts(mat.id, upstream_map, upstream_summaries, max_hops=2)
                    if (execution_mode == "full" and mat.material_kind == "CODE_LOGIC")
                    else None
                )
                script_bus = build_script_child_bus(
                    per_script_calls,
                    max_elapsed_seconds=per_script_elapsed,
                    parent=job_bus,
                    script_tag=f"script:{mat.id}",
                )
                fut = submit_with_context(
                    pool,
                    _worker_parse_single,
                    mat.title,
                    mat.material_kind,
                    raw_bytes,
                    active_llm=active_llm,
                    script_bus=script_bus,
                    execution_mode=execution_mode,
                    upstream_context=up_ctx,
                    cancellation=cancel_token,
                )
                pending_futs[fut] = (mat, script_bus)

            if not pending_futs:
                break

            wait_timeout = None if stop_state == "OK" else 30.0
            done, _ = wait(list(pending_futs.keys()), timeout=wait_timeout, return_when=FIRST_COMPLETED)
            if not done:
                break
            for fut in done:
                mat, s_bus = pending_futs.pop(fut)
                signal = _commit_completed_future(fut, mat, s_bus)
                if signal in {"CANCELLED", "PAUSED_BUDGET", "PAUSED_BILLING"} and stop_state == "OK":
                    stop_state = signal
            if stop_state != "OK" and not pending_futs:
                break

        # 若因中止/预算停止了派发，重置未完成在途脚本状态
        for fut, (mat, _sb) in list(pending_futs.items()):
            fut.cancel()
            if mat.status == "PARSING":
                mat.status = "STATIC_INDEXED"
                session.commit()

    _emit_progress(force=True)
    return stop_state


def _clone_master_evidences_for_siblings(
    session: Session,
    job: IngestionJobModel,
    clone_materials: List[SourceMaterialModel],
) -> None:
    """Wave 3：同构模板副本 0 LLM 调用直接继承 Master 实证，保留各自物理定位以便回溯。"""
    if not clone_materials:
        return
    job.current_phase = "TEMPLATE_CLONE"
    master_ids = list({m.template_master_id for m in clone_materials if m.template_master_id})
    masters_by_id: Dict[str, SourceMaterialModel] = {}
    master_evs: Dict[str, List[LogicEvidenceModel]] = {}
    if master_ids:
        for m in session.query(SourceMaterialModel).filter(SourceMaterialModel.id.in_(master_ids)).all():
            masters_by_id[m.id] = m
        for ev in session.query(LogicEvidenceModel).filter(LogicEvidenceModel.material_id.in_(master_ids)).all():
            master_evs.setdefault(ev.material_id, []).append(ev)

    for sib in clone_materials:
        m_id = sib.template_master_id or ""
        master_mat = masters_by_id.get(m_id)
        evs = master_evs.get(m_id) or []
        if master_mat is None or master_mat.status != "PARSED" or not evs:
            sib.status = "SKIPPED_AI"
            sib.item_count = 0
            sib.summary = "已标记同构模板跳过（未生成额外模型调用）"
            job.skipped_count = int(job.skipped_count or 0) + 1
            continue

        cloned_cnt = 0
        for orig in evs:
            fp = hashlib.sha256(f"clone:{sib.id}:{orig.logic_fingerprint}".encode("utf-8")).hexdigest()
            if session.query(LogicEvidenceModel).filter(LogicEvidenceModel.logic_fingerprint == fp).first():
                continue
            loc = dict(orig.internal_locator_json or {})
            loc["script"] = sib.relative_path or sib.title
            loc["target_tables"] = list(sib.target_tables_json or loc.get("target_tables") or [])
            loc["source_tables"] = list(sib.source_tables_json or loc.get("source_tables") or [])
            loc["tier"] = TIER_3_SKIP_AI
            loc["is_template_clone"] = True
            loc["cloned_from_master_id"] = m_id
            clone_row = LogicEvidenceModel(
                id=str(uuid.uuid4()),
                material_id=sib.id,
                logic_fingerprint=fp,
                business_domain_hint=orig.business_domain_hint,
                target_concept_hint=orig.target_concept_hint,
                source_summary=orig.source_summary,
                inclusion_predicates_json=list(orig.inclusion_predicates_json or []),
                exclusion_predicates_json=list(orig.exclusion_predicates_json or []),
                branch_cases_json=list(orig.branch_cases_json or []),
                calculation_kind=orig.calculation_kind,
                calculation_summary=orig.calculation_summary,
                dedup_grain_hint=orig.dedup_grain_hint,
                internal_locator_json=loc,
            )
            try:
                with session.begin_nested():
                    session.add(clone_row)
                    session.flush()
                cloned_cnt += 1
            except IntegrityError:
                pass
        sib.status = "SKIPPED_AI"
        sib.item_count = cloned_cnt
        sib.summary = f"已零模型调用复用同构主模板（继承 {cloned_cnt} 组实证）"
        job.skipped_count = int(job.skipped_count or 0) + 1

    session.commit()


def _drive_batch_job(
    session: Session,
    run: RunModel,
    cancel_token: CancellationToken,
    *,
    job_id: str,
    settings: Optional[Settings] = None,
    llm: Any = None,
) -> Dict[str, Any]:
    job = session.get(IngestionJobModel, job_id)
    if job is None:
        raise ValueError(f"Ingestion job not found: {job_id}")

    started_monotonic = time.monotonic()
    active_settings = get_effective_settings(session, settings or get_settings())
    active_llm = _resolve_optional_llm(session, active_settings, llm)
    budget = get_budget(session)
    concurrency = max(1, min(16, int(budget.get("max_llm_concurrency", 6))))
    rpm = int(budget.get("max_llm_rpm", 60))
    per_script_calls = max(1, int(budget.get("max_calls_per_script", 8)))
    per_script_elapsed = max(30, min(900, int(budget.get("max_elapsed_seconds", 7200))))

    configure_llm_concurrency(concurrency)
    configure_llm_rate_limit(rpm if active_llm is not None else 0)
    workers = concurrency if active_llm is not None else min(4, concurrency)
    job_bus = build_run_bus(budget, parent=global_bus(), run_id=run.id)

    cfg = dict(job.config_json or {})
    upstream_map: Dict[str, List[str]] = cfg.get("upstream_map") or {}
    topo_order: Dict[str, int] = cfg.get("topo_order") or {}
    reused_ids: List[str] = cfg.get("reused_material_ids") or []
    blob_store = LocalBlobStore(active_settings.blob_dir)

    all_mats = (
        session.query(SourceMaterialModel)
        .filter(SourceMaterialModel.batch_job_id == job_id)
        .all()
    )
    tier_by_id = {m.id: m.tier for m in all_mats}
    has_tier1_downstream: Dict[str, bool] = {}
    for mid, ups in upstream_map.items():
        if tier_by_id.get(mid) == TIER_1_CORE:
            for u in ups:
                has_tier1_downstream[u] = True

    upstream_summaries = _seed_existing_upstream_summaries(session, job_id, reused_ids)
    affected_domains: set = set()

    # Wave 0: 非同构克隆类 Tier 3 立即静默跳过
    non_clone_tier3 = [
        m
        for m in all_mats
        if m.material_kind == "CODE_LOGIC"
        and m.tier == TIER_3_SKIP_AI
        and not m.template_master_id
        and m.status not in TERMINAL_MATERIAL_STATUSES
    ]
    if non_clone_tier3:
        for m in non_clone_tier3:
            m.status = "SKIPPED_AI"
            m.item_count = 0
        _recount_job_tiers(session, job)
        session.commit()

    def _clone_ready_siblings() -> int:
        clone_mats = [
            m
            for m in all_mats
            if m.material_kind == "CODE_LOGIC"
            and m.tier == TIER_3_SKIP_AI
            and m.template_master_id
            and m.status not in TERMINAL_MATERIAL_STATUSES
        ]
        if not clone_mats:
            return 0
        parsed_master_ids = {m.id for m in all_mats if m.status == "PARSED"}
        ready_clones = [c for c in clone_mats if c.template_master_id in parsed_master_ids]
        if ready_clones:
            _clone_master_evidences_for_siblings(session, job, ready_clones)
        return len(ready_clones)

    def _handle_stop_state(stop_state: str, phase_name: str, processed_count: int) -> Outcome:
        if stop_state == "CANCELLED":
            _recount_job_tiers(session, job)
            _sync_job_guard_metrics(job, job_bus, started_monotonic)
            job.status = "CANCELLED"
            job.current_phase = "CANCELLED"
            job.error_summary = cancel_token.reason
            session.commit()
            raise RunCancelledError(cancel_token.reason)
        if stop_state in {"PAUSED_BUDGET", "PAUSED_BILLING"}:
            _clone_ready_siblings()
            _recount_job_tiers(session, job)
            _sync_job_guard_metrics(job, job_bus, started_monotonic)
            job.status = "PAUSED_BUDGET"
            job.current_phase = "PAUSED_BUDGET"
            if stop_state == "PAUSED_BUDGET":
                job.error_summary = "批任务达到执行预算上限已安全暂停（已解析脚本全部保留），调高预算后可一键断点续跑。"
            elif not job.error_summary:
                job.error_summary = "模型账户余额不足或鉴权失效，批任务已安全暂停（已解析脚本全部保留），处理后可一键断点续跑。"
            snap = _job_to_dict(job)
            return Outcome(
                when="paused",
                shared={"pause_reason": stop_state, "phase_stats": {phase_name: processed_count}},
                output={"pause_reason": stop_state},
                events=[("BATCH_PAUSED", "paused", snap)],
            )
        return Outcome(
            when="always",
            shared={"phase_stats": {phase_name: processed_count}},
            output={"phase": phase_name, "count": processed_count},
        )

    def _node_tier_review_gate(_inp: Any, local_ctx: Any) -> Outcome:
        if local_ctx.resume_value is not None:
            return Outcome(when="always", output={"resumed_from": "tier_review_gate"})
        return Outcome.park(
            "TIER_REVIEW_REQUIRED",
            "请确认分层优先级并启动解析。",
            payload={"job_id": job.id},
            resume_node="tier_review_gate",
        )

    def _node_budget_pause_gate(_inp: Any, local_ctx: Any) -> Outcome:
        if local_ctx.resume_value is not None:
            return Outcome(when="always", output={"resumed_from": "budget_pause_gate"})
        return Outcome.park(
            "BATCH_PAUSE_REQUIRED",
            "批任务达到预算阈值或模型余额受限，已保留全部已完成检查点，调整预算后可断点续跑。",
            payload={
                "job_id": job.id,
                "pause_reason": str(local_ctx.get("pause_reason") or "PAUSED_BUDGET"),
                "completed_count": int(job.completed_count or 0),
            },
            resume_node="budget_pause_gate",
        )

    def _node_doc_wave(_inp: Any, _ctx: Any) -> Outcome:
        pending_docs = [
            m
            for m in all_mats
            if m.material_kind == "REGULATORY_DOC" and m.status not in TERMINAL_MATERIAL_STATUSES
        ]
        stop_state = _drive_wave(
            session,
            job,
            run,
            pending_docs,
            execution_mode="full",
            phase_name="DOC_PARSE",
            blob_store=blob_store,
            active_llm=active_llm,
            job_bus=job_bus,
            per_script_calls=per_script_calls * 2,
            per_script_elapsed=per_script_elapsed,
            workers=workers,
            cancel_token=cancel_token,
            upstream_map=upstream_map,
            upstream_summaries=upstream_summaries,
            has_tier1_downstream=has_tier1_downstream,
            started_monotonic=started_monotonic,
            affected_domains=affected_domains,
        )
        return _handle_stop_state(stop_state, "doc_wave", len(pending_docs))

    def _node_tier2_wave(_inp: Any, _ctx: Any) -> Outcome:
        tier2_mats = sorted(
            [
                m
                for m in all_mats
                if m.material_kind == "CODE_LOGIC"
                and m.tier == TIER_2_UPSTREAM
                and m.status not in TERMINAL_MATERIAL_STATUSES
            ],
            key=lambda m: (topo_order.get(m.id, 999999), m.relative_path or m.title),
        )
        stop_state = _drive_wave(
            session,
            job,
            run,
            tier2_mats,
            execution_mode="lite",
            phase_name="TIER2_PARSE",
            blob_store=blob_store,
            active_llm=active_llm,
            job_bus=job_bus,
            per_script_calls=per_script_calls,
            per_script_elapsed=per_script_elapsed,
            workers=workers,
            cancel_token=cancel_token,
            upstream_map=upstream_map,
            upstream_summaries=upstream_summaries,
            has_tier1_downstream=has_tier1_downstream,
            started_monotonic=started_monotonic,
            affected_domains=affected_domains,
        )
        return _handle_stop_state(stop_state, "tier2_wave", len(tier2_mats))

    def _node_tier1_wave(_inp: Any, _ctx: Any) -> Outcome:
        tier1_mats = sorted(
            [
                m
                for m in all_mats
                if m.material_kind == "CODE_LOGIC"
                and m.tier == TIER_1_CORE
                and m.status not in TERMINAL_MATERIAL_STATUSES
            ],
            key=lambda m: (topo_order.get(m.id, 999999), m.relative_path or m.title),
        )
        stop_state = _drive_wave(
            session,
            job,
            run,
            tier1_mats,
            execution_mode="full",
            phase_name="TIER1_PARSE",
            blob_store=blob_store,
            active_llm=active_llm,
            job_bus=job_bus,
            per_script_calls=per_script_calls,
            per_script_elapsed=per_script_elapsed,
            workers=workers,
            cancel_token=cancel_token,
            upstream_map=upstream_map,
            upstream_summaries=upstream_summaries,
            has_tier1_downstream=has_tier1_downstream,
            started_monotonic=started_monotonic,
            affected_domains=affected_domains,
        )
        return _handle_stop_state(stop_state, "tier1_wave", len(tier1_mats))

    def _node_clone_wave(_inp: Any, _ctx: Any) -> Outcome:
        clone_mats = [
            m
            for m in all_mats
            if m.material_kind == "CODE_LOGIC"
            and m.tier == TIER_3_SKIP_AI
            and m.template_master_id
            and m.status not in TERMINAL_MATERIAL_STATUSES
        ]
        _clone_master_evidences_for_siblings(session, job, clone_mats)
        _recount_job_tiers(session, job)
        _sync_job_guard_metrics(job, job_bus, started_monotonic)
        if cfg.get("auto_synthesize"):
            job.current_phase = "SYNTHESIZING"
            session.commit()
            return Outcome(
                when="synthesize",
                shared={"phase_stats": {"clone_wave": len(clone_mats)}},
                output={"cloned": len(clone_mats)},
            )
        return Outcome(
            when="always",
            shared={"phase_stats": {"clone_wave": len(clone_mats)}},
            output={"cloned": len(clone_mats)},
        )

    def _node_auto_synthesize(_inp: Any, _ctx: Any) -> Outcome:
        syn_summary = synthesize_business_assets(
            session,
            settings=active_settings,
            llm=active_llm,
            target_domains=sorted(affected_domains) if affected_domains else None,
        )
        return Outcome(
            when="always",
            shared={"synthesis_summary": syn_summary},
            output=syn_summary,
        )

    def _node_finalize_batch(_inp: Any, _ctx: Any) -> Outcome:
        _recount_job_tiers(session, job)
        _sync_job_guard_metrics(job, job_bus, started_monotonic)
        if job.failed_count > 0 and job.completed_count == 0 and job.skipped_count == 0:
            job.status = "FAILED"
            job.error_summary = "批任务中待解析脚本均失败，请查看失败详情或调整配置后重试。"
        else:
            job.status = "COMPLETED"
            job.current_phase = "DONE"
            job.error_summary = (
                f"批任务完成，另有 {job.failed_count} 份脚本异常支持单独重试。" if job.failed_count > 0 else ""
            )
        session.flush()
        snap = _job_to_dict(job)
        return Outcome(
            output=snap,
            events=[("BATCH_COMPLETED", "finalize_batch", snap)],
        )

    ctx = build_batch_ingest_context(
        session,
        run,
        settings=active_settings,
        llm=active_llm,
        bus=default_bus(),
        concurrency=workers,
        cancellation=cancel_token,
        shared={"job_id": job.id, "execution_mode": "full"},
    )
    sched = Scheduler(
        BATCH_INGEST_PLAN,
        {
            "tier_review_gate": _node_tier_review_gate,
            "budget_pause_gate": _node_budget_pause_gate,
            "doc_wave": _node_doc_wave,
            "tier2_wave": _node_tier2_wave,
            "tier1_wave": _node_tier1_wave,
            "clone_wave": _node_clone_wave,
            "auto_synthesize": _node_auto_synthesize,
            "finalize_batch": _node_finalize_batch,
        },
    )
    try:
        if (
            ctx.state.pending_target in {"tier_review_gate", "budget_pause_gate"}
            or ctx.state.status in {"WAITING_REVIEW", "WAITING_BUDGET"}
        ):
            target_gate = ctx.state.pending_target or (
                "budget_pause_gate" if ctx.state.status == "WAITING_BUDGET" else "tier_review_gate"
            )
            sched.resume(
                ctx,
                resume_value={"action": "resume"},
                resume_nodes=[target_gate],
                answer_kind="BATCH_STARTED",
                answer_payload={"job_id": job.id},
            )
        else:
            ctx.record_event("BATCH_STARTED", "doc_wave", {"job_id": job.id})
            ctx.flush()
            ctx.refresh_state()
            sched.drive(ctx, initial_wave=[Send("doc_wave", None)])
    except BaseException as exc:
        root = unwrap_wave_error(exc)
        if root is not exc:
            raise root from exc
        raise
    return _job_to_dict(job)


def start_batch_job(
    session: Session,
    job_id: str,
    *,
    settings: Optional[Settings] = None,
    llm: Any = None,
    inline: bool = False,
    session_factory: Optional[Callable[[], Session]] = None,
) -> Dict[str, Any]:
    """启动已预扫描的批处理任务；默认走 TaskRunner 后台异步执行，测试可传 inline=True。"""
    job = session.get(IngestionJobModel, job_id)
    if job is None:
        raise KeyError("Ingestion job not found")
    if job.status == "RUNNING":
        return _job_to_dict(job)
    run = session.get(RunModel, job.run_id)
    if run is None:
        raise ValueError("Run record missing for batch job")

    job.status = "RUNNING"
    job.current_phase = "TIER2_PARSE" if int(job.tier2_count or 0) > 0 else "TIER1_PARSE"
    job.error_summary = ""
    session.commit()

    if inline:
        token = CancellationToken()
        try:
            _drive_batch_job(
                session,
                run,
                token,
                job_id=job.id,
                settings=settings,
                llm=llm,
            )
        except RunCancelledError:
            pass
        session.refresh(job)
        return _job_to_dict(job)

    factory = session_factory or SessionLocal

    def _driver(bg_session: Session, bg_run: RunModel, token: CancellationToken) -> Any:
        try:
            return _drive_batch_job(
                bg_session,
                bg_run,
                token,
                job_id=job.id,
                settings=settings,
                llm=llm,
            )
        except RunCancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            bg_job = bg_session.get(IngestionJobModel, job.id)
            if bg_job is not None and bg_job.status == "RUNNING":
                bg_job.status = "FAILED"
                bg_job.error_summary = f"{type(exc).__name__}: {exc}"[:500]
                bg_session.commit()
            raise

    submit_run(
        job.run_id,
        factory,
        _driver,
        allowed_statuses=("RUNNING", "READY_REVIEW", "WAITING_REVIEW", "PAUSED_BUDGET", "WAITING_BUDGET", "FAILED"),
        projector=project_dc_plan,
    )
    return _job_to_dict(job)


def cancel_batch_job(session: Session, job_id: str, *, reason: str = "操作员手动中止批任务") -> Dict[str, Any]:
    job = session.get(IngestionJobModel, job_id)
    if job is None:
        raise KeyError("Ingestion job not found")
    run = session.get(RunModel, job.run_id)
    if run is not None:
        try:
            cancel_run(
                session,
                run,
                active_statuses=("RUNNING", "READY_REVIEW", "WAITING_REVIEW", "PAUSED_BUDGET", "WAITING_BUDGET"),
                reason=reason,
                projector=project_dc_plan,
            )
        except ValueError:
            pass
    if job.status in {"RUNNING", "READY_REVIEW", "PAUSED_BUDGET"}:
        job.status = "CANCELLED"
        job.error_summary = reason
        session.commit()
    return _job_to_dict(job)


def resume_batch_job(
    session: Session,
    job_id: str,
    *,
    mode: str = "pending_and_failed",
    settings: Optional[Settings] = None,
    llm: Any = None,
    inline: bool = False,
    session_factory: Optional[Callable[[], Session]] = None,
) -> Dict[str, Any]:
    """从断点恢复批任务：已 PARSED / SKIPPED_AI 脚本零重复调用，仅补跑未完成/失败脚本。"""
    if mode not in {"pending_and_failed", "failed_only"}:
        raise ValueError(f"不支持的续跑模式：{mode}")
    job = session.get(IngestionJobModel, job_id)
    if job is None:
        raise KeyError("Ingestion job not found")
    if job.status == "RUNNING":
        return _job_to_dict(job)

    rows = (
        session.query(SourceMaterialModel)
        .filter(SourceMaterialModel.batch_job_id == job_id)
        .all()
    )
    if mode == "failed_only":
        for r in rows:
            if r.status == "FAILED":
                r.status = "STATIC_INDEXED"
                r.error_message = ""
            elif r.status in {"PENDING", "STATIC_INDEXED", "PARSING"} and r.tier != TIER_3_SKIP_AI:
                # 仅重跑失败项时跳过未开始项
                pass
    else:
        for r in rows:
            if r.status in {"FAILED", "PARSING", "PENDING"}:
                r.status = "STATIC_INDEXED"
                r.error_message = ""

    _recount_job_tiers(session, job)
    session.commit()
    return start_batch_job(
        session,
        job_id,
        settings=settings,
        llm=llm,
        inline=inline,
        session_factory=session_factory,
    )


def recover_stale_batch_jobs(session: Session) -> Dict[str, int]:
    """服务重启自愈：将遗留 `PARSING` 重置为可续跑态，将僵尸 `RUNNING` 任务收敛为可恢复失败态。"""
    stale_mats = (
        session.query(SourceMaterialModel)
        .filter(SourceMaterialModel.status == "PARSING")
        .all()
    )
    for m in stale_mats:
        m.status = "STATIC_INDEXED"

    stale_jobs = (
        session.query(IngestionJobModel)
        .filter(IngestionJobModel.status == "RUNNING")
        .all()
    )
    stale_run_ids = [j.run_id for j in stale_jobs if j.run_id]
    stale_runs = (
        session.query(RunModel).filter(RunModel.id.in_(stale_run_ids)).all()
        if stale_run_ids
        else []
    )
    recovered_runs = recover_interrupted_runs(
        session,
        ("RUNNING",),
        runs=stale_runs,
        projector_many=lambda s, rs: {r.id: project_dc_plan(s, r) for r in rs},
        reason="服务重启导致批任务中断，已保留全部已解析进度",
        commit=False,
    )
    for job in stale_jobs:
        _recount_job_tiers(session, job)
        job.status = "FAILED"
        job.error_summary = "服务重启导致批任务中断，已保留全部已解析进度，可一键断点续跑。"
    session.commit()
    return {"reset_materials": len(stale_mats), "recovered_jobs": len(stale_jobs), "recovered_runs": len(recovered_runs)}

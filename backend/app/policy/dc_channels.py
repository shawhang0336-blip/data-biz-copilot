"""dc_agent 策略层通道契约与上下文构造器（策略层 Layer 2，零 import 期全局副作用）。

为四套 `WorkflowPlan` 定义显式命名 `ChannelSpec` 与并行归约语义：
- 多写者安全通道（`merge_dict` / `add_number`）专供并发 `Send` 波次在 BSP 屏障确定性汇总；
- 单写者通道（`last_value`）在同波次多写时由微内核抛出 `AmbiguousWriteError`；
- 外层与内层 `ctx` 自身不挂载 `budget_guard`（LLM 预算熔断仍由 `llm_execution_scope(bus=llm_bus)` 精准计数），
  确保高成本 LLM 调用后的收尾落库/实体封板节点不会被误拦截。
"""

from typing import Any, Callable, Dict, Optional

from sqlalchemy.orm import Session

from app.infra.db_tables import RunModel
from app.kernel.bus import Bus
from app.kernel.event_log import RunState
from app.kernel.run import (
    ChannelSpec,
    RunContext,
    add_number,
    create_memory_context,
    last_value,
    merge_dict,
)
from .dc_bus import default_bus
from .dc_plan import _apply, project


CODE_PARSE_CHANNELS: Dict[str, ChannelSpec] = {
    "filename": ChannelSpec("filename", last_value),  # 代码脚本文件名
    "raw_text": ChannelSpec("raw_text", last_value),  # 代码文本快照
    "execution_mode": ChannelSpec("execution_mode", last_value),  # 解析执行模式（fast/full）
    "upstream_context": ChannelSpec("upstream_context", last_value),  # 纯中文上游摘要列表
    "chunk_count": ChannelSpec("chunk_count", last_value),  # 逻辑块数量
    "code_plan": ChannelSpec("code_plan", last_value),  # AI 结构规划骨架
    "evidences": ChannelSpec("evidences", last_value),  # 脚本内多步骤初始逻辑实证
    "stage_results": ChannelSpec("stage_results", merge_dict, allow_multi=True),  # 并发分阶段翻译结果
    "translated_count": ChannelSpec("translated_count", add_number, allow_multi=True),  # 并发翻译累计步数
    "final_evidences": ChannelSpec("final_evidences", last_value),  # 校验收敛后的最终逻辑实证
}

DOC_PARSE_CHANNELS: Dict[str, ChannelSpec] = {
    "filename": ChannelSpec("filename", last_value),  # 制度文件名
    "raw_bytes": ChannelSpec("raw_bytes", last_value),  # 制度文件字节快照
    "doc_tier": ChannelSpec("doc_tier", last_value),  # 制度层级（TIER1_POLICY/TIER2_INTERPRETATION）
    "raw_clauses": ChannelSpec("raw_clauses", last_value),  # 初筛切分后的制度条款列表
    "doc_plan": ChannelSpec("doc_plan", last_value),  # AI 制度主领域规划结果
    "batch_results": ChannelSpec("batch_results", merge_dict, allow_multi=True),  # 并发批次条款提纯结果
    "final_clauses": ChannelSpec("final_clauses", last_value),  # 领域收敛后的最终条款列表
}

SYNTHESIS_CHANNELS: Dict[str, ChannelSpec] = {
    "mode": ChannelSpec("mode", last_value),  # 资产合成模式（full/fast/incremental）
    "clauses_loaded": ChannelSpec("clauses_loaded", last_value),  # 已加载条款数量
    "aligned_domains": ChannelSpec("aligned_domains", last_value),  # 按业务域对齐后的领域与草稿映射
    "refined_domains": ChannelSpec("refined_domains", merge_dict, allow_multi=True),  # 并发领域 AI 口径提纯产物
    "summary": ChannelSpec("summary", last_value),  # 合成发布后的汇总快照
}

BATCH_INGEST_CHANNELS: Dict[str, ChannelSpec] = {
    "job_id": ChannelSpec("job_id", last_value),  # 批量摄入任务 ID
    "execution_mode": ChannelSpec("execution_mode", last_value),  # 批量执行模式
    "scan_summary": ChannelSpec("scan_summary", last_value),  # 静态扫描分层结果摘要
    "phase_stats": ChannelSpec("phase_stats", merge_dict, allow_multi=True),  # 各波次处理与成本统计累加表
    "pause_reason": ChannelSpec("pause_reason", last_value),  # 预算阈值挂起原因
    "synthesis_summary": ChannelSpec("synthesis_summary", last_value),  # 批量入库后自动资产合成摘要
}


def build_code_parse_context(
    filename: str,
    raw_text: str,
    *,
    execution_mode: str = "fast",
    upstream_context: Optional[list] = None,
    llm: Any = None,
    bus: Optional[Bus] = None,
    concurrency: int = 4,
    cancellation: Any = None,
) -> RunContext:
    """构造内层单脚本解析的纯内存 `RunContext`（零 SQLAlchemy Session 依赖）。"""
    return create_memory_context(
        "dc_code_parse",
        initial_node="created",
        shared={
            "filename": filename,
            "raw_text": raw_text,
            "execution_mode": execution_mode,
            "upstream_context": list(upstream_context or []),
        },
        channels=CODE_PARSE_CHANNELS,
        bus=bus if bus is not None else default_bus(),
        llm=llm,
        concurrency=concurrency,
        cancellation=cancellation,
        budget_guard=None,
        reducer=_apply,
    )


def build_doc_parse_context(
    filename: str,
    raw_bytes: bytes,
    *,
    llm: Any = None,
    bus: Optional[Bus] = None,
    concurrency: int = 4,
    cancellation: Any = None,
) -> RunContext:
    """构造内层单制度解析的纯内存 `RunContext`（零 SQLAlchemy Session 依赖）。"""
    return create_memory_context(
        "dc_doc_parse",
        initial_node="created",
        shared={
            "filename": filename,
            "raw_bytes": raw_bytes,
        },
        channels=DOC_PARSE_CHANNELS,
        bus=bus if bus is not None else default_bus(),
        llm=llm,
        concurrency=concurrency,
        cancellation=cancellation,
        budget_guard=None,
        reducer=_apply,
    )


def build_synthesis_context(
    session: Session,
    run: RunModel,
    *,
    settings: Any = None,
    llm: Any = None,
    bus: Optional[Bus] = None,
    concurrency: int = 4,
    cancellation: Any = None,
    shared: Optional[Dict[str, Any]] = None,
    projector: Optional[Callable[[Session, RunModel], RunState]] = None,
) -> RunContext:
    """构造外层资产合成 `RunContext`（挂载 `SYNTHESIS_CHANNELS` 与双门禁 `Bus`）。"""
    active_projector = projector or project
    state = active_projector(session, run)
    return RunContext(
        session=session,
        run=run,
        settings=settings,
        state=state,
        shared=dict(shared or {}),
        channels=dict(SYNTHESIS_CHANNELS),
        bus=bus if bus is not None else default_bus(),
        llm=llm,
        concurrency=max(1, int(concurrency or 1)),
        current_node=state.current_node or "created",
        projector=active_projector,
        reducer=_apply,
        cancellation=cancellation,
        budget_guard=None,
    )


def build_batch_ingest_context(
    session: Session,
    run: RunModel,
    *,
    settings: Any = None,
    llm: Any = None,
    bus: Optional[Bus] = None,
    concurrency: int = 4,
    cancellation: Any = None,
    shared: Optional[Dict[str, Any]] = None,
    projector: Optional[Callable[[Session, RunModel], RunState]] = None,
) -> RunContext:
    """构造外层批量摄入 `RunContext`（挂载 `BATCH_INGEST_CHANNELS` 与事件折叠器）。"""
    active_projector = projector or project
    state = active_projector(session, run)
    return RunContext(
        session=session,
        run=run,
        settings=settings,
        state=state,
        shared=dict(shared or {}),
        channels=dict(BATCH_INGEST_CHANNELS),
        bus=bus if bus is not None else default_bus(),
        llm=llm,
        concurrency=max(1, int(concurrency or 1)),
        current_node=state.current_node or "created",
        projector=active_projector,
        reducer=_apply,
        cancellation=cancellation,
        budget_guard=None,
    )

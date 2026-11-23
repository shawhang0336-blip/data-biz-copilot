"""统一 ORM 表模型定义（基础设施层 Layer 0，11 张核心表）。

分为三组：
1. 内核运行时账本与系统设置：`RunModel` / `EventModel` / `SystemSettingModel`；
2. 双源接入与底层隐身实证库：`SourceMaterialModel` / `RegulatoryClauseModel` / `LogicEvidenceModel`；
3. 纯业务资产层与 Copilot 会话：`BusinessCapabilityModel` / `BusinessRuleAssetModel` /
   `BusinessCaliberAssetModel` / `AssetRevisionModel` / `CopilotSessionModel` / `CopilotMessageModel`。
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db import Base

_LONG_TEXT = Text().with_variant(LONGTEXT(), "mysql")


# =============================================================================
# 1. 内核运行时账本与系统配置
# =============================================================================


class RunModel(Base):
    """Run 身份表，状态完全由 EventModel 事件账本折叠决定。"""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    spec_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class EventModel(Base):
    __tablename__ = "events"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(64))
    node: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[Dict[str, Any]] = mapped_column(JSON)
    prev_hash: Mapped[str] = mapped_column(String(64))
    event_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SystemSettingModel(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(96), primary_key=True)
    value_json: Mapped[Dict[str, Any]] = mapped_column(JSON)
    secret_reference: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# =============================================================================
# 2. 双源接入与底层隐身实证库（代码线索严格封存，不直接暴露给业务问答）
# =============================================================================


class SourceMaterialModel(Base):
    __tablename__ = "source_materials"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    # REGULATORY_DOC（监管/业务制度） | CODE_LOGIC（系统计算逻辑源）
    material_kind: Mapped[str] = mapped_column(String(32), index=True)
    file_type: Mapped[str] = mapped_column(String(16))
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    blob_uri: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), index=True, default="INGESTED")
    summary: Mapped[str] = mapped_column(Text, default="")
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RegulatoryClauseModel(Base):
    __tablename__ = "regulatory_clauses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    material_id: Mapped[str] = mapped_column(ForeignKey("source_materials.id"), index=True)
    chapter: Mapped[str] = mapped_column(String(128), default="")
    business_domain: Mapped[str] = mapped_column(String(128), index=True)
    clause_title: Mapped[str] = mapped_column(String(255))
    # CAPABILITY | INCLUSION | EXCLUSION | BRANCH | CALIBER | ROLE_GUIDE
    clause_kind: Mapped[str] = mapped_column(String(32), index=True)
    business_requirement: Mapped[str] = mapped_column(_LONG_TEXT)
    audience: Mapped[str] = mapped_column(String(128), default="业务 / 监管报送")
    metrics_mentioned_json: Mapped[List[str]] = mapped_column(JSON, default=list)
    source_locator_json: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class LogicEvidenceModel(Base):
    """从 SQL / 存储过程提取的底层实证事实。

    注意：`internal_locator_json` 中保存物理对象与行号供内部审计对齐，
    绝不向业务资产展示层或 Copilot 问答输出透出任何表名、字段名或 SQL 语句。
    """

    __tablename__ = "logic_evidences"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    material_id: Mapped[str] = mapped_column(ForeignKey("source_materials.id"), index=True)
    logic_fingerprint: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    business_domain_hint: Mapped[str] = mapped_column(String(128), index=True)
    target_concept_hint: Mapped[str] = mapped_column(String(255), index=True)
    source_summary: Mapped[str] = mapped_column(_LONG_TEXT, default="")
    inclusion_predicates_json: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, default=list)
    exclusion_predicates_json: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, default=list)
    branch_cases_json: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, default=list)
    calculation_kind: Mapped[str] = mapped_column(String(32), default="DIRECT")
    calculation_summary: Mapped[str] = mapped_column(_LONG_TEXT, default="")
    dedup_grain_hint: Mapped[str] = mapped_column(Text, default="")
    internal_locator_json: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# =============================================================================
# 3. 纯业务资产层（代码 × 监管材料融合产出）与 Copilot 对话账本
# =============================================================================


class BusinessCapabilityModel(Base):
    """1. 业务版图卡：回答“我们的系统逻辑已经包含了哪些业务”。"""

    __tablename__ = "business_capabilities"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    code: Mapped[str] = mapped_column(String(96), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    description: Mapped[str] = mapped_column(_LONG_TEXT, default="")
    scope_summary: Mapped[str] = mapped_column(_LONG_TEXT, default="")
    audience: Mapped[str] = mapped_column(String(128), default="业务 / 监管报送")
    # COVERED（系统逻辑已涵盖） | PARTIAL（部分覆盖/待完善） | UNREALIZED（仅制度提及尚未纳入逻辑）
    coverage_status: Mapped[str] = mapped_column(String(24), index=True, default="COVERED")
    rule_count: Mapped[int] = mapped_column(Integer, default=0)
    caliber_count: Mapped[int] = mapped_column(Integer, default=0)
    confirmed_count: Mapped[int] = mapped_column(Integer, default=0)
    evidence_ids_json: Mapped[List[str]] = mapped_column(JSON, default=list)
    clause_ids_json: Mapped[List[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(24), index=True, default="DRAFT")
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    confirmed_by: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BusinessRuleAssetModel(Base):
    """2. 业务处理规则卡：回答纳入范围、剔除情形、分支处理与去重规则。"""

    __tablename__ = "business_rule_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    capability_id: Mapped[str] = mapped_column(ForeignKey("business_capabilities.id"), index=True)
    code: Mapped[str] = mapped_column(String(96), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    sub_domain: Mapped[str] = mapped_column(String(128), index=True, default="")
    subject_theme: Mapped[str] = mapped_column(String(64), index=True, default="")
    # INCLUSION | EXCLUSION | BRANCH | DEDUP
    rule_category: Mapped[str] = mapped_column(String(24), index=True)
    business_condition: Mapped[str] = mapped_column(_LONG_TEXT, default="")
    business_action: Mapped[str] = mapped_column(_LONG_TEXT, default="")
    business_rationale: Mapped[str] = mapped_column(_LONG_TEXT, default="")
    # VERIFIED | CONSISTENT | DIVERGENT | UNREALIZED
    reconcile_status: Mapped[str] = mapped_column(String(24), index=True, default="VERIFIED")
    evidence_ids_json: Mapped[List[str]] = mapped_column(JSON, default=list)
    clause_ids_json: Mapped[List[str]] = mapped_column(JSON, default=list)
    audience: Mapped[str] = mapped_column(String(128), default="业务 / 监管报送")
    status: Mapped[str] = mapped_column(String(24), index=True, default="DRAFT")
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    confirmed_by: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BusinessCaliberAssetModel(Base):
    """3. 业务指标口径卡 & 4. 制度 vs 逻辑落地对账台账。"""

    __tablename__ = "business_caliber_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    capability_id: Mapped[str] = mapped_column(ForeignKey("business_capabilities.id"), index=True)
    code: Mapped[str] = mapped_column(String(96), unique=True, index=True)
    metric_name: Mapped[str] = mapped_column(String(255), index=True)
    sub_domain: Mapped[str] = mapped_column(String(128), index=True, default="")
    subject_theme: Mapped[str] = mapped_column(String(64), index=True, default="")
    business_definition: Mapped[str] = mapped_column(_LONG_TEXT, default="")
    formula_summary: Mapped[str] = mapped_column(_LONG_TEXT, default="")
    inclusion_scope: Mapped[str] = mapped_column(_LONG_TEXT, default="")
    exclusion_notes: Mapped[str] = mapped_column(_LONG_TEXT, default="")
    distinction_notes: Mapped[str] = mapped_column(_LONG_TEXT, default="")
    triggers_json: Mapped[List[str]] = mapped_column(JSON, default=list)
    # VERIFIED | CONSISTENT | DIVERGENT | UNREALIZED
    reconcile_status: Mapped[str] = mapped_column(String(24), index=True, default="VERIFIED")
    conflict_notes: Mapped[str] = mapped_column(_LONG_TEXT, default="")
    evidence_ids_json: Mapped[List[str]] = mapped_column(JSON, default=list)
    clause_ids_json: Mapped[List[str]] = mapped_column(JSON, default=list)
    audience: Mapped[str] = mapped_column(String(128), default="业务 / 监管报送 / 合规")
    status: Mapped[str] = mapped_column(String(24), index=True, default="DRAFT")
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    confirmed_by: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AssetRevisionModel(Base):
    __tablename__ = "asset_revisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    asset_kind: Mapped[str] = mapped_column(String(24), index=True)
    asset_id: Mapped[str] = mapped_column(String(36), index=True)
    revision_kind: Mapped[str] = mapped_column(String(32))
    old_value_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    new_value_json: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    editor: Mapped[str] = mapped_column(String(80))
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CopilotSessionModel(Base):
    __tablename__ = "copilot_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), default="新业务咨询")
    role_lens: Mapped[str] = mapped_column(String(64), default="业务")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CopilotMessageModel(Base):
    __tablename__ = "copilot_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("copilot_sessions.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(_LONG_TEXT)
    disclosures_json: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, default=list)
    cited_assets_json: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, default=list)
    tool_trace_json: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, default=list)
    verification_json: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

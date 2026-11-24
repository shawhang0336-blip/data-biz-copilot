"""dc_agent 策略层统一导出（Layer 2）：Bus 双门禁与预算构造器 + Run 状态折叠器（零全局副作用）。"""

from .dc_bus import (
    build_run_bus,
    build_script_child_bus,
    de_jargon_adjudicator,
    default_bus,
    detect_technical_jargon,
    evidence_backing_adjudicator,
    sanitize_business_text,
)
from .dc_plan import fold, project, project_many

__all__ = [
    "build_run_bus",
    "build_script_child_bus",
    "de_jargon_adjudicator",
    "default_bus",
    "detect_technical_jargon",
    "evidence_backing_adjudicator",
    "fold",
    "project",
    "project_many",
    "sanitize_business_text",
]

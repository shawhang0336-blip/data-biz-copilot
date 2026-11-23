"""跨层共享值对象与确定性 JSON 序列化契约（基础设施层 Layer 0）。"""

import json
from typing import Any, Dict, List, Literal

from pydantic import BaseModel, Field


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ExtractedClause(BaseModel):
    chapter: str = ""
    business_domain: str
    clause_title: str
    clause_kind: Literal["CAPABILITY", "INCLUSION", "EXCLUSION", "BRANCH", "CALIBER", "ROLE_GUIDE"]
    business_requirement: str
    audience: str = "业务 / 监管报送"
    metrics_mentioned: List[str] = Field(default_factory=list)
    source_locator: Dict[str, Any] = Field(default_factory=dict)


class ExtractedLogicEvidence(BaseModel):
    logic_fingerprint: str
    business_domain_hint: str
    target_concept_hint: str
    source_summary: str = ""
    inclusion_predicates: List[Dict[str, Any]] = Field(default_factory=list)
    exclusion_predicates: List[Dict[str, Any]] = Field(default_factory=list)
    branch_cases: List[Dict[str, Any]] = Field(default_factory=list)
    calculation_kind: Literal["DIRECT", "CONDITIONAL", "AGGREGATE", "CONFLICT"] = "DIRECT"
    calculation_summary: str = ""
    dedup_grain_hint: str = ""
    internal_locator: Dict[str, Any] = Field(default_factory=dict)

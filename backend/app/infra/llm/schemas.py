"""受控大模型工具契约（基础设施层 Layer 0）。"""

from typing import Any, Dict

TOOL_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "synthesize_business_capability": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "description": {"type": "string"},
            "scope_summary": {"type": "string"},
            "audience": {"type": "string"},
        },
        "required": ["name", "description", "scope_summary"],
    },
    "synthesize_business_rule": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "rule_category": {"type": "string", "enum": ["INCLUSION", "EXCLUSION", "BRANCH", "DEDUP"]},
            "business_condition": {"type": "string"},
            "business_action": {"type": "string"},
            "business_rationale": {"type": "string"},
            "evidence_ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["title", "rule_category", "business_condition", "business_action", "business_rationale"],
    },
    "synthesize_business_caliber": {
        "type": "object",
        "properties": {
            "metric_name": {"type": "string"},
            "business_definition": {"type": "string"},
            "formula_summary": {"type": "string"},
            "inclusion_scope": {"type": "string"},
            "exclusion_notes": {"type": "string"},
            "distinction_notes": {"type": "string"},
            "evidence_ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["metric_name", "business_definition", "formula_summary"],
    },
    "compose_business_answer": {
        "type": "object",
        "properties": {
            "answer": {"type": "string"},
            "cited_asset_codes": {"type": "array", "items": {"type": "string"}},
            "follow_up_questions": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["answer"],
    },
    "translate_logic_evidence": {
        "type": "object",
        "properties": {
            "business_domain_hint": {"type": "string"},
            "target_concept_hint": {"type": "string"},
            "source_summary": {"type": "string"},
            "inclusions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "rule_code": {"type": "string"},
                        "title": {"type": "string"},
                        "condition": {"type": "string"},
                        "rationale": {"type": "string"},
                    },
                    "required": ["rule_code", "title", "condition", "rationale"],
                },
            },
            "exclusions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "rule_code": {"type": "string"},
                        "title": {"type": "string"},
                        "condition": {"type": "string"},
                        "rationale": {"type": "string"},
                    },
                    "required": ["rule_code", "title", "condition", "rationale"],
                },
            },
            "branches": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "condition": {"type": "string"},
                        "action": {"type": "string"},
                    },
                    "required": ["title", "condition", "action"],
                },
            },
            "calculation_summary": {"type": "string"},
            "dedup_grain_hint": {"type": "string"},
        },
        "required": ["business_domain_hint", "target_concept_hint", "source_summary", "calculation_summary"],
    },
}


def tool_prompt_preamble(tool_name: str) -> str:
    return (
        f"你只能使用纯中文业务语言调用 `{tool_name}` 返回结构化参数。"
        "严禁在任何输出文本中出现 SQL 关键字、物理表名、字段英文名、脚本文件名、存储过程等技术词汇。"
    )

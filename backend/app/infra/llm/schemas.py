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
    "extract_regulatory_clauses": {
        "type": "object",
        "properties": {
            "clauses": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "section_index": {"type": "integer"},
                        "keep": {"type": "boolean"},
                        "business_domain": {"type": "string"},
                        "clause_title": {"type": "string"},
                        "clause_kind": {
                            "type": "string",
                            "enum": ["CAPABILITY", "INCLUSION", "EXCLUSION", "BRANCH", "CALIBER", "ROLE_GUIDE"],
                        },
                        "business_requirement": {"type": "string"},
                        "audience": {"type": "string"},
                        "metrics_mentioned": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": [
                        "section_index",
                        "keep",
                        "business_domain",
                        "clause_title",
                        "clause_kind",
                        "business_requirement",
                    ],
                },
            }
        },
        "required": ["clauses"],
    },
    "synthesize_domain_assets": {
        "type": "object",
        "properties": {
            "description": {"type": "string"},
            "scope_summary": {"type": "string"},
            "rules": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string"},
                        "title": {"type": "string"},
                        "rule_category": {"type": "string", "enum": ["INCLUSION", "EXCLUSION", "BRANCH", "DEDUP"]},
                        "sub_domain": {"type": "string"},
                        "subject_theme": {"type": "string"},
                        "business_condition": {"type": "string"},
                        "business_action": {"type": "string"},
                        "business_rationale": {"type": "string"},
                        "evidence_ids": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": [
                        "code",
                        "title",
                        "rule_category",
                        "business_condition",
                        "business_action",
                        "business_rationale",
                    ],
                },
            },
            "calibers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string"},
                        "metric_name": {"type": "string"},
                        "sub_domain": {"type": "string"},
                        "subject_theme": {"type": "string"},
                        "business_definition": {"type": "string"},
                        "formula_summary": {"type": "string"},
                        "distinction_notes": {"type": "string"},
                        "conflict_notes": {"type": "string"},
                        "evidence_ids": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["code", "metric_name", "business_definition", "formula_summary"],
                },
            },
        },
        "required": ["description", "scope_summary"],
    },
    "plan_copilot_tools": {
        "type": "object",
        "properties": {
            "tool_calls": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "tool": {
                            "type": "string",
                            "enum": [
                                "list_covered_businesses",
                                "resolve_business_concept",
                                "get_business_scope_and_rules",
                                "get_business_caliber_detail",
                                "check_regulatory_coverage_gap",
                                "find_rules_by_business_role",
                            ],
                        },
                        "args": {"type": "object"},
                    },
                    "required": ["tool", "args"],
                },
            },
            "reasoning_summary": {"type": "string"},
        },
        "required": ["tool_calls"],
    },
    "plan_document_structure": {
        "type": "object",
        "properties": {
            "document_kind": {
                "type": "string",
                "enum": ["REGULATORY_SPEC", "CALIBER_GUIDE", "GLOSSARY", "MACRO_POLICY", "MIXED"],
            },
            "document_summary": {"type": "string"},
            "chunking_strategy": {
                "type": "string",
                "enum": ["HEADING_SECTIONS", "ARTICLE_CLAUSES", "PARAGRAPH_WINDOWS"],
            },
            "canonical_domains": {"type": "array", "items": {"type": "string"}},
            "section_assignments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "section_index": {"type": "integer"},
                        "role": {
                            "type": "string",
                            "enum": ["RULE_CLAUSE", "CALIBER_DEF", "CAPABILITY_SCOPE", "IGNORE"],
                        },
                        "business_domain": {"type": "string"},
                        "chunk_group": {"type": "integer"},
                        "reason": {"type": "string"},
                    },
                    "required": ["section_index", "role", "business_domain", "chunk_group"],
                },
            },
        },
        "required": [
            "document_kind",
            "document_summary",
            "chunking_strategy",
            "canonical_domains",
            "section_assignments",
        ],
    },
    "plan_code_structure": {
        "type": "object",
        "properties": {
            "business_overview": {"type": "string"},
            "canonical_domain": {"type": "string"},
            "chunk_groups": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "group_id": {"type": "integer"},
                        "stage_title": {"type": "string"},
                        "business_domain": {"type": "string"},
                        "stage_role": {
                            "type": "string",
                            "enum": [
                                "SCOPE_FILTER",
                                "BRANCH_LOGIC",
                                "CALCULATION",
                                "DEDUP_MERGE",
                                "OUTPUT_REPORT",
                                "IGNORE",
                            ],
                        },
                        "step_indices": {"type": "array", "items": {"type": "integer"}},
                        "stage_purpose": {"type": "string"},
                    },
                    "required": [
                        "group_id",
                        "stage_title",
                        "business_domain",
                        "stage_role",
                        "step_indices",
                        "stage_purpose",
                    ],
                },
            },
        },
        "required": ["business_overview", "canonical_domain", "chunk_groups"],
    },
}


def tool_prompt_preamble(tool_name: str) -> str:
    return (
        f"你只能使用纯中文业务语言调用 `{tool_name}` 返回结构化参数。"
        "严禁在任何输出文本中出现 SQL 关键字、物理表名、字段英文名、脚本文件名、存储过程等技术词汇。"
    )

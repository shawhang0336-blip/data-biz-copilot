"""业务资产层原子 CAS 乐观锁修改与人工确认服务（资产层 Layer 4）。"""

from typing import Any, Dict, Type
import uuid

from sqlalchemy import update
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from app.infra.db_tables import (
    AssetRevisionModel,
    BusinessCaliberAssetModel,
    BusinessCapabilityModel,
    BusinessRuleAssetModel,
)
from app.policy.dc_bus import detect_technical_jargon


def _claim_asset_version_bump(
    session: Session,
    model_cls: Type[Any],
    asset_row: Any,
    expected_version: int,
    *,
    values: Dict[str, Any],
) -> None:
    """在修改任何字段前先做原子 SQL CAS 抢占，杜绝并发覆盖丢更新。"""
    new_version = expected_version + 1
    res = session.execute(
        update(model_cls)
        .where(
            model_cls.id == asset_row.id,
            model_cls.current_version == expected_version,
        )
        .values(current_version=new_version, **values)
    )
    if (res.rowcount or 0) != 1:
        session.rollback()
        raise ValueError("VERSION_CONFLICT")
    set_committed_value(asset_row, "current_version", new_version)
    for k, v in values.items():
        set_committed_value(asset_row, k, v)


def _assert_pure_business_changes(changes: Dict[str, Any]) -> None:
    for field_name, val in changes.items():
        if isinstance(val, str):
            hits = detect_technical_jargon(val)
            if hits:
                raise ValueError(
                    f"BUSINESS_LANGUAGE_ONLY: 业务资产字段 `{field_name}` 禁止包含代码或技术词 {hits}"
                )


def _sync_capability_confirmed_count(session: Session, capability_id: str) -> None:
    cap = session.get(BusinessCapabilityModel, capability_id)
    if cap is None:
        return
    r_cnt = (
        session.query(BusinessRuleAssetModel)
        .filter(BusinessRuleAssetModel.capability_id == capability_id, BusinessRuleAssetModel.status == "CONFIRMED")
        .count()
    )
    c_cnt = (
        session.query(BusinessCaliberAssetModel)
        .filter(BusinessCaliberAssetModel.capability_id == capability_id, BusinessCaliberAssetModel.status == "CONFIRMED")
        .count()
    )
    cap.confirmed_count = r_cnt + c_cnt


def update_business_rule(
    session: Session,
    rule_id: str,
    *,
    expected_version: int,
    changes: Dict[str, Any],
    editor: str,
    reason: str = "",
) -> BusinessRuleAssetModel:
    row = session.get(BusinessRuleAssetModel, rule_id)
    if row is None:
        raise KeyError("Business rule not found")
    allowed_fields = {"title", "business_condition", "business_action", "business_rationale", "audience"}
    filtered = {k: str(v).strip() for k, v in changes.items() if k in allowed_fields and v is not None}
    if not filtered:
        raise ValueError("No valid editable rule fields provided")
    _assert_pure_business_changes(filtered)
    old_snap = {k: getattr(row, k) for k in filtered}
    _claim_asset_version_bump(
        session,
        BusinessRuleAssetModel,
        row,
        expected_version,
        values={**filtered, "status": "DRAFT"},
    )
    session.add(
        AssetRevisionModel(
            id=str(uuid.uuid4()),
            asset_kind="RULE",
            asset_id=row.id,
            revision_kind="WORDING_EDIT",
            old_value_json=old_snap,
            new_value_json=filtered,
            editor=editor,
            reason=reason,
        )
    )
    _sync_capability_confirmed_count(session, row.capability_id)
    session.commit()
    return row


def confirm_business_rule(
    session: Session,
    rule_id: str,
    *,
    expected_version: int,
    confirmer: str,
    comment: str = "",
) -> BusinessRuleAssetModel:
    row = session.get(BusinessRuleAssetModel, rule_id)
    if row is None:
        raise KeyError("Business rule not found")
    _claim_asset_version_bump(
        session,
        BusinessRuleAssetModel,
        row,
        expected_version,
        values={"status": "CONFIRMED", "confirmed_by": confirmer},
    )
    session.add(
        AssetRevisionModel(
            id=str(uuid.uuid4()),
            asset_kind="RULE",
            asset_id=row.id,
            revision_kind="CONFIRM",
            old_value_json={"status": row.status},
            new_value_json={"status": "CONFIRMED", "confirmed_by": confirmer},
            editor=confirmer,
            reason=comment,
        )
    )
    _sync_capability_confirmed_count(session, row.capability_id)
    session.commit()
    return row


def update_business_caliber(
    session: Session,
    caliber_id: str,
    *,
    expected_version: int,
    changes: Dict[str, Any],
    editor: str,
    reason: str = "",
) -> BusinessCaliberAssetModel:
    row = session.get(BusinessCaliberAssetModel, caliber_id)
    if row is None:
        raise KeyError("Business caliber not found")
    allowed_fields = {
        "metric_name",
        "business_definition",
        "formula_summary",
        "inclusion_scope",
        "exclusion_notes",
        "distinction_notes",
        "audience",
    }
    filtered = {k: str(v).strip() for k, v in changes.items() if k in allowed_fields and v is not None}
    if not filtered:
        raise ValueError("No valid editable caliber fields provided")
    _assert_pure_business_changes(filtered)
    old_snap = {k: getattr(row, k) for k in filtered}
    _claim_asset_version_bump(
        session,
        BusinessCaliberAssetModel,
        row,
        expected_version,
        values={**filtered, "status": "DRAFT"},
    )
    session.add(
        AssetRevisionModel(
            id=str(uuid.uuid4()),
            asset_kind="CALIBER",
            asset_id=row.id,
            revision_kind="WORDING_EDIT",
            old_value_json=old_snap,
            new_value_json=filtered,
            editor=editor,
            reason=reason,
        )
    )
    _sync_capability_confirmed_count(session, row.capability_id)
    session.commit()
    return row


def confirm_business_caliber(
    session: Session,
    caliber_id: str,
    *,
    expected_version: int,
    confirmer: str,
    comment: str = "",
) -> BusinessCaliberAssetModel:
    row = session.get(BusinessCaliberAssetModel, caliber_id)
    if row is None:
        raise KeyError("Business caliber not found")
    _claim_asset_version_bump(
        session,
        BusinessCaliberAssetModel,
        row,
        expected_version,
        values={"status": "CONFIRMED", "confirmed_by": confirmer},
    )
    session.add(
        AssetRevisionModel(
            id=str(uuid.uuid4()),
            asset_kind="CALIBER",
            asset_id=row.id,
            revision_kind="CONFIRM",
            old_value_json={"status": row.status},
            new_value_json={"status": "CONFIRMED", "confirmed_by": confirmer},
            editor=confirmer,
            reason=comment,
        )
    )
    _sync_capability_confirmed_count(session, row.capability_id)
    session.commit()
    return row

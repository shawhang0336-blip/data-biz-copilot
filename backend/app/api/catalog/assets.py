"""纯业务资产层管理路由（`/api/assets/*`）。"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.common import db_session
from app.catalog.mutations import (
    confirm_business_caliber,
    confirm_business_rule,
    update_business_caliber,
    update_business_rule,
)
from app.catalog.views import (
    export_rule_studio_handoff,
    get_asset_backing_detail,
    get_asset_panorama,
    list_asset_revisions,
    list_business_calibers,
    list_business_rules,
    list_capabilities,
    list_reconcile_ledger,
)
from app.infra.config import OPERATOR_PLACEHOLDER

router = APIRouter(prefix="/assets", tags=["catalog"])


class AssetEditRequest(BaseModel):
    expected_version: int
    editor: str = Field(default=OPERATOR_PLACEHOLDER, max_length=80)
    reason: str = ""
    changes: Dict[str, Any]


class AssetConfirmRequest(BaseModel):
    expected_version: int
    confirmer: str = Field(default=OPERATOR_PLACEHOLDER, max_length=80)
    comment: str = ""


@router.get("/panorama")
def panorama(session: Session = Depends(db_session)) -> Dict[str, Any]:
    return get_asset_panorama(session)


@router.get("/capabilities")
def capabilities(
    agency: Optional[str] = None,
    regime: Optional[str] = None,
    session: Session = Depends(db_session),
) -> List[Dict[str, Any]]:
    return list_capabilities(session, agency=agency, regime=regime)


@router.get("/rules")
def rules(
    capability_id: Optional[str] = None,
    rule_category: Optional[str] = None,
    reconcile_status: Optional[str] = None,
    agency: Optional[str] = None,
    regime: Optional[str] = None,
    sub_domain: Optional[str] = None,
    subject_theme: Optional[str] = None,
    session: Session = Depends(db_session),
) -> List[Dict[str, Any]]:
    return list_business_rules(
        session,
        capability_id=capability_id,
        rule_category=rule_category,
        reconcile_status=reconcile_status,
        agency=agency,
        regime=regime,
        sub_domain=sub_domain,
        subject_theme=subject_theme,
    )


@router.patch("/rules/{rule_id}")
def edit_rule(rule_id: str, req: AssetEditRequest, session: Session = Depends(db_session)) -> Dict[str, Any]:
    try:
        row = update_business_rule(
            session,
            rule_id,
            expected_version=req.expected_version,
            changes=req.changes,
            editor=req.editor,
            reason=req.reason,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        code = 409 if "VERSION_CONFLICT" in str(exc) else 400
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    return {"id": row.id, "code": row.code, "current_version": row.current_version, "status": row.status}


@router.post("/rules/{rule_id}/confirm")
def confirm_rule(rule_id: str, req: AssetConfirmRequest, session: Session = Depends(db_session)) -> Dict[str, Any]:
    try:
        row = confirm_business_rule(
            session,
            rule_id,
            expected_version=req.expected_version,
            confirmer=req.confirmer,
            comment=req.comment,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": row.id, "code": row.code, "current_version": row.current_version, "status": row.status}


@router.get("/calibers")
def calibers(
    capability_id: Optional[str] = None,
    reconcile_status: Optional[str] = None,
    agency: Optional[str] = None,
    regime: Optional[str] = None,
    sub_domain: Optional[str] = None,
    subject_theme: Optional[str] = None,
    session: Session = Depends(db_session),
) -> List[Dict[str, Any]]:
    return list_business_calibers(
        session,
        capability_id=capability_id,
        reconcile_status=reconcile_status,
        agency=agency,
        regime=regime,
        sub_domain=sub_domain,
        subject_theme=subject_theme,
    )


@router.patch("/calibers/{caliber_id}")
def edit_caliber(caliber_id: str, req: AssetEditRequest, session: Session = Depends(db_session)) -> Dict[str, Any]:
    try:
        row = update_business_caliber(
            session,
            caliber_id,
            expected_version=req.expected_version,
            changes=req.changes,
            editor=req.editor,
            reason=req.reason,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        code = 409 if "VERSION_CONFLICT" in str(exc) else 400
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    return {"id": row.id, "code": row.code, "current_version": row.current_version, "status": row.status}


@router.post("/calibers/{caliber_id}/confirm")
def confirm_caliber(caliber_id: str, req: AssetConfirmRequest, session: Session = Depends(db_session)) -> Dict[str, Any]:
    try:
        row = confirm_business_caliber(
            session,
            caliber_id,
            expected_version=req.expected_version,
            confirmer=req.confirmer,
            comment=req.comment,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": row.id, "code": row.code, "current_version": row.current_version, "status": row.status}


@router.get("/reconcile")
def reconcile(
    agency: Optional[str] = None,
    regime: Optional[str] = None,
    session: Session = Depends(db_session),
) -> Dict[str, Any]:
    return list_reconcile_ledger(session, agency=agency, regime=regime)


@router.get("/revisions")
def revisions(
    asset_kind: Optional[str] = None,
    revision_kind: Optional[str] = None,
    agency: Optional[str] = None,
    regime: Optional[str] = None,
    human_only: bool = False,
    limit: int = 120,
    session: Session = Depends(db_session),
) -> Dict[str, Any]:
    return list_asset_revisions(
        session,
        asset_kind=asset_kind,
        revision_kind=revision_kind,
        agency=agency,
        regime=regime,
        human_only=human_only,
        limit=max(1, min(limit, 500)),
    )


@router.get("/rule-studio-export")
def rule_studio_export(
    agency: Optional[str] = None,
    regime: Optional[str] = None,
    status: Optional[str] = None,
    session: Session = Depends(db_session),
) -> Dict[str, Any]:
    statuses = tuple(s.strip().upper() for s in status.split(",") if s.strip()) if status else ("UNREALIZED", "DIVERGENT")
    return export_rule_studio_handoff(session, agency=agency, regime=regime, statuses=statuses)


@router.get("/backing/{asset_code}")
def backing_detail(asset_code: str, session: Session = Depends(db_session)) -> Dict[str, Any]:
    try:
        return get_asset_backing_detail(session, asset_code)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

"""统一资源接口：查询、导出、登记和动作按模块 key 分派到领域服务。"""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas import ActionPayload, ActionResult, EntryPayload, PageResult
from app.services import service_for
from app.services.workbench_boundary import workbench_boundary

router = APIRouter(prefix="/api", tags=["业务资源"])


def require_service(module: str):
    service = service_for(module)
    if service is None:
        raise HTTPException(status_code=404, detail=f"未知业务模块：{module}")
    return service


@router.get("/{module}", response_model=PageResult[dict])
def list_entries(
    module: str,
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> PageResult[dict]:
    service = require_service(module)
    filters = workbench_boundary.collect_filters(service, request.query_params)
    payload = workbench_boundary.query(
        service,
        filters=filters,
        keyword=keyword,
        status=status,
        page=page,
        size=size,
    )
    return PageResult(**payload)


@router.get("/receipts/{receipt_key}", response_model=ActionResult)
def get_action_receipt(receipt_key: str) -> ActionResult:
    receipt = workbench_boundary.get_receipt(receipt_key)
    if receipt is None:
        raise HTTPException(status_code=404, detail=f"动作回执 {receipt_key} 不存在或已过期")
    return ActionResult(
        ok=bool(receipt["ok"]),
        message=str(receipt["message"]),
        entry=receipt.get("entry"),
    )


@router.get("/{module}/export")
def export_entries(
    module: str,
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> StreamingResponse:
    service = require_service(module)
    filters = workbench_boundary.collect_filters(service, request.query_params)
    payload = workbench_boundary.query(
        service,
        filters=filters,
        keyword=keyword,
        status=status,
        page=1,
        size=1_000_000,
    )
    items = payload["items"]
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["id", *service.LIST_FIELDS, "status"])
    for item in items:
        values = [item.get("id"), *(item.get(field, "") for field in service.LIST_FIELDS),
                  item.get("status", "")]
        writer.writerow([
            "'" + str(value) if str(value or "").startswith(("=", "+", "-", "@")) else value
            for value in values
        ])
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{module}.csv"'},
    )


@router.get("/{module}/{entry_id}", response_model=dict)
def get_entry(module: str, entry_id: int) -> dict[str, Any]:
    service = require_service(module)
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"{service.ENTITY} {entry_id} 不存在或已归档")
    return entry


@router.post("/{module}", response_model=ActionResult)
def create_entry(module: str, payload: EntryPayload) -> ActionResult:
    service = require_service(module)
    receipt = workbench_boundary.register(service, payload.values)
    return ActionResult(
        ok=receipt["ok"], message=receipt["message"], entry=receipt.get("entry"),
    )


@router.post("/{module}/{entry_id}/actions", response_model=ActionResult)
def run_action(
    module: str,
    entry_id: int,
    payload: ActionPayload,
    request: Request,
) -> ActionResult:
    service = require_service(module)
    receipt_key = (
        request.headers.get("idempotency-key")
        or request.headers.get("x-request-id")
        or payload.idempotency_key
        or payload.request_id
    )
    receipt = workbench_boundary.act(
        service, entry_id, payload.action, receipt_key=receipt_key,
    )
    return ActionResult(
        ok=bool(receipt["ok"]),
        message=str(receipt["message"]),
        entry=receipt.get("entry"),
    )

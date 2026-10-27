from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from backend.api.deps import EngineDep
from backend.api.schemas import BulkResult, ImportReportOut, LeadDetail, LeadOut, LeadPatch, LeadsPage
from backend.core import exports, leads
from backend.core.models import EmailConfidence, LeadStatus, Mode

router = APIRouter(prefix="/leads", tags=["leads"])
MAX_IMPORT_BYTES = 5_000_000


def _filter(q: str, status: list[LeadStatus], mode: list[Mode], confidence: list[EmailConfidence],
            run_id: int | None) -> leads.LeadFilter:
    return leads.LeadFilter(q=q, status=status, mode=mode, confidence=confidence, run_id=run_id)


@router.get("", response_model=LeadsPage)
def list_leads(
    engine: EngineDep,
    q: str = "",
    status: Annotated[list[LeadStatus], Query()] = [],  # noqa: B006 - FastAPI reads the default as a spec
    mode: Annotated[list[Mode], Query()] = [],  # noqa: B006
    confidence: Annotated[list[EmailConfidence], Query()] = [],  # noqa: B006
    run_id: int | None = None,
    sort: str = "",
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=5000),
) -> LeadsPage:
    rows, total = leads.query_leads(_filter(q, status, mode, confidence, run_id), sort, offset, limit, engine)
    return LeadsPage(items=[LeadOut(**r) for r in rows], total=total)


@router.get("/export.csv")
def export_csv(
    engine: EngineDep,
    q: str = "",
    status: Annotated[list[LeadStatus], Query()] = [],  # noqa: B006
    mode: Annotated[list[Mode], Query()] = [],  # noqa: B006
    confidence: Annotated[list[EmailConfidence], Query()] = [],  # noqa: B006
    run_id: int | None = None,
    sort: str = "",
) -> Response:
    body = exports.export_csv(_filter(q, status, mode, confidence, run_id), sort, engine)
    return Response(body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="leads.csv"'})


@router.post("/import-csv", response_model=ImportReportOut)
async def import_csv(request: Request, engine: EngineDep, dry_run: bool = False) -> ImportReportOut:
    """Body is the raw CSV text. With dry_run=true nothing is saved and the report previews the result."""
    raw = await request.body()
    if len(raw) > MAX_IMPORT_BYTES:
        raise HTTPException(413, "The file is larger than 5 MB")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(422, "The file is not UTF-8 text")
    from fastapi.concurrency import run_in_threadpool  # MX lookups block: keep them off the event loop

    try:
        rep = await run_in_threadpool(exports.import_csv, text, dry_run=dry_run, engine=engine)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return ImportReportOut(
        total=rep.total, valid=rep.valid, inserted=rep.inserted, duplicates=rep.duplicates, suppressed=rep.suppressed,
        invalid=rep.invalid, no_mx=rep.no_mx, issues=[i.__dict__ for i in rep.issues],  # type: ignore[arg-type]
    )


@router.post("/bulk", response_model=BulkResult)
def bulk(body: leads.BulkRequest, engine: EngineDep) -> BulkResult:
    if body.ids is None and body.filter is None:
        raise HTTPException(422, "Provide ids or a filter")
    if body.action == "set_status" and body.status is None:
        raise HTTPException(422, "status is required for set_status")
    return BulkResult(**leads.bulk_apply(body, engine))


@router.get("/{lead_id}", response_model=LeadDetail)
def get_lead(lead_id: int, engine: EngineDep) -> dict:
    lead = leads.get_lead(lead_id, engine)
    if lead is None:
        raise HTTPException(404, "Lead not found")
    return lead


@router.patch("/{lead_id}", response_model=LeadDetail)
def patch_lead(lead_id: int, body: LeadPatch, engine: EngineDep) -> dict:
    fields = body.model_dump(exclude_unset=True)
    if "status" in fields and fields["status"] is not None:
        fields["status"] = fields["status"].value
    lead = leads.update_lead(lead_id, {k: v for k, v in fields.items() if v is not None}, engine)
    if lead is None:
        raise HTTPException(404, "Lead not found")
    return lead

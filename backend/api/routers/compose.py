"""Templates, profile fields and drafts (milestone 3). Owner: task B."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from backend.api.deps import EngineDep
from backend.api.schemas import JobOut
from backend.api.schemas_compose import (BulkApproveIn, BulkApproveOut, DraftDetail, DraftKind, DraftPatch, DraftsPage,
                                         DraftStatus, GenerateDraftsIn, ProfileOut, ProfilePut, TemplateIn,
                                         TemplateOut, TemplatePreview, TemplatePreviewIn)
from backend.core import campaigns, composer, drafts, jobs

router = APIRouter(tags=["compose"])


def _tpl(body: TemplateIn) -> dict:
    return body.model_dump(mode="json")


@router.get("/templates", response_model=list[TemplateOut])
def list_templates(engine: EngineDep) -> list[dict]:
    """Seeds the three starter templates on first call."""
    return composer.list_templates(engine)


@router.post("/templates", response_model=TemplateOut, status_code=201)
def create_template(body: TemplateIn, engine: EngineDep) -> dict:
    try:
        return composer.create_template(_tpl(body), engine)
    except composer.TemplateError as e:
        raise HTTPException(422, str(e)) from e


@router.put("/templates/{template_id}", response_model=TemplateOut)
def update_template(template_id: int, body: TemplateIn, engine: EngineDep) -> dict:
    try:
        out = composer.update_template(template_id, _tpl(body), engine)
    except composer.TemplateError as e:
        raise HTTPException(422, str(e)) from e
    if out is None:
        raise HTTPException(404, "Template not found")
    return out


@router.delete("/templates/{template_id}", status_code=204)
def delete_template(template_id: int, engine: EngineDep) -> None:
    try:
        if not composer.delete_template(template_id, engine):
            raise HTTPException(404, "Template not found")
    except composer.TemplateInUse as e:
        raise HTTPException(409, str(e)) from e


@router.post("/templates/preview", response_model=TemplatePreview)
def preview_template(body: TemplatePreviewIn, engine: EngineDep) -> dict:
    lead = None
    if body.lead_id is not None:
        with engine.connect() as c:
            row = c.execute(text("SELECT name, company, role, mode FROM leads WHERE id = :i"), {"i": body.lead_id}).first()
        if row is None:
            raise HTTPException(404, "Lead not found")
        lead = dict(row._mapping)
    try:
        composer.validate(_tpl(body.template))
        return composer.preview(_tpl(body.template), lead, engine)
    except composer.TemplateError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/profile", response_model=ProfileOut)
def get_profile(engine: EngineDep) -> dict:
    return composer.get_profile(engine)


@router.put("/profile", response_model=ProfileOut)
def put_profile(body: ProfilePut, engine: EngineDep) -> dict:
    try:
        return composer.put_profile([f.model_dump() for f in body.fields], engine)
    except composer.ProfileError as e:
        raise HTTPException(422, str(e)) from e


@router.post("/drafts/generate", response_model=JobOut, status_code=202)
def generate_drafts(body: GenerateDraftsIn, engine: EngineDep) -> dict:
    """Starts the `drafts_generate` job (progress over SSE, `draft` events as each draft lands)."""
    camp = campaigns.get(body.campaign_id, engine)
    if camp is None:
        raise HTTPException(404, "Campaign not found")
    if not camp.get("template_id") or composer.get_template(camp["template_id"], engine) is None:
        raise HTTPException(422, "Pick a template for this campaign before generating drafts.")
    for j in jobs.list_jobs(kind="drafts_generate", active_only=True, engine=engine):
        if j["payload"].get("campaign_id") == body.campaign_id:
            return j  # already running for this campaign: don't start a second writer
    return jobs.create_job("drafts_generate", body.model_dump(), engine)


@router.get("/drafts", response_model=DraftsPage)
def list_drafts(engine: EngineDep, campaign_id: int, status: Annotated[list[DraftStatus] | None, Query()] = None,
                flagged: bool | None = None, q: str = "", offset: int = 0, limit: int = 100,
                kind: DraftKind | None = None) -> dict:
    return drafts.list_drafts(engine, campaign_id, status, flagged, q, offset, limit, kind)


@router.post("/drafts/bulk-approve", response_model=BulkApproveOut)
def bulk_approve(body: BulkApproveIn, engine: EngineDep) -> dict:
    """Pending drafts only; `needs_review` drafts need an explicit single approve."""
    return drafts.bulk_approve(body.campaign_id, body.draft_ids, body.only_unflagged, engine, kinds=body.kinds)


@router.get("/drafts/{draft_id}", response_model=DraftDetail)
def get_draft(draft_id: int, engine: EngineDep) -> dict:
    d = drafts.get_draft(draft_id, engine)
    if d is None:
        raise HTTPException(404, "Draft not found")
    return d


@router.patch("/drafts/{draft_id}", response_model=DraftDetail)
def patch_draft(draft_id: int, body: DraftPatch, engine: EngineDep) -> dict:
    """Edit and/or approve/reject. Editing an approved draft withdraws the approval (DB trigger)."""
    try:
        return drafts.patch_draft(draft_id, body.model_dump(exclude_unset=True, exclude_none=True), engine)
    except drafts.DraftNotFound as e:
        raise HTTPException(404, "Draft not found") from e
    except drafts.DraftConflict as e:
        raise HTTPException(409, str(e)) from e


@router.post("/drafts/{draft_id}/regenerate-opener", response_model=DraftDetail)
def regenerate_opener(draft_id: int, engine: EngineDep) -> dict:
    try:
        return drafts.regenerate_opener(draft_id, engine)
    except drafts.DraftNotFound as e:
        raise HTTPException(404, "Draft not found") from e
    except drafts.DraftConflict as e:
        raise HTTPException(409, str(e)) from e

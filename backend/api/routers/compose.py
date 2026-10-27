"""Templates, profile fields and drafts (milestone 3). Owner: task B."""
from __future__ import annotations

from fastapi import APIRouter

from backend.api.deps import EngineDep
from backend.api.schemas import JobOut
from backend.api.schemas_compose import (BulkApproveIn, BulkApproveOut, DraftDetail, DraftPatch, DraftsPage,
                                         DraftStatus, GenerateDraftsIn, ProfileOut, ProfilePut, TemplateIn,
                                         TemplateOut, TemplatePreview, TemplatePreviewIn)
from backend.api.stubs import not_built

router = APIRouter(tags=["compose"])
OWNER = "milestone 3: compose"


@router.get("/templates", response_model=list[TemplateOut])
def list_templates(engine: EngineDep) -> list[dict]:
    """Seeds the three starter templates on first call."""
    raise not_built(OWNER)


@router.post("/templates", response_model=TemplateOut, status_code=201)
def create_template(body: TemplateIn, engine: EngineDep) -> dict:
    raise not_built(OWNER)


@router.put("/templates/{template_id}", response_model=TemplateOut)
def update_template(template_id: int, body: TemplateIn, engine: EngineDep) -> dict:
    raise not_built(OWNER)


@router.delete("/templates/{template_id}", status_code=204)
def delete_template(template_id: int, engine: EngineDep) -> None:
    raise not_built(OWNER)


@router.post("/templates/preview", response_model=TemplatePreview)
def preview_template(body: TemplatePreviewIn, engine: EngineDep) -> TemplatePreview:
    raise not_built(OWNER)


@router.get("/profile", response_model=ProfileOut)
def get_profile(engine: EngineDep) -> ProfileOut:
    raise not_built(OWNER)


@router.put("/profile", response_model=ProfileOut)
def put_profile(body: ProfilePut, engine: EngineDep) -> ProfileOut:
    raise not_built(OWNER)


@router.post("/drafts/generate", response_model=JobOut, status_code=202)
def generate_drafts(body: GenerateDraftsIn, engine: EngineDep) -> dict:
    """Starts the `drafts_generate` job (progress over SSE, `draft` events as each draft lands)."""
    raise not_built(OWNER)


@router.get("/drafts", response_model=DraftsPage)
def list_drafts(engine: EngineDep, campaign_id: int, status: list[DraftStatus] | None = None, flagged: bool | None = None,
                q: str = "", offset: int = 0, limit: int = 100) -> DraftsPage:
    raise not_built(OWNER)


@router.get("/drafts/{draft_id}", response_model=DraftDetail)
def get_draft(draft_id: int, engine: EngineDep) -> DraftDetail:
    raise not_built(OWNER)


@router.patch("/drafts/{draft_id}", response_model=DraftDetail)
def patch_draft(draft_id: int, body: DraftPatch, engine: EngineDep) -> DraftDetail:
    """Edit and/or approve/reject. Editing an approved draft withdraws the approval (DB trigger)."""
    raise not_built(OWNER)


@router.post("/drafts/bulk-approve", response_model=BulkApproveOut)
def bulk_approve(body: BulkApproveIn, engine: EngineDep) -> BulkApproveOut:
    raise not_built(OWNER)


@router.post("/drafts/{draft_id}/regenerate-opener", response_model=DraftDetail)
def regenerate_opener(draft_id: int, engine: EngineDep) -> DraftDetail:
    raise not_built(OWNER)

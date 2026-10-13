"""/api/projects/{id}/requirements and /api/requirements/{id} -- the
requirement pipeline. Viewers can read; editors can start, retry and delete.
Generated test cases are approved with the normal per-test-case review."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import pipeline
from ..auth.sessions import CurrentUser, require_project_role, role_meets
from ..crypto import decrypt_secret
from ..db import models as m
from ..db import records, requirements
from ..db.base import new_id
from ..db.session import Db
from ..errors import ApiError, parse_body
from ..integrations import azure_devops, defects
from ..ws import registry
from .common import json_body, no_content, project_role, require_role

router = APIRouter()


class CreateRequirementIn(BaseModel):
  title: Optional[str] = None
  text: Optional[str] = None
  azureDevOpsWorkItemId: Optional[int] = Field(default=None, gt=0)
  environmentId: Optional[str] = None


async def _requirement(db: Db, user: CurrentUser, requirement_id: str, min_role: str = "viewer") -> tuple[m.Requirement, str]:
  req = await db.get(m.Requirement, requirement_id)
  if req is None:
    raise ApiError(404, "Requirement not found")
  role = await require_project_role(db, user, req.project_id, min_role)
  return req, role


async def _respond(db: Db, req: m.Requirement) -> dict[str, Any]:
  await db.flush()
  record = await requirements.record(db, req)
  registry.broadcast(req.project_id, {"type": "requirement.updated", "requirement": await requirements.summary(db, req)})
  return record


@router.get("/api/projects/{project_id}/requirements")
async def list_requirements(project_id: str, user: CurrentUser, db: Db) -> list[dict[str, Any]]:
  await project_role(db, user, project_id)
  rows = list(
    (
      await db.execute(select(m.Requirement).where(m.Requirement.project_id == project_id).order_by(m.Requirement.created_at.desc()))
    ).scalars()
  )
  return await requirements.summaries(db, rows)


@router.post("/api/projects/{project_id}/requirements", status_code=201)
async def create_requirement(project_id: str, request: Request, user: CurrentUser, db: Db) -> dict[str, Any]:
  """From pasted text, or imported from an Azure DevOps work item. Starts the
  pipeline straight away."""
  require_role(await project_role(db, user, project_id), "editor")
  body = parse_body(CreateRequirementIn, await json_body(request))
  source_type, source_ref, source_url = "manual", None, None

  if body.azureDevOpsWorkItemId:
    integration = await defects.integration_for(db, project_id)
    if integration is None:
      raise ApiError(400, "Configure Azure DevOps on the Integrations page to import work items.")
    try:
      item = await azure_devops.get_work_item(
        integration.base_url, integration.project_key, decrypt_secret(integration.api_token_encrypted), body.azureDevOpsWorkItemId
      )
    except azure_devops.AzureDevOpsError as err:
      raise ApiError(400, str(err)) from err
    title = body.title or item.title
    parts = [item.description]
    if item.acceptance_criteria:
      parts.append("Acceptance criteria:\n" + item.acceptance_criteria)
    text = "\n\n".join(p for p in parts if p) or item.title
    source_type, source_ref, source_url = "azureDevOps", str(item.id), item.url
  else:
    if not body.text or not body.text.strip():
      raise ApiError(400, "Paste the requirement text, or give an Azure DevOps work item id.")
    text = body.text.strip()
    title = (body.title or "").strip() or text.splitlines()[0][:120]

  if body.environmentId:
    env = await db.get(m.Environment, body.environmentId)
    if env is None or env.project_id != project_id:
      raise ApiError(400, "Unknown environment for this project.")

  req = m.Requirement(
    id=new_id(),
    project_id=project_id,
    title=title,
    source_text=text,
    source_type=source_type,
    source_ref=source_ref,
    source_url=source_url,
    status=pipeline.ANALYZING,
    environment_id=body.environmentId,
    created_by_id=user.id,
  )
  db.add(req)
  await records.record_audit(
    db, user_id=user.id, project_id=project_id, action="requirement.created", target_type="Requirement", target_id=req.id, detail=title
  )
  record = await _respond(db, req)
  await db.commit()
  pipeline.start(req.id)
  return record


@router.get("/api/requirements/{requirement_id}")
async def get_requirement(requirement_id: str, user: CurrentUser, db: Db) -> dict[str, Any]:
  req, _ = await _requirement(db, user, requirement_id)
  return await requirements.record(db, req)


@router.post("/api/requirements/{requirement_id}/retry")
async def retry(requirement_id: str, user: CurrentUser, db: Db) -> dict[str, Any]:
  req, role = await _requirement(db, user, requirement_id)
  if not role_meets(role, "editor"):
    raise ApiError(403, 'This action requires at least "editor" access to this project.')
  if req.status != pipeline.FAILED or not req.failed_stage:
    raise ApiError(409, "Only a failed requirement can be retried.")
  stage = req.failed_stage
  req.status, req.failed_stage, req.error = pipeline.STATUS_FOR_STAGE[stage], None, None
  record = await _respond(db, req)
  await db.commit()
  pipeline.retry(req.id, stage)
  return record


@router.delete("/api/requirements/{requirement_id}")
async def delete_requirement(requirement_id: str, user: CurrentUser, db: Db) -> Response:
  """Removes the requirement and its traceability; the test cases stay."""
  req, role = await _requirement(db, user, requirement_id)
  if not role_meets(role, "editor"):
    raise ApiError(403, 'This action requires at least "editor" access to this project.')
  if req.status in pipeline.RUNNING_STATUSES:
    raise ApiError(409, "Wait for the current stage to finish before deleting.")
  await records.record_audit(
    db, user_id=user.id, project_id=req.project_id, action="requirement.deleted", target_type="Requirement", target_id=req.id, detail=req.title
  )
  await db.delete(req)
  return no_content()

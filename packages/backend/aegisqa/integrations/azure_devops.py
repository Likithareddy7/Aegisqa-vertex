"""Azure DevOps (Work Item Tracking REST API, api-version 7.1), authenticated
with a Personal Access Token (Basic auth, empty username).

- create_bug:     file a failed run as a Bug work item
- get_work_item:  import a user story as a requirement
- add_comment:    post the pipeline's results back on that story

`base_url` is the organization URL (https://dev.azure.com/yourorg) and
`project` the Azure DevOps project name, as configured on the Integrations page.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote

import httpx

INTEGRATION_TYPE = "azureDevOps"


@dataclass
class CreatedIssue:
  key: str
  url: str


@dataclass
class WorkItem:
  id: int
  title: str
  description: str
  acceptance_criteria: str
  url: str


class AzureDevOpsError(Exception):
  pass


class _Connection:
  def __init__(self, base_url: str, project: str, personal_access_token: str):
    self.organization_url = base_url.rstrip("/")
    self.project_path = quote(project, safe="")
    token = base64.b64encode(f":{personal_access_token}".encode()).decode()
    self.headers = {"Authorization": f"Basic {token}"}

  def url(self, path: str) -> str:
    return f"{self.organization_url}/{self.project_path}/{path}"

  def work_item_page(self, work_item_id: int) -> str:
    return self.url(f"_workitems/edit/{work_item_id}")

  async def request(self, method: str, path: str, **kwargs: Any) -> Any:
    headers = {**self.headers, **kwargs.pop("headers", {})}
    async with httpx.AsyncClient(timeout=30) as client:
      response = await client.request(method, self.url(path), headers=headers, **kwargs)
    if response.status_code >= 400:
      detail = response.text
      try:
        detail = response.json().get("message") or detail
      except ValueError:
        pass
      raise AzureDevOpsError(f"Azure DevOps API error ({response.status_code}): {detail}")
    return response.json() if response.content else None


def _html_from_text(text: str) -> str:
  # Work item rich-text fields render HTML; keep line breaks visible.
  escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
  return escaped.replace("\n", "<br>")


class _TextExtractor(HTMLParser):
  _BREAKS = {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "ul", "ol"}

  def __init__(self) -> None:
    super().__init__()
    self.parts: list[str] = []

  def handle_starttag(self, tag: str, attrs: Any) -> None:
    if tag in self._BREAKS:
      self.parts.append("\n")
    if tag == "li":
      self.parts.append("- ")

  def handle_data(self, data: str) -> None:
    self.parts.append(data)


def text_from_html(value: str) -> str:
  parser = _TextExtractor()
  parser.feed(value or "")
  text = "".join(parser.parts).replace("\xa0", " ")
  return re.sub(r"\n\s*\n+", "\n\n", text).strip()


async def create_bug(base_url: str, project: str, personal_access_token: str, title: str, description: str) -> CreatedIssue:
  conn = _Connection(base_url, project, personal_access_token)
  body = await conn.request(
    "POST",
    "_apis/wit/workitems/$Bug?api-version=7.1",
    headers={"Content-Type": "application/json-patch+json"},
    json=[
      {"op": "add", "path": "/fields/System.Title", "value": title},
      {"op": "add", "path": "/fields/System.Description", "value": _html_from_text(description)},
    ],
  )
  # The response's own `url` is the REST resource; link to the browsable page.
  return CreatedIssue(key=f"#{body['id']}", url=conn.work_item_page(body["id"]))


async def get_work_item(base_url: str, project: str, personal_access_token: str, work_item_id: int) -> WorkItem:
  conn = _Connection(base_url, project, personal_access_token)
  body = await conn.request("GET", f"_apis/wit/workitems/{work_item_id}?api-version=7.1")
  fields = body.get("fields", {})
  return WorkItem(
    id=int(body["id"]),
    title=fields.get("System.Title", f"Work item {work_item_id}"),
    description=text_from_html(fields.get("System.Description", "")),
    acceptance_criteria=text_from_html(fields.get("Microsoft.VSTS.Common.AcceptanceCriteria", "")),
    url=conn.work_item_page(int(body["id"])),
  )


async def add_comment(base_url: str, project: str, personal_access_token: str, work_item_id: int, text: str) -> None:
  conn = _Connection(base_url, project, personal_access_token)
  await conn.request(
    "POST",
    f"_apis/wit/workItems/{work_item_id}/comments?api-version=7.1-preview.4",
    json={"text": _html_from_text(text)},
  )

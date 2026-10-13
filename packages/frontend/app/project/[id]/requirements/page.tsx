"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import type { IntegrationSummary, RequirementSummary, ServerToClientMessage } from "@testingmcp/shared";
import { api } from "../../../../lib/api";
import { useProjectContext } from "../../../../lib/ProjectContext";
import { useProjectSocket } from "../../../../lib/useProjectSocket";
import { EmptyState } from "../../../../components/EmptyState";
import { RequirementStatusBadge } from "../../../../components/RequirementStatusBadge";

type Source = "text" | "azureDevOps";

export default function RequirementsPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const { project, environmentId } = useProjectContext();
  const [requirements, setRequirements] = useState<RequirementSummary[] | null>(null);
  const [azureDevOps, setAzureDevOps] = useState<IntegrationSummary | null>(null);
  const [source, setSource] = useState<Source>("text");
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [workItemId, setWorkItemId] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.listRequirements(params.id).then(setRequirements).catch((err) => setError((err as Error).message));
  }, [params.id]);

  useEffect(() => {
    load();
    api.getIntegrations(params.id).then((integrations) => setAzureDevOps(integrations.azureDevOps ?? null));
  }, [load, params.id]);

  useProjectSocket(params.id, (event: ServerToClientMessage) => {
    if (event.type === "requirement.updated") load();
  });

  const canCreate = project?.myRole === "owner" || project?.myRole === "editor";

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const created = await api.createRequirement(
        params.id,
        source === "text"
          ? { title: title.trim() || undefined, text, environmentId: environmentId || undefined }
          : { azureDevOpsWorkItemId: Number(workItemId), environmentId: environmentId || undefined },
      );
      router.push(`/project/${params.id}/requirements/${created.id}`);
    } catch (err) {
      setError((err as Error).message);
      setSubmitting(false);
    }
  }

  return (
    <div>
      <h1 style={{ marginTop: 0 }}>Requirements</h1>
      <p className="muted" style={{ maxWidth: 760 }}>
        Give AegisQA a user story and its agents take it from there: extract the acceptance criteria, reuse
        existing tests that already cover them, generate tests for the gaps, and score the coverage. New test
        cases are saved as drafts -- approve them with the usual review, then run them as normal.
      </p>

      {canCreate && (
        <form className="card" onSubmit={handleSubmit} style={{ maxWidth: 760, display: "grid", gap: 10, marginBottom: 20 }}>
          <div style={{ display: "flex", gap: 8 }}>
            <button type="button" className={`btn ${source === "text" ? "primary" : ""}`} onClick={() => setSource("text")}>
              Paste a requirement
            </button>
            <button
              type="button"
              className={`btn ${source === "azureDevOps" ? "primary" : ""}`}
              onClick={() => setSource("azureDevOps")}
            >
              Import from Azure DevOps
            </button>
          </div>

          {source === "text" ? (
            <>
              <label>
                Title (optional)
                <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Sign in with email" style={{ width: "100%" }} />
              </label>
              <label>
                Requirement
                <textarea
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  rows={7}
                  required
                  placeholder={"As a shopper I want to sign in with my email so that I can check out.\n\nAcceptance criteria:\n- Valid credentials sign me in\n- A wrong password shows an error"}
                  style={{ width: "100%" }}
                />
              </label>
            </>
          ) : azureDevOps ? (
            <label>
              Work item id
              <input
                value={workItemId}
                onChange={(e) => setWorkItemId(e.target.value.replace(/\D/g, ""))}
                placeholder="e.g. 1234"
                required
                style={{ width: 200, display: "block" }}
              />
              <span className="muted" style={{ fontSize: "0.85em" }}>
                Imports the title, description and acceptance criteria from {azureDevOps.baseUrl} ({azureDevOps.projectKey}).
              </span>
            </label>
          ) : (
            <p className="muted" style={{ margin: 0 }}>
              Configure Azure DevOps on the <Link href={`/project/${params.id}/integrations`}>Integrations</Link> page first.
            </p>
          )}

          {error && <p style={{ color: "var(--danger)", margin: 0 }}>{error}</p>}
          <div>
            <button
              className="btn primary"
              type="submit"
              disabled={submitting || (source === "azureDevOps" && (!azureDevOps || !workItemId))}
            >
              {submitting ? "Starting..." : "Start"}
            </button>
          </div>
        </form>
      )}

      {requirements === null ? (
        <p className="muted">Loading...</p>
      ) : requirements.length === 0 ? (
        <EmptyState icon="📋" title="No requirements yet" description="Start one above to run it through the agent pipeline." />
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>Requirement</th>
              <th>Status</th>
              <th>Coverage</th>
              <th>Criteria</th>
              <th>Tests</th>
              <th>Started by</th>
              <th>Updated</th>
            </tr>
          </thead>
          <tbody>
            {requirements.map((req) => (
              <tr key={req.id}>
                <td>
                  <Link href={`/project/${params.id}/requirements/${req.id}`}>{req.title}</Link>
                  {req.sourceType === "azureDevOps" && <span className="muted"> · ADO #{req.sourceRef}</span>}
                </td>
                <td>
                  <RequirementStatusBadge status={req.status} />
                </td>
                <td>{req.coverageScore === null ? "–" : `${req.coverageScore}%`}</td>
                <td>{req.criteriaCount}</td>
                <td>{req.testCaseCount}</td>
                <td className="muted">{req.createdBy?.username ?? "–"}</td>
                <td className="muted">{new Date(req.updatedAt).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

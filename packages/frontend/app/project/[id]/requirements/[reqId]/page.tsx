"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import type { RequirementRecord, RequirementStatus, ServerToClientMessage } from "@testingmcp/shared";
import { api } from "../../../../../lib/api";
import { useProjectContext } from "../../../../../lib/ProjectContext";
import { useProjectSocket } from "../../../../../lib/useProjectSocket";
import { Stepper } from "../../../../../components/Stepper";
import { StatusBadge } from "../../../../../components/StatusBadge";
import { RequirementStatusBadge, RUNNING_REQUIREMENT_STATUSES } from "../../../../../components/RequirementStatusBadge";
import { useToast } from "../../../../../components/ToastProvider";

const STEPS = ["Analyze", "Reuse existing tests", "Generate for gaps", "Coverage"];

const STEP_FOR_STATUS: Record<RequirementStatus, number> = {
  analyzing: 0,
  searching: 1,
  generating: 2,
  scoring: 3,
  completed: 4,
  failed: 0,
};

const STAGE_STEP: Record<string, number> = { analyze: 0, search: 1, generate: 2, score: 3 };

const KIND_LABELS: Record<string, string> = {
  acceptance_criterion: "Acceptance criterion",
  scenario: "Scenario",
  edge_case: "Edge case",
  rule: "Rule",
};

export default function RequirementDetailPage() {
  const params = useParams<{ id: string; reqId: string }>();
  const router = useRouter();
  const { project } = useProjectContext();
  const { showToast } = useToast();
  const [requirement, setRequirement] = useState<RequirementRecord | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.getRequirement(params.reqId).then(setRequirement).catch((err) => setError((err as Error).message));
  }, [params.reqId]);

  useEffect(() => {
    load();
  }, [load]);

  useProjectSocket(params.id, (event: ServerToClientMessage) => {
    if (event.type === "requirement.updated" && event.requirement.id === params.reqId) load();
    if (event.type === "run.completed" && requirement?.testCases.some((t) => t.testCaseId === event.testRun.testCaseId)) load();
  });

  // Fallback while an agent is working, in case a live update is missed.
  useEffect(() => {
    if (!requirement || !RUNNING_REQUIREMENT_STATUSES.includes(requirement.status)) return;
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, [requirement, load]);

  if (error) return <p style={{ color: "var(--danger)" }}>{error}</p>;
  if (!requirement) return <p className="muted">Loading...</p>;

  const canEdit = project?.myRole === "owner" || project?.myRole === "editor";
  const codeById = Object.fromEntries(requirement.criteria.map((c) => [c.id, c.code]));
  const coverageById = Object.fromEntries((requirement.coverage?.criteria ?? []).map((c) => [c.criterionId, c]));
  const testsByCriterion = (criterionId: string) =>
    requirement.testCases.filter((t) => t.criterionIds.includes(criterionId)).length;
  const current =
    requirement.status === "failed" && requirement.failedStage
      ? STAGE_STEP[requirement.failedStage] ?? 0
      : STEP_FOR_STATUS[requirement.status];
  const drafts = requirement.testCases.filter((t) => t.status === "draft").length;

  async function act(action: () => Promise<RequirementRecord>, message: string) {
    try {
      setRequirement(await action());
      showToast(message);
    } catch (err) {
      showToast((err as Error).message);
    }
  }

  async function handleDelete() {
    if (!requirement || !confirm("Delete this requirement? Its test cases stay in the project.")) return;
    await api.deleteRequirement(requirement.id);
    router.push(`/project/${params.id}/requirements`);
  }

  return (
    <div>
      <div style={{ display: "flex", alignItems: "flex-start", gap: 12, marginBottom: 6 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <Link href={`/project/${params.id}/requirements`} className="muted">
            ← Requirements
          </Link>
          <h1 style={{ margin: "6px 0 4px" }}>{requirement.title}</h1>
          <div className="muted" style={{ fontSize: "0.85em" }}>
            Started by {requirement.createdBy?.username ?? "–"} · {new Date(requirement.createdAt).toLocaleString()}
            {requirement.sourceUrl && (
              <>
                {" · "}
                <a href={requirement.sourceUrl} target="_blank" rel="noreferrer">
                  Azure DevOps #{requirement.sourceRef} ↗
                </a>
              </>
            )}
          </div>
        </div>
        <RequirementStatusBadge status={requirement.status} />
        {canEdit && !RUNNING_REQUIREMENT_STATUSES.includes(requirement.status) && (
          <button className="btn danger" onClick={handleDelete}>
            Delete
          </button>
        )}
      </div>

      <div style={{ margin: "16px 0" }}>
        <Stepper steps={STEPS} current={current} />
      </div>

      {requirement.status === "failed" && (
        <div className="card" style={{ borderColor: "var(--danger)", marginBottom: 16 }}>
          <strong style={{ color: "var(--danger)" }}>This step failed.</strong>
          <p style={{ marginBottom: 8 }}>{requirement.error}</p>
          {canEdit && (
            <button className="btn primary" onClick={() => act(() => api.retryRequirement(requirement.id), "Retrying.")}>
              Retry
            </button>
          )}
        </div>
      )}

      {requirement.status === "completed" && (
        <div className="card" style={{ marginBottom: 16 }}>
          <strong>Next steps</strong>
          <p className="muted" style={{ marginBottom: 0 }}>
            {drafts > 0
              ? `${drafts} new test case${drafts === 1 ? " is a draft" : "s are drafts"}: open each one below to review and approve it, then run them from the test case page or with "Run all".`
              : "All linked test cases are approved. Run them from the test case pages or with \"Run all\"."}
          </p>
        </div>
      )}

      {requirement.coverage && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h2 className="section-title" style={{ marginTop: 0 }}>
            Coverage: {requirement.coverage.scorePercent}%
          </h2>
          <p style={{ marginTop: 0 }}>{requirement.coverage.summary}</p>
          {requirement.coverage.risks.length > 0 && (
            <>
              <strong>Risks</strong>
              <ul>
                {requirement.coverage.risks.map((risk, i) => (
                  <li key={i}>
                    <span className={`badge ${risk.level === "high" ? "failed" : risk.level === "medium" ? "waiting" : "pending"}`}>
                      {risk.level}
                    </span>{" "}
                    {risk.description}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}

      <div className="card" style={{ marginBottom: 16 }}>
        <h2 className="section-title" style={{ marginTop: 0 }}>Criteria</h2>
        {requirement.summary && <p style={{ marginTop: 0 }}>{requirement.summary}</p>}
        {requirement.criteria.length === 0 ? (
          <p className="muted">The requirement agent hasn't extracted the criteria yet.</p>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Code</th>
                <th>Type</th>
                <th>Criterion</th>
                <th>Tests</th>
                <th>Coverage</th>
              </tr>
            </thead>
            <tbody>
              {requirement.criteria.map((c) => {
                const verdict = coverageById[c.id];
                return (
                  <tr key={c.id}>
                    <td><strong>{c.code}</strong></td>
                    <td className="muted">{KIND_LABELS[c.kind] ?? c.kind}</td>
                    <td>{c.text}</td>
                    <td>{testsByCriterion(c.id)}</td>
                    <td>
                      {verdict ? (
                        verdict.covered ? (
                          <span className="badge passed">covered</span>
                        ) : (
                          <span className="badge failed" title={verdict.gap ?? undefined}>gap</span>
                        )
                      ) : testsByCriterion(c.id) > 0 ? (
                        <span className="badge pending">linked</span>
                      ) : (
                        <span className="muted">–</span>
                      )}
                      {verdict && !verdict.covered && verdict.gap && (
                        <div className="muted" style={{ fontSize: "0.8em" }}>{verdict.gap}</div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <h2 className="section-title" style={{ marginTop: 0 }}>Test cases</h2>
        {requirement.testCases.length === 0 ? (
          <p className="muted">No test cases linked yet.</p>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Test case</th>
                <th>Source</th>
                <th>Covers</th>
                <th>Status</th>
                <th>Last run</th>
              </tr>
            </thead>
            <tbody>
              {requirement.testCases.map((t) => (
                <tr key={t.testCaseId}>
                  <td>
                    <Link href={`/project/${params.id}/tests/${t.testCaseId}`}>{t.title}</Link>
                    {t.rationale && <div className="muted" style={{ fontSize: "0.8em" }}>{t.rationale}</div>}
                  </td>
                  <td>
                    <span className={`badge ${t.origin === "reused" ? "approved" : "pending"}`}>
                      {t.origin === "reused" ? "reused" : "generated"}
                    </span>
                  </td>
                  <td className="muted">{t.criterionIds.map((id) => codeById[id]).filter(Boolean).join(", ") || "–"}</td>
                  <td>
                    <StatusBadge status={t.status} />
                  </td>
                  <td>
                    {t.latestRun ? (
                      <Link href={`/project/${params.id}/runs/${t.latestRun.id}`}>
                        <StatusBadge status={t.latestRun.status} />
                      </Link>
                    ) : (
                      <span className="muted">–</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <div className="card">
          <h2 className="section-title" style={{ marginTop: 0 }}>Activity</h2>
          <div className="activity-timeline">
            {requirement.events.map((item) => (
              <div className="activity-row" key={item.id}>
                <div style={{ flex: 1 }}>
                  <div>{item.label}</div>
                  {item.detail && <div className="muted" style={{ fontSize: "0.8em" }}>{item.detail}</div>}
                </div>
                <span className="muted" style={{ fontSize: "0.75em", whiteSpace: "nowrap" }}>
                  {new Date(item.createdAt).toLocaleTimeString()}
                </span>
              </div>
            ))}
          </div>
        </div>
        <div className="card">
          <h2 className="section-title" style={{ marginTop: 0 }}>Requirement</h2>
          <pre style={{ whiteSpace: "pre-wrap", fontFamily: "inherit", margin: 0, maxHeight: 360, overflowY: "auto" }}>
            {requirement.sourceText}
          </pre>
        </div>
      </div>
    </div>
  );
}

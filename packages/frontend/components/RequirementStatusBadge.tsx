import type { RequirementStatus } from "@testingmcp/shared";

const LABELS: Record<RequirementStatus, string> = {
  analyzing: "Analyzing",
  searching: "Finding existing tests",
  generating: "Generating tests",
  scoring: "Scoring coverage",
  completed: "Completed",
  failed: "Failed",
};

// Reuses the existing badge colours: running (an agent is working), passed, failed.
const STYLE: Record<RequirementStatus, string> = {
  analyzing: "running",
  searching: "running",
  generating: "running",
  scoring: "running",
  completed: "passed",
  failed: "failed",
};

export const RUNNING_REQUIREMENT_STATUSES: RequirementStatus[] = ["analyzing", "searching", "generating", "scoring"];

export function RequirementStatusBadge({ status }: { status: RequirementStatus }) {
  return <span className={`badge ${STYLE[status]}`}>{LABELS[status]}</span>;
}

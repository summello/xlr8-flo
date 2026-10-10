import type { components } from "../../api/generated/schema";
export type Project = components["schemas"]["ProjectRead"];
export type Transition = components["schemas"]["TransitionAvailable"];
export type Phase = components["schemas"]["PhaseRead"];
export type Milestone = components["schemas"]["MilestoneRead"];
export type Risk = components["schemas"]["RiskRead"];
export type Balance = components["schemas"]["BalanceQueryRead"];
export type ProjectStatus = Project["status"];
export const statuses: ProjectStatus[] = ["draft", "approval_pending", "active", "deferred", "completed", "abandoned"];
export function label(value: string) { return value.replaceAll("_", " ").replace(/^./, c => c.toUpperCase()); }
export function failure(error: { detail?: string | null } | undefined, action: string) {
  return error?.detail ? `${error.detail} Your entries and loaded data are preserved. Correct the issue or retry.` : `${action} failed. Your entries and loaded data were preserved. Check your connection and try again.`;
}

export class WriteFailure extends Error {
  constructor(public problem: components["schemas"]["ProblemDetails"] | undefined, action: string) {
    super(failure(problem, action));
  }
}

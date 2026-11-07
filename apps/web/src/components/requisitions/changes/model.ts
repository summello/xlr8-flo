import type { components } from "../../../api/generated/schema";
import { formatMoney } from "../../../lib/money";
import { problemFromError, type SummaryProblem } from "../model";

export type ChangeSet = components["schemas"]["ChangeSet"];
export type ChangeRow = components["schemas"]["ChangeRow"];
export type ChangeFunding = components["schemas"]["ChangeFunding"];
export type Revision = components["schemas"]["RevisionSummary"];
type ProblemBody = { detail?: string | null; checks?: Record<string, string> | null; errors?: { field: string; message: string }[] | null };

/** One label map for the server's funding outcome (E09-S12 amendment 1). A word outside it renders raw and fails the vitest. */
export const OUTCOME_LABEL: Readonly<Record<ChangeFunding["outcome"], string>> = {
  unchanged: "Unchanged", released_once: "Released Once", reserved_more: "Reserved More", moved: "Moved",
};
export const outcomeLabel = (outcome: string) => (OUTCOME_LABEL as Record<string, string | undefined>)[outcome] ?? outcome;

export const CHANGE_LABEL: Readonly<Record<ChangeRow["change"], string>> = { changed: "Changed", added: "Added", removed: "Removed" };

/** The server's delta, formatted from its own strings. Nothing is subtracted or divided here. */
export function deltaText(row: ChangeRow, currency: string): string | null {
  if (!row.delta) return null;
  const { direction, amount, percent } = row.delta;
  return `${direction === "up" ? "Up" : "Down"} ${formatMoney(amount, currency)} ${currency}${percent ? ` (${percent}%)` : ""}`;
}

/** What resubmitting does, read from the server's `material` and `routing` and never decided here (D-M2-13). */
export function consequence(set: ChangeSet): { heading: string; text: string } {
  if (set.routing === "restarts") return { heading: "Material Change", text: "Material change: approval restarts from the first step; earlier decisions no longer apply; the reservation is adjusted." };
  if (set.routing === "same_step") return { heading: "Not a Material Change", text: "Not a material change: it returns to the step that sent it back, and the approver sees this diff." };
  return set.material
    ? { heading: "Material Change", text: "Material change: it is sent for approval. The approved version stays in force until it is approved." }
    : { heading: "Not a Material Change", text: "Not a material change: it is applied as soon as you submit." };
}

export type Outcome =
  | { kind: "nothing_changed" | "stale_version"; message: string }
  | { kind: "submission_invalid"; message: string; problems: SummaryProblem[] }
  | { kind: "other"; message: string };

/** Sorts a failed submit into the packet's four alerts by the server's `checks.problem`. */
export function classify(error: ProblemBody | undefined, verb: "resubmitted" | "submitted"): Outcome {
  const problem = error?.checks?.problem;
  if (problem === "nothing_changed") return { kind: "nothing_changed", message: `Not ${verb}. Nothing has changed.` };
  if (problem === "stale_version") return { kind: "stale_version", message: "This requisition changed since you opened it. Reload to see the current version." };
  if (problem === "submission_invalid" || (error?.errors?.length ?? 0) > 0) {
    return { kind: "submission_invalid", message: `Not ${verb}. Fix these first. Everything you entered is kept.`, problems: (error?.errors ?? []).map(problemFromError) };
  }
  return { kind: "other", message: "" };
}

/** The version a lineage list reaches: the highest revision number, or the fallback before it loads. */
export const latestRevision = (revisions: readonly Revision[] | null | undefined, fallback: number) =>
  revisions?.length ? Math.max(...revisions.map(r => r.revision_no)) : fallback;

/** The applied version an amendment was opened on: the newest applied revision before it. */
export const baseRevision = (revisions: readonly Revision[], amendmentNo: number): number | undefined => {
  const applied = revisions.filter(r => r.status === "applied" && r.revision_no < amendmentNo).map(r => r.revision_no);
  return applied.length ? Math.max(...applied) : undefined;
};

/** The revision's actor: the requester as a person, anyone else by the id the server returned. */
export const actorOf = (id: string, requester: { id: string; email: string; name: string | null }) => id === requester.id ? { name: requester.name, email: requester.email } : id;

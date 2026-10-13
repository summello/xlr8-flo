import type { components } from "../../api/generated/schema";
import type { StatusTone } from "../status/tones";

export type InboxRow = components["schemas"]["InboxRow"];
export type InboxPage = components["schemas"]["InboxPage"];

export const TABS = ["pending", "overdue", "completed", "delegated"] as const;
export type Tab = (typeof TABS)[number];

export const TAB_LABELS: Readonly<Record<Tab, string>> = {
  pending: "Pending",
  overdue: "Overdue",
  completed: "Completed",
  delegated: "Delegated",
};

export const EMPTY_COPY: Readonly<Record<Tab, { title: string; body: string }>> = {
  pending: { title: "All Caught Up", body: "Nothing is waiting for your decision." },
  overdue: { title: "Nothing Overdue", body: "Every approval waiting on you is within its due date." },
  completed: { title: "No Decisions Yet", body: "Approvals you decide will be listed here." },
  delegated: { title: "Nothing Delegated", body: "Approvals you cover for, or pass on, will be listed here." },
};

const DAY = 86_400_000;

export function parseTab(search: string): Tab {
  const value = new URLSearchParams(search).get("tab");
  return TABS.find((tab) => tab === value) ?? "pending";
}

/** Whole days past due, in words, or null when the row is not overdue. */
export function overdueLabel(dueAt: string | null, now: number): string | null {
  if (dueAt === null) return null;
  const late = now - Date.parse(dueAt);
  if (!(late > 0)) return null;
  const days = Math.max(1, Math.floor(late / DAY));
  return `${days} ${days === 1 ? "day" : "days"} overdue`;
}

export function stepLabel(row: Pick<InboxRow, "step">): string {
  return `${row.step.index} of ${row.step.of}`;
}

/** The status word is the server's; this only chooses its tone (icon and words always show). */
export function statusTone(word: string): StatusTone {
  if (word === "Overdue" || word === "Rejected") return "danger";
  if (word === "Approved") return "success";
  if (word === "Returned for Changes") return "warning";
  if (word.startsWith("With ")) return "neutral";
  return "info";
}

export function nextRow(rows: readonly InboxRow[], taskId: string): InboxRow | undefined {
  return rows.find((row) => row.task_id !== taskId);
}

/** Counts are the server's; the subline only words them. */
export function subline(counts: InboxPage["counts"], totals: InboxPage["total_amount"]): string {
  const currencies = totals.map((t) => t.currency).join(", ");
  const waiting = `${counts.pending} waiting on you, ${counts.overdue} overdue`;
  return currencies === "" ? waiting : `${waiting} · amounts in ${currencies}`;
}

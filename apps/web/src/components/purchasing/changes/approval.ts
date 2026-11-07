export type DiffEntry = { line_no: number | null; field: string; label: string; was: string | null; now: string | null };

const AMOUNT = ["quantity", "unit_price", "tax", "freight", "document_freight"];

/** The reapproval sentence. Whether approval is needed is the server's `material`; the fields only name why. */
export function approvalLine({ material, diff }: { material: boolean; diff: readonly { field: string }[] }): string {
  if (diff.length === 0) return "No changes yet.";
  const has = (...fields: string[]) => diff.some(d => fields.includes(d.field));
  if (material) {
    const why = [has(...AMOUNT) && "the amount changes", has("line") && "the line count changes"].filter(Boolean);
    return why.length ? `Needs approval: ${why.join(" and ")}` : "Needs approval.";
  }
  const parts = [has("needed_by") && "the needed-by date", has("terms") && "the terms"].filter(Boolean);
  const verb = parts.length === 1 && parts[0] === "the needed-by date" ? "changes" : "change";
  return parts.length ? `No approval needed: only ${parts.join(" and ")} ${verb}` : "No approval needed.";
}

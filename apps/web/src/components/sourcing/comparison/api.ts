import type { components } from "../../../api/generated/schema";

export type Comparison = components["schemas"]["ComparisonRead"];
export type AnyComparison = Comparison | components["schemas"]["SealedComparisonRead"];
export type Vendor = Comparison["vendors"][number];
export type Basis = Comparison["lowest_cost_supplier"];
export type Evaluation = components["schemas"]["EvaluationRead"];
export type BasisName = "lowest_cost_supplier" | "best_average_cost";

export const BASES: readonly (readonly [BasisName, string, string])[] = [
  ["lowest_cost_supplier", "Lowest Cost Supplier", "Each line goes to the supplier with the lowest landed cost for that line. The RFQ may be awarded to several vendors."],
  ["best_average_cost", "Best Average Cost", "One vendor takes the whole RFQ: the lowest total landed cost among vendors that quoted every line."],
];

/** Sealed until the deadline (SRC-007): the server sends no figure, so the page has none to show. */
export const isSealed = (c: AnyComparison): c is Exclude<AnyComparison, Comparison> => c.sealed === true;

/** The marks a vendor holds, in words; a tie reads `Tied` (decision 114). */
export function marksFor(c: Comparison, vendorId: string): { lowest?: string; fastest?: string; score?: string } {
  const mark = (name: string, m: { vendor_id: string; tied?: boolean } | null) => m?.vendor_id === vendorId ? (m.tied ? `${name}, Tied` : name) : undefined;
  return { lowest: mark("Lowest", c.markers.lowest), fastest: mark("Fastest", c.markers.fastest), score: mark("Highest Score", c.markers.highest_score) };
}

/** The server omits a defaulted list when empty. */
export const vendorsOf = (b: Basis) => b.vendors ?? [];

/** Line numbers for a list of line ids, as `2, 3`. */
export const lineNumbers = (c: Comparison, ids: readonly string[]) => c.lines.filter(l => ids.includes(l.rfq_line_id)).map(l => l.line_no).join(", ");

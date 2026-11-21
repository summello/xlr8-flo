import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import RequisitionForm from "./RequisitionForm";

/** MASTER 7.11 lint gate: a tag whose control is not required, or a required control without a tag, fails. */
export function requiredMarkViolations(markup: string): string[] {
  const problems: string[] = [];
  const labels = [...markup.matchAll(/<label[^>]*for="([^"]+)"[^>]*>(.*?)<\/label>/g)];
  const tagged = new Map(labels.map(m => [m[1]!, m[2]!.includes("req-tag")]));
  for (const [id, hasTag] of tagged) {
    const control = markup.match(new RegExp(`<(?:input|select|textarea)[^>]*\\bid="${id}"[^>]*>`))?.[0];
    if (!control) continue;
    const required = /\brequired=""|\brequired\b/.test(control.replace(/aria-required/g, ""));
    if (hasTag && !required) problems.push(`${id}: tagged Required but the control is not required`);
    if (required && !hasTag) problems.push(`${id}: required control without a Required tag`);
  }
  for (const m of markup.matchAll(/<(?:input|select|textarea)[^>]*\brequired=""[^>]*\bid="([^"]+)"|<(?:input|select|textarea)[^>]*\bid="([^"]+)"[^>]*\brequired=""/g)) {
    const id = m[1] ?? m[2]!;
    if (!tagged.has(id)) problems.push(`${id}: required control with no label`);
  }
  return problems;
}

const markup = renderToStaticMarkup(<RequisitionForm navigate={() => undefined} />);
// A saved draft with one hand-typed line and one catalogue line: the line controls must be covered too.
const row = { id: "r", number: "REQ-1", title: "T", status: "draft", standalone: false, project_id: "p", project_number: null, project_name: null, bu_id: "b", bu_name: "B", requester_id: "u", requester_email: "e@x", requester_name: null, justification: null, needed_by: null, department_code: null, ledger_account_code: null, delivery_address_id: null, estimate: "0.0000", currency: "USD", version: 1, submitted_at: null, decided_at: null, created_at: "", updated_at: "", reservation: null, approval: null, amendment: null } as const;
const line = (n: number, catalogue: string | null) => ({ id: `l${n}`, line_no: n, line_type: "item", description: "d", quantity: "1.0000", uom_code: "ea", unit_price: "1.0000", amount: "1.0000", currency: "USD", category_code: null, catalogue_item_id: catalogue, requested_qty: "1", sourced_qty: "0", awarded_qty: "0", ordered_qty: "0", cancelled_qty: "0", remaining_qty: "1" }) as const;
const withLines = renderToStaticMarkup(<RequisitionForm navigate={() => undefined} initial={{ row: { ...row }, lines: [line(1, null), line(2, "c")] }} />);

describe("required-field markup on the requisition form", () => {
  it("covers every required header control", () => {
    for (const id of ["req-title", "req-project_id", "req-department_code", "req-ledger_account_code", "req-needed_by", "req-delivery_address_id", "req-justification"]) {
      expect(markup, id).toMatch(new RegExp(`<label[^>]*for="${id}"[^>]*>[^<]*<span class="req-tag"`));
      expect(markup, id).toMatch(new RegExp(`<(?:input|select|textarea)[^>]*id="${id}"[^>]*required=""|<(?:input|select|textarea)[^>]*required=""[^>]*id="${id}"`));
    }
  });
  it("has no mismatched tag or control", () => {
    expect(requiredMarkViolations(markup)).toEqual([]);
    expect(requiredMarkViolations(withLines)).toEqual([]);
  });
  it("covers the editable line controls with required, a Required tag and persistent helper text", () => {
    for (const id of ["line-1-description", "line-1-quantity", "line-1-uom_code", "line-1-unit_price"]) {
      expect(withLines, id).toMatch(new RegExp(`<label[^>]*for="${id}"[^>]*>[^<]*<span class="req-tag"`));
      expect(withLines, id).toMatch(new RegExp(`<(?:input|select)[^>]*id="${id}"[^>]*required=""|<(?:input|select)[^>]*required=""[^>]*id="${id}"`));
      expect(withLines, id).toMatch(new RegExp(`id="${id}-help"`));
    }
    // A catalogue line takes its description and unit from the item: no editable control to cover.
    expect(withLines).not.toContain('id="line-2-description"');
  });
  it("catches a line control that lost its required attribute", () => {
    const broken = withLines.replace(/(<input[^>]*id="line-1-quantity"[^>]*?) required=""/, "$1");
    expect(requiredMarkViolations(broken)).toEqual(["line-1-quantity: tagged Required but the control is not required"]);
  });
  it("catches a tag on a control that is not required, and a required control without a tag", () => {
    expect(requiredMarkViolations(markup.replace(/(<input[^>]*id="req-title"[^>]*?) required=""/, "$1"))).toEqual(["req-title: tagged Required but the control is not required"]);
    expect(requiredMarkViolations(markup.replace(/<span class="req-tag".*?<\/span><\/span><\/label><input([^>]*id="req-title")/, "</label><input$1"))).toEqual(["req-title: required control without a Required tag"]);
  });
});

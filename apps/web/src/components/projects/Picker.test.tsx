import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { PickerMatches } from "./Picker";

const render = (state: Parameters<typeof PickerMatches>[0]["state"], rows: { value: string; label: string }[] = []) =>
  renderToStaticMarkup(<PickerMatches state={state} rows={rows} noun="sponsors" blocked="Choose a business unit first." onAdd={() => undefined} />);

describe("PickerMatches (UX-004)", () => {
  it("says Searching… and never No matches. while a lookup is pending", () => {
    const html = render("searching");
    expect(html).toContain('role="status"');
    expect(html).toContain("Searching…");
    expect(html).not.toContain("No matches.");
  });
  it("says No matches. only after a settled empty result", () => {
    const html = render("settled", []);
    expect(html).toContain("No matches.");
    expect(html).not.toContain("Searching…");
  });
  it("lists the matches once settled", () => {
    expect(render("settled", [{ value: "1", label: "ada@example.test" }])).toContain("Add ada@example.test");
  });
  it("says why when the lookup cannot run, instead of No matches.", () => {
    const html = render("blocked");
    expect(html).toContain("Choose a business unit first.");
    expect(html).not.toContain("No matches.");
  });
});

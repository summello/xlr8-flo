import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import Money from "./Money";

const html = (value: string | null, zero?: "dash" | "value") => renderToStaticMarkup(<Money value={value} currency="USD" zero={zero} />);

describe("Money zero prop", () => {
  it("shows a dash for zero by default", () => {
    expect(html("0.0000")).toContain("<span>—</span>");
  });
  it("shows the amount for zero when zero is value", () => {
    const out = html("0.0000", "value");
    expect(out).not.toContain("<span>—</span>");
    expect(out).toContain("0.00");
  });
  it("treats negative zero as zero, not a negative", () => {
    expect(html("-0.00", "value")).not.toContain("data-negative");
  });
  it("marks real negatives", () => {
    expect(html("-5.00", "value")).toContain("data-negative");
  });
});

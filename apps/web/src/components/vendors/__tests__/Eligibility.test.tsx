import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import Eligibility from "../Eligibility";

type Sample = {
  eligible: boolean;
  reasons: { code: string; text: string; document_type?: string }[];
  warnings: { code: string; text: string }[];
};
const bad: Sample = {
  eligible: false,
  reasons: [
    {
      code: "document_expired",
      text: "Insurance Certificate expired 2 Oct",
      document_type: "INS",
    },
    {
      code: "document_missing",
      text: "Tax Certificate missing",
      document_type: "TAX",
    },
  ],
  warnings: [],
};
const good: Sample = { eligible: true, reasons: [], warnings: [] };

const render = (
  e: Sample,
  variant: "cell" | "header" | "picker" | "field",
  unitName?: string,
) =>
  renderToStaticMarkup(
    <Eligibility eligibility={e} variant={variant} unitName={unitName} />,
  );

const text = (html: string) =>
  html
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
const iconOf = (html: string) => /<svg[^>]*>([\s\S]*?)<\/svg>/.exec(html)?.[1];

describe("Eligibility", () => {
  it("cell: words, first reason, and N more", () => {
    expect(text(render(bad, "cell"))).toBe(
      "Not Eligible Insurance Certificate expired 2 Oct and 1 more",
    );
    expect(text(render(good, "cell"))).toBe("Eligible Here");
  });

  it("picker and field read like the cell", () => {
    expect(render(bad, "picker")).toBe(render(bad, "cell"));
    expect(render(good, "field")).toBe(render(good, "cell"));
  });

  it("header: names the unit and lists every reason", () => {
    const html = render(bad, "header", "Plant 4");
    expect(html).toContain("elig");
    expect(text(html)).toBe(
      "Not Eligible In Plant 4 Insurance Certificate expired 2 Oct Tax Certificate missing",
    );
    expect(text(render(good, "header", "Plant 4"))).toBe("Eligible In Plant 4");
  });

  it("warnings show on an eligible vendor without changing the words", () => {
    const html = render(
      {
        eligible: true,
        reasons: [],
        warnings: [
          { code: "document_expiring", text: "INS expiring in 12 Days" },
        ],
      },
      "cell",
    );
    expect(text(html)).toBe("Eligible Here INS expiring in 12 Days");
    expect(html).toContain("is-warn");
  });

  it("renders the server's eligible flag, never reasons.length", () => {
    expect(
      text(render({ eligible: false, reasons: [], warnings: [] }, "cell")),
    ).toBe("Not Eligible");
    expect(
      text(
        render({ eligible: true, reasons: bad.reasons, warnings: [] }, "cell"),
      ),
    ).toContain("Eligible Here");
  });

  it("greyscale: the two states differ by words and by icon with every colour stripped", () => {
    const a = render(good, "cell")
      .replace(/ class="[^"]*"/g, "")
      .replace(/ style="[^"]*"/g, "");
    const b = render(bad, "cell")
      .replace(/ class="[^"]*"/g, "")
      .replace(/ style="[^"]*"/g, "");
    expect(text(a)).not.toBe(text(b));
    expect(iconOf(a)).toBeTruthy();
    expect(iconOf(b)).toBeTruthy();
    expect(iconOf(a)).not.toBe(iconOf(b));
    expect(a).toContain('aria-hidden="true"');
  });

  it("carries no colour literal", () => {
    for (const f of [
      "Eligibility.tsx",
      "../ui/Signal.tsx",
      "../ui/signal.css",
    ]) {
      const src = readFileSync(new URL(`../${f}`, import.meta.url), "utf8");
      expect(src).not.toMatch(/#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(|oklch\(/);
    }
  });
});
